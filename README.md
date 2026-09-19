# paste-bin

A minimal Pastebin that stores pastes directly in memory.

## Run

### Binary

```sh
PASTEBIN_HOST=127.0.0.1 \
PASTEBIN_PORT=8000 \
PASTEBIN_MAX_BYTES=131072 \
PASTEBIN_MAX_ENTRIES=1024 \
./pastebin
```

### Docker

```sh
docker run -d \
  --name paste-bin \
  --restart unless-stopped \
  -p 8000:8000 \
  -e PASTEBIN_HOST=0.0.0.0 \
  -e PASTEBIN_PORT=8000 \
  -e PASTEBIN_MAX_BYTES=131072 \
  -e PASTEBIN_MAX_ENTRIES=1024 \
  chriskimzht/paste-bin:1
```

Open [http://localhost:8000](http://localhost:8000). All pastes are lost when the process stops.

## Environment variables

| Variable               | Default                         | Description                            |
| ---------------------- | ------------------------------- | -------------------------------------- |
| `PASTEBIN_HOST`        | `127.0.0.1` (Docker: `0.0.0.0`) | Address to listen on.                  |
| `PASTEBIN_PORT`        | `8000`                          | Port to listen on.                     |
| `PASTEBIN_MAX_BYTES`   | `131072` (128 KiB)              | Maximum UTF-8 size per paste in bytes. |
| `PASTEBIN_MAX_ENTRIES` | `1024`                          | Maximum number of stored pastes.       |
