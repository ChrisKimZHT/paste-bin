FROM python:3.13-slim

WORKDIR /app

COPY server.py index.html ./

ENV PASTEBIN_HOST=0.0.0.0 \
    PASTEBIN_PORT=8000 \
    PASTEBIN_MAX_BYTES=131072

EXPOSE 8000

CMD ["python", "server.py"]
