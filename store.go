package main

import (
	"container/list"
	"encoding/json"
	"net/http"
	"sync"
	"time"
)

type paste struct {
	content      string
	revision     int
	lastModified string
}

type pasteMessage struct {
	Type         string `json:"type"`
	ID           string `json:"id"`
	Content      string `json:"content"`
	Revision     int    `json:"revision"`
	Length       int    `json:"length"`
	LastModified string `json:"lastModified"`
	MaxBytes     int    `json:"maxBytes"`
}

type pasteStore struct {
	mu            sync.Mutex
	entries       map[string]paste
	creationOrder list.List
	subscribers   map[string]map[*client]struct{}
	started       string
	maxBytes      int
	maxEntries    int
}

func newStore(maxBytes, maxEntries int) *pasteStore {
	return &pasteStore{
		entries:     make(map[string]paste),
		subscribers: make(map[string]map[*client]struct{}),
		started:     time.Now().UTC().Format(http.TimeFormat),
		maxBytes:    maxBytes,
		maxEntries:  maxEntries,
	}
}

func (s *pasteStore) subscribe(id string, subscriber *client) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.subscribers[id] == nil {
		s.subscribers[id] = make(map[*client]struct{})
	}
	s.subscribers[id][subscriber] = struct{}{}
}

func (s *pasteStore) unsubscribe(id string, subscriber *client) {
	s.mu.Lock()
	defer s.mu.Unlock()

	delete(s.subscribers[id], subscriber)
	if len(s.subscribers[id]) == 0 {
		delete(s.subscribers, id)
	}
}

func (s *pasteStore) message(id string, entry paste) pasteMessage {
	return pasteMessage{
		Type:         "paste",
		ID:           id,
		Content:      entry.content,
		Revision:     entry.revision,
		Length:       len(entry.content), // Go strings count bytes, matching the UTF-8 limit.
		LastModified: entry.lastModified,
		MaxBytes:     s.maxBytes,
	}
}

func (s *pasteStore) sendSnapshot(id string, recipient *client, requestID json.RawMessage) {
	s.mu.Lock()
	defer s.mu.Unlock()

	entry, exists := s.entries[id]
	if !exists {
		entry.lastModified = s.started
	}
	// Only requested snapshots include requestId; broadcasts omit it.
	response := struct {
		pasteMessage
		RequestID json.RawMessage `json:"requestId"`
	}{
		pasteMessage: s.message(id, entry),
		RequestID:    requestID,
	}
	recipient.enqueue(response)
}

func (s *pasteStore) writeAndBroadcast(id, content string, writer *client, requestID json.RawMessage) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, exists := s.entries[id]; !exists {
		// FIFO by creation order; reads and updates do not refresh an entry's age.
		if len(s.entries) >= s.maxEntries {
			oldest := s.creationOrder.Front()
			delete(s.entries, oldest.Value.(string))
			s.creationOrder.Remove(oldest)
		}
		s.creationOrder.PushBack(id)
	}

	entry := paste{
		content:      content,
		revision:     s.entries[id].revision + 1,
		lastModified: time.Now().UTC().Format(http.TimeFormat),
	}
	s.entries[id] = entry

	// Queue snapshots, broadcasts, and acknowledgments under the same lock so
	// concurrent writes cannot deliver revisions out of order. Network I/O is
	// handled separately by each client's writer loop.
	event := s.message(id, entry)
	for subscriber := range s.subscribers[id] {
		subscriber.enqueue(event)
	}
	writer.enqueue(message{
		"type":      "ack",
		"action":    "put",
		"requestId": requestID,
		"revision":  entry.revision,
	})
}
