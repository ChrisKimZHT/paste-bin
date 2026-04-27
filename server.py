from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("PASTEBIN_HOST", "127.0.0.1")
    port: int = int(os.getenv("PASTEBIN_PORT", "8000"))
    max_bytes: int = int(os.getenv("PASTEBIN_MAX_BYTES", str(1024 * 128)))


class PasteStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, float]] = {}
        self._empty_mtime = time()

    def read(self, paste_id: str) -> tuple[bytes, float]:
        with self._lock:
            text, mtime = self._entries.get(paste_id, ("", self._empty_mtime))
            return text.encode("utf-8"), mtime

    def write(self, paste_id: str, text: str) -> float:
        with self._lock:
            mtime = time()
            self._entries[paste_id] = (text, mtime)
            return mtime


class PasteHandler(BaseHTTPRequestHandler):
    server_version = "PastebinHTTP/0.1"
    paste_id_pattern = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

    def send_api_header(self, paste_id: str, data_length: int | None = None) -> None:
        self.send_header("X-Max-Bytes", str(self.settings.max_bytes))
        self.send_header("X-Paste-Id", paste_id)
        if data_length is not None:
            self.send_header("X-Data-Length", str(data_length))

    def send_api_cache_control_headers(self) -> None:
        # Prevent browsers and intermediaries from storing API responses.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

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
        self.send_header("Access-Control-Expose-Headers", "X-Max-Bytes, X-Data-Length, X-Paste-Id, Last-Modified")
        super().end_headers()

    def parse_request_target(self) -> tuple[str, str | None]:
        parts = urlsplit(self.path)
        if parts.path != "/api":
            return parts.path, None

        query = parse_qs(parts.query)
        paste_id = query.get("id", ["default"])[0].strip() or "default"
        if not self.paste_id_pattern.fullmatch(paste_id):
            return parts.path, None
        return parts.path, paste_id

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        path, paste_id = self.parse_request_target()
        if path in ("/", "/index.html"):
            index = Path("index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(index)))
            self.end_headers()
            self.wfile.write(index)
            return

        if path != "/api":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if paste_id is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid paste id")
            return

        data, mtime = self.store.read(paste_id)
        self.send_response(HTTPStatus.OK)
        self.send_api_header(paste_id, len(data))
        self.send_api_cache_control_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Last-Modified", formatdate(mtime, usegmt=True))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        path, paste_id = self.parse_request_target()
        if path != "/api":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if paste_id is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid paste id")
            return

        data, mtime = self.store.read(paste_id)
        self.send_response(HTTPStatus.OK)
        self.send_api_header(paste_id, len(data))
        self.send_api_cache_control_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Last-Modified", formatdate(mtime, usegmt=True))
        self.end_headers()

    def do_PUT(self) -> None:
        path, paste_id = self.parse_request_target()
        if path != "/api":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if paste_id is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid paste id")
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

        self.store.write(paste_id, text)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_api_header(paste_id)
        self.send_api_cache_control_headers()
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