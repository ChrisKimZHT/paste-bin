from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("PASTEBIN_HOST", "127.0.0.1")
    port: int = int(os.getenv("PASTEBIN_PORT", "8000"))
    max_bytes: int = int(os.getenv("PASTEBIN_MAX_BYTES", str(1024 * 128)))


class PasteStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._text = ""
        self._mtime = time()

    def read(self) -> tuple[bytes, float]:
        with self._lock:
            return self._text.encode("utf-8"), self._mtime

    def write(self, text: str) -> float:
        with self._lock:
            self._text = text
            self._mtime = time()
            return self._mtime


class PasteHandler(BaseHTTPRequestHandler):
    server_version = "PastebinHTTP/0.1"

    def send_api_header(self) -> None:
        self.send_header("X-Max-Bytes", str(self.settings.max_bytes))

    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def store(self) -> PasteStore:
        return self.server.store  # type: ignore[attr-defined]

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Content-Length, If-Modified-Since")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            index = Path("index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(index)))
            self.end_headers()
            self.wfile.write(index)
            return

        if self.path != "/api":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data, mtime = self.store.read()
        self.send_response(HTTPStatus.OK)
        self.send_api_header()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Last-Modified", formatdate(mtime, usegmt=True))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        if self.path != "/api":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data, mtime = self.store.read()
        self.send_response(HTTPStatus.OK)
        self.send_api_header()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Last-Modified", formatdate(mtime, usegmt=True))
        self.end_headers()

    def do_PUT(self) -> None:
        if self.path != "/api":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self.send_error(HTTPStatus.LENGTH_REQUIRED)
            return

        try:
            body_size = int(content_length)
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return

        if body_size > self.settings.max_bytes:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "payload too large")
            return

        body = self.rfile.read(body_size)
        if len(body) != body_size:
            self.send_error(HTTPStatus.BAD_REQUEST, "truncated request body")
            return

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "body must be utf-8 text")
            return

        self.store.write(text)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_api_header()
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    settings = Settings()
    store = PasteStore()
    server = ThreadingHTTPServer((settings.host, settings.port), PasteHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.store = store  # type: ignore[attr-defined]
    print(f"Listening on http://{settings.host}:{settings.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()