# Pastebin

Zero-dependency, ultra-minimal multi-paste backend using only Python's standard library.

## Run

```bash
python server.py
```

Notice: Paste content is stored only in memory and is lost when the process restarts.

## API

- `GET /api?id=<paste_id>` returns the current text.
- `PUT /api?id=<paste_id>` replaces the current text with the request body.
- `HEAD /api?id=<paste_id>` returns `X-Data-Length` and `Last-Modified` for change detection.
- `OPTIONS /api` supports CORS preflight.
- If `id` is omitted, the server uses `default`.
- Valid `paste_id` pattern: `^[A-Za-z0-9_-]{1,128}$`.
- API responses include `X-Max-Bytes`, `X-Paste-Id`, and (for `GET`/`HEAD`) `X-Data-Length`.

## Static front-end

`GET /` serves `index.html` from the same directory as `server.py`.

## Configuration

Environment variables:

- `PASTEBIN_HOST` default `127.0.0.1`
- `PASTEBIN_PORT` default `8000`
- `PASTEBIN_MAX_BYTES` default `131072` (128 KB)