package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

const (
	outgoingQueueSize = 64
	pingInterval      = 25 * time.Second
	pingTimeout       = 10 * time.Second
	messageOverhead   = 16 * 1024
)

//go:embed index.html
var index []byte

var pasteIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

type message = map[string]any

type server struct {
	store *pasteStore
}

func newServer(maxBytes int) *server {
	return &server{store: newStore(maxBytes)}
}

func (s *server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "unsupported method", http.StatusNotImplemented)
		return
	}

	switch r.URL.Path {
	case "/", "/index.html":
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Content-Length", strconv.Itoa(len(index)))
		_, _ = w.Write(index)
	case "/ws":
		id := parsePasteID(r.URL.Query())
		if !pasteIDPattern.MatchString(id) {
			http.Error(w, "invalid paste id", http.StatusBadRequest)
			return
		}
		s.serveWebSocket(w, r, id)
	default:
		http.NotFound(w, r)
	}
}

func parsePasteID(query url.Values) string {
	id := strings.TrimSpace(query.Get("id"))
	if id == "" {
		return "default"
	}
	return id
}

type client struct {
	conn     *websocket.Conn
	outgoing chan any
	cancel   context.CancelFunc
}

func (c *client) enqueue(response any) {
	select {
	case c.outgoing <- response:
	default:
		// A slow client reconnects and fetches a fresh snapshot instead of
		// accumulating an unbounded queue or blocking other clients.
		c.cancel()
	}
}

func (c *client) sendError(requestID json.RawMessage, reason string) {
	c.enqueue(message{
		"type":      "error",
		"requestId": requestID,
		"message":   reason,
	})
}

func (s *server) serveWebSocket(w http.ResponseWriter, r *http.Request, id string) {
	// Preserve the original server's acceptance of cross-origin clients.
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{InsecureSkipVerify: true})
	if err != nil {
		return
	}
	defer conn.CloseNow()
	// JSON can escape one content byte as six bytes, e.g. "\u0000".
	conn.SetReadLimit(int64(s.store.maxBytes)*6 + messageOverhead)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	subscriber := &client{
		conn:     conn,
		outgoing: make(chan any, outgoingQueueSize),
		cancel:   cancel,
	}
	s.store.subscribe(id, subscriber)
	defer s.store.unsubscribe(id, subscriber)

	go subscriber.writeMessages(ctx)
	s.readMessages(ctx, subscriber, id)
}

func (s *server) readMessages(ctx context.Context, subscriber *client, id string) {
	for {
		kind, payload, err := subscriber.conn.Read(ctx)
		if err != nil {
			return
		}
		if kind != websocket.MessageText {
			_ = subscriber.conn.Close(websocket.StatusUnsupportedData, "binary messages are not supported")
			return
		}
		s.handleRequest(id, payload, subscriber)
	}
}

func (c *client) writeMessages(ctx context.Context) {
	defer c.cancel()
	ticker := time.NewTicker(pingInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case response := <-c.outgoing:
			if err := wsjson.Write(ctx, c.conn, response); err != nil {
				return
			}
		case <-ticker.C:
			pingCtx, cancel := context.WithTimeout(ctx, pingTimeout)
			err := c.conn.Ping(pingCtx)
			cancel()
			if err != nil {
				return
			}
		}
	}
}

func (s *server) handleRequest(id string, payload []byte, sender *client) {
	// Raw fields preserve arbitrary request IDs and allow validation per action
	// (for example, a get request ignores the content field).
	var request map[string]json.RawMessage
	if !utf8.Valid(payload) || json.Unmarshal(payload, &request) != nil || request == nil {
		sender.sendError(nil, "message must be a JSON object with valid UTF-8")
		return
	}
	requestID := request["requestId"]
	var action string
	if err := json.Unmarshal(request["type"], &action); err != nil {
		sender.sendError(requestID, "unsupported message type")
		return
	}

	switch action {
	case "get":
		s.store.sendSnapshot(id, sender, requestID)
	case "put":
		// A pointer distinguishes an empty string from missing or null content.
		var content *string
		if err := json.Unmarshal(request["content"], &content); err != nil || content == nil {
			sender.sendError(requestID, "put.content must be a string")
			return
		}
		if len(*content) > s.store.maxBytes {
			sender.sendError(requestID, fmt.Sprintf("content exceeds %d bytes", s.store.maxBytes))
			return
		}
		s.store.writeAndBroadcast(id, *content, sender, requestID)
	default:
		sender.sendError(requestID, "unsupported message type")
	}
}
