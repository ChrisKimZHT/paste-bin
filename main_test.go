package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

func connect(t *testing.T, server *httptest.Server, query string) (*websocket.Conn, context.Context) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	conn, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(server.URL, "http")+"/ws"+query, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.CloseNow() })
	return conn, ctx
}

func receive(t *testing.T, ctx context.Context, conn *websocket.Conn, kind string) message {
	t.Helper()
	var m message
	if err := wsjson.Read(ctx, conn, &m); err != nil {
		t.Fatal(err)
	}
	if m["type"] != kind {
		t.Fatalf("expected %s, got %v", kind, m)
	}
	return m
}

func send(t *testing.T, ctx context.Context, conn *websocket.Conn, m any) {
	t.Helper()
	if err := wsjson.Write(ctx, conn, m); err != nil {
		t.Fatal(err)
	}
}

func TestPasteProtocol(t *testing.T) {
	server := httptest.NewServer(newServer(6))
	defer server.Close()
	a, ctx := connect(t, server, "?id=shared")
	b, _ := connect(t, server, "?id=shared")
	other, otherCtx := connect(t, server, "?id=other")
	// Synchronize subscription before broadcasting.
	for _, c := range []*websocket.Conn{a, b, other} {
		send(t, ctx, c, message{"type": "get", "requestId": "initial"})
		m := receive(t, ctx, c, "paste")
		if m["revision"] != float64(0) || m["content"] != "" || m["maxBytes"] != float64(6) || m["requestId"] != "initial" {
			t.Fatalf("bad initial snapshot: %v", m)
		}
		if _, err := time.Parse(http.TimeFormat, m["lastModified"].(string)); err != nil {
			t.Fatal(err)
		}
	}
	for i, content := range []string{"你好", ""} {
		send(t, ctx, a, message{"type": "put", "content": content, "requestId": i})
		for _, c := range []*websocket.Conn{a, b} {
			m := receive(t, ctx, c, "paste")
			if m["content"] != content || m["length"] != float64(len(content)) || m["revision"] != float64(i+1) {
				t.Fatalf("bad broadcast: %v", m)
			}
		}
		ack := receive(t, ctx, a, "ack")
		if ack["requestId"] != float64(i) || ack["revision"] != float64(i+1) || ack["action"] != "put" {
			t.Fatalf("bad ack: %v", ack)
		}
	}
	send(t, otherCtx, other, message{"type": "get"})
	if m := receive(t, otherCtx, other, "paste"); m["revision"] != float64(0) {
		t.Fatalf("paste isolation failed: %v", m)
	}
}

func TestPing(t *testing.T) {
	server := httptest.NewServer(newServer(6))
	defer server.Close()
	c, ctx := connect(t, server, "")
	go func() { _, _, _ = c.Read(ctx) }()
	if err := c.Ping(ctx); err != nil {
		t.Fatal(err)
	}
}

func TestResponseFields(t *testing.T) {
	server := httptest.NewServer(newServer(128))
	defer server.Close()
	c, ctx := connect(t, server, "")

	// Request IDs are opaque JSON values, including integers beyond float64 precision.
	requestID := json.RawMessage(`{"sequence":9007199254740993}`)
	send(t, ctx, c, message{"type": "put", "content": "value", "requestId": requestID})
	broadcast := receive(t, ctx, c, "paste")
	if _, exists := broadcast["requestId"]; exists {
		t.Fatal("broadcast must omit requestId")
	}
	var ack map[string]json.RawMessage
	if err := wsjson.Read(ctx, c, &ack); err != nil {
		t.Fatal(err)
	}
	if string(ack["requestId"]) != string(requestID) {
		t.Fatalf("request ID changed: %s", ack["requestId"])
	}

	send(t, ctx, c, message{"type": "get"})
	snapshot := receive(t, ctx, c, "paste")
	if id, exists := snapshot["requestId"]; !exists || id != nil {
		t.Fatal("snapshot without a request ID must include requestId: null")
	}
}

