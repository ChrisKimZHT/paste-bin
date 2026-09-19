# Pastebin

Minimal multi-paste backend written in Go, using the standard HTTP server and a single WebSocket dependency (`github.com/coder/websocket`). The front-end is embedded in the executable.

## Run

```bash
go run .
```

Requires Go 1.23 or newer. Open http://127.0.0.1:8000.

To build a standalone executable (no Go installation needed on the target machine):

```bash
go build -trimpath -ldflags="-s -w" -o pastebin .
./pastebin
```

On Windows, build with `-o pastebin.exe` and run `.\pastebin.exe`.

Or use Docker:

```bash
docker build -t pastebin .
docker run --rm -p 8000:8000 pastebin
```

Notice: Paste content is stored only in memory and is lost when the process restarts.

## API

- `GET /ws?id=<paste_id>` upgrades to the only data API: a bidirectional WebSocket.
- If `id` is omitted, the server uses `default`.
- Valid `paste_id` pattern: `^[A-Za-z0-9_-]{1,128}$`.

Client messages are JSON objects:

```json
{"type":"get","requestId":"1"}
{"type":"put","requestId":"2","content":"hello"}
```

The server responds with `paste`, `ack`, or `error` messages. Every `paste` message contains the complete current content, revision, UTF-8 byte length, modification time, and size limit. A successful write broadcasts a `paste` message to every WebSocket subscribed to the same paste id.

The former `/api` REST endpoint has been removed. The browser reconnects with exponential backoff and sends `get` again after reconnecting.

## Static front-end

`GET /` and `GET /index.html` serve the embedded `index.html`. Rebuild after editing the page.

## Configuration

Environment variables:

- `PASTEBIN_HOST` default `127.0.0.1`
- `PASTEBIN_PORT` default `8000`
- `PASTEBIN_MAX_BYTES` default `131072` (128 KB)
- `PASTBIN_MAX_ENTRIES` default `1024`; must be a positive integer. Once full, writing a new paste ID automatically evicts the oldest entry by creation order (FIFO). Reads and updates do not change this order. Only successful writes create entries, including writes of empty content. Reading an evicted ID returns an empty paste with revision 0; writing it again creates a new entry with revision 1.

The API, browser UI, paste IDs, and configuration are unchanged from the Python version. Content limits count UTF-8 bytes. Slow clients whose outgoing queue fills are disconnected; the browser reconnects and fetches the latest content.

## Code layout

- `main.go`: environment configuration and startup.
- `server.go`: HTTP routing, WebSocket lifecycle, and request validation.
- `store.go`: typed paste data, subscriptions, and ordered broadcasts.

The store lock protects data and message ordering; socket writes run separately for each client. The project stays in one Go package with one external dependency.

## Checks

```bash
go test ./...
go vet ./...
```
