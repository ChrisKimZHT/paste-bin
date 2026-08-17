# Pastebin

Zero-dependency, ultra-minimal multi-paste backend using only Python's standard library.

## Run

```bash
python server.py
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

`GET /` serves `index.html` from the same directory as `server.py`.

## Configuration

Environment variables:

- `PASTEBIN_HOST` default `127.0.0.1`
- `PASTEBIN_PORT` default `8000`
- `PASTEBIN_MAX_BYTES` default `131072` (128 KB)