func TestErrorsAndReconnect(t *testing.T) {
	server := httptest.NewServer(newServer(6))
	defer server.Close()
	c, ctx := connect(t, server, "")
	for _, raw := range []string{`{`, `null`, `[]`, `{"type":"put","content":null}`, `{"type":"put","content":42}`, `{"type":"put"}`, `{"type":"wat"}`, `{"type":"put","content":"你好!"}`} {
		if err := c.Write(ctx, websocket.MessageText, []byte(raw)); err != nil {
			t.Fatal(err)
		}
		receive(t, ctx, c, "error")
	}
	send(t, ctx, c, message{"type": "put", "content": "saved", "requestId": "save"})
	receive(t, ctx, c, "paste")
	receive(t, ctx, c, "ack")
	c.CloseNow()
	reconnected, nextCtx := connect(t, server, "?id=default")
	send(t, nextCtx, reconnected, message{"type": "get", "requestId": "again"})
	m := receive(t, nextCtx, reconnected, "paste")
	if m["content"] != "saved" || m["revision"] != float64(1) || m["requestId"] != "again" {
		t.Fatalf("reconnect failed: %v", m)
	}
}

func TestHTTPAndIDs(t *testing.T) {
	s := newServer(131072)
	for path, status := range map[string]int{"/": 200, "/index.html": 200, "/api": 404, "/missing": 404, "/ws?id=bad!": 400, "/ws?id=" + strings.Repeat("a", 129): 400} {
		w := httptest.NewRecorder()
		s.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if w.Code != status {
			t.Fatalf("%s: got %d, want %d", path, w.Code, status)
		}
		if status == 200 && w.Body.String() != string(index) {
			t.Fatal("embedded page differs")
		}
	}
	server := httptest.NewServer(s)
	defer server.Close()
	for _, query := range []string{"", "?id=", "?id=%20", "?id=default", "?id=&id=default"} {
		c, ctx := connect(t, server, query)
		send(t, ctx, c, message{"type": "get"})
		if m := receive(t, ctx, c, "paste"); m["id"] != "default" {
			t.Fatalf("%s: %v", query, m)
		}
	}
}

func TestFragmentationAndBinary(t *testing.T) {
	server := httptest.NewServer(newServer(131072))
	defer server.Close()
	c, ctx := connect(t, server, "")
	content := strings.Repeat("界", 10000)
	payload, _ := json.Marshal(message{"type": "put", "content": content})
	w, err := c.Writer(ctx, websocket.MessageText)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = io.Copy(w, strings.NewReader(string(payload))); err != nil {
		t.Fatal(err)
	}
	if err = w.Close(); err != nil {
		t.Fatal(err)
	}
	if m := receive(t, ctx, c, "paste"); m["content"] != content {
		t.Fatal("fragmented content changed")
	}
	receive(t, ctx, c, "ack")
	if err = c.Write(ctx, websocket.MessageBinary, []byte("binary")); err != nil {
		t.Fatal(err)
	}
	_, _, err = c.Read(ctx)
	if websocket.CloseStatus(err) != websocket.StatusUnsupportedData {
		t.Fatalf("unexpected binary close: %v", err)
	}
}

func TestConcurrentWrites(t *testing.T) {
	server := httptest.NewServer(newServer(128))
	defer server.Close()
	reader, ctx := connect(t, server, "?id=shared")
	send(t, ctx, reader, message{"type": "get"})
	receive(t, ctx, reader, "paste")
	const count = 12
	clients := make([]*websocket.Conn, count)
	for i := range clients {
		clients[i], _ = connect(t, server, "?id=shared")
	}
	var writers sync.WaitGroup
	errors := make(chan error, count)
	for _, c := range clients {
		writers.Add(1)
		go func(c *websocket.Conn) {
			defer writers.Done()
			errors <- wsjson.Write(ctx, c, message{"type": "put", "content": "value"})
		}(c)
	}
	writers.Wait()
	for range clients {
		if err := <-errors; err != nil {
			t.Fatal(err)
		}
	}
	for revision := 1; revision <= count; revision++ {
		if m := receive(t, ctx, reader, "paste"); m["revision"] != float64(revision) {
			t.Fatalf("out-of-order broadcast: %v", m)
		}
	}
}
