# Pastebin backend

Minimal standard-library backend for a single paste.

## Run

```bash
python server.py
```

Notice: The paste content is stored only in memory and is lost when the process restarts.

## API

- `GET /api` returns the current text.
- `PUT /api` replaces the current text with the request body.
- `HEAD /api` returns `Content-Length` and `Last-Modified` for change detection.
- API responses include `X-Max-Bytes`.

## Static front-end

`GET /` serves `index.html` from the same directory as `server.py`.

## Configuration

Environment variables:

- `PASTEBIN_HOST` default `127.0.0.1`
- `PASTEBIN_PORT` default `8000`
- `PASTEBIN_MAX_BYTES` default `131072` (128 KB)