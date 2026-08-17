from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import queue
import re
import socket
import struct
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


WebSocketMessage = dict[str, object]
MessageQueue = queue.Queue[WebSocketMessage | None]


class PasteStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, float, int]] = {}
        self._subscribers: dict[str, set[MessageQueue]] = {}
        self._empty_mtime = time()

    def snapshot(self, paste_id: str) -> WebSocketMessage:
        with self._lock:
            text, mtime, revision = self._entries.get(paste_id, ("", self._empty_mtime, 0))
            return self._event(paste_id, text, mtime, revision)

    def write(self, paste_id: str, text: str) -> WebSocketMessage:
        with self._lock:
            mtime = time()
            previous = self._entries.get(paste_id)
            revision = 1 if previous is None else previous[2] + 1
            self._entries[paste_id] = (text, mtime, revision)
            event = self._event(paste_id, text, mtime, revision)
            for messages in self._subscribers.get(paste_id, ()):
                messages.put_nowait(event)
            return event

    def subscribe(self, paste_id: str) -> MessageQueue:
        messages: MessageQueue = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(paste_id, set()).add(messages)
        return messages

    def unsubscribe(self, paste_id: str, messages: MessageQueue) -> None:
        with self._lock:
            subscribers = self._subscribers.get(paste_id)
            if subscribers is None:
                return
            subscribers.discard(messages)
            if not subscribers:
                self._subscribers.pop(paste_id, None)

    @staticmethod
    def _event(paste_id: str, text: str, mtime: float, revision: int) -> WebSocketMessage:
        return {
            "type": "paste",
            "id": paste_id,
            "revision": revision,
            "content": text,
            "length": len(text.encode("utf-8")),
            "lastModified": formatdate(mtime, usegmt=True),
        }


class WebSocketProtocolError(Exception):
    pass


class PasteHandler(BaseHTTPRequestHandler):
    server_version = "PastebinWS/1.0"
    protocol_version = "HTTP/1.1"
    paste_id_pattern = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
    websocket_magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def store(self) -> PasteStore:
        return self.server.store  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path in ("/", "/index.html"):
            index = Path("index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(index)))
            self.end_headers()
            self.wfile.write(index)
            return

        if parts.path != "/ws":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        paste_id = self.parse_paste_id(parts.query)
        if paste_id is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid paste id")
            return
        self.handle_websocket(paste_id)

    def parse_paste_id(self, query_string: str) -> str | None:
        query = parse_qs(query_string)
        paste_id = query.get("id", ["default"])[0].strip() or "default"
        if not self.paste_id_pattern.fullmatch(paste_id):
            return None
        return paste_id

    def handle_websocket(self, paste_id: str) -> None:
        websocket_key = self.headers.get("Sec-WebSocket-Key", "")
        try:
            valid_key = len(base64.b64decode(websocket_key.encode("ascii"), validate=True)) == 16
        except (binascii.Error, UnicodeEncodeError):
            valid_key = False

        connection_tokens = {
            token.strip().lower() for token in self.headers.get("Connection", "").split(",")
        }
        if (
            self.headers.get("Upgrade", "").lower() != "websocket"
            or "upgrade" not in connection_tokens
            or not valid_key
            or self.headers.get("Sec-WebSocket-Version") != "13"
        ):
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid WebSocket handshake")
            return

        messages = self.store.subscribe(paste_id)
        stop = threading.Event()
        self._websocket_write_lock = threading.Lock()
        writer: threading.Thread | None = None
        try:
            accept = base64.b64encode(
                hashlib.sha1((websocket_key + self.websocket_magic).encode("ascii")).digest()
            ).decode("ascii")
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            writer = threading.Thread(
                target=self.write_websocket_messages,
                args=(messages, stop),
                daemon=True,
            )
            writer.start()
            self.read_websocket_messages(paste_id, messages, stop)
        except (BrokenPipeError, ConnectionError, OSError, WebSocketProtocolError):
            pass
        finally:
            stop.set()
            self.store.unsubscribe(paste_id, messages)
            messages.put_nowait(None)
            if writer is not None:
                writer.join(timeout=1)
            self.close_connection = True

    def read_websocket_messages(
        self,
        paste_id: str,
        messages: MessageQueue,
        stop: threading.Event,
    ) -> None:
        fragments = bytearray()
        fragmented_opcode: int | None = None
        max_message_bytes = self.settings.max_bytes * 6 + 16 * 1024

        while not stop.is_set():
            final, opcode, payload = self.read_websocket_frame(max_message_bytes)
            if opcode == 0x8:
                self.send_websocket_frame(payload[:125], opcode=0x8)
                return
            if opcode == 0x9:
                self.send_websocket_frame(payload, opcode=0xA)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x2:
                self.send_websocket_close(1003, "binary messages are not supported")
                return

            if opcode == 0x1:
                if fragmented_opcode is not None:
                    raise WebSocketProtocolError("unexpected data frame")
                if final:
                    self.handle_websocket_request(paste_id, payload, messages)
                    continue
                fragmented_opcode = opcode
                fragments.extend(payload)
            elif opcode == 0x0:
                if fragmented_opcode is None:
                    raise WebSocketProtocolError("unexpected continuation frame")
                fragments.extend(payload)
                if final:
                    self.handle_websocket_request(paste_id, bytes(fragments), messages)
                    fragments.clear()
                    fragmented_opcode = None
            else:
                raise WebSocketProtocolError("unsupported opcode")

            if len(fragments) > max_message_bytes:
                self.send_websocket_close(1009, "message too large")
                return

    def handle_websocket_request(
        self,
        paste_id: str,
        payload: bytes,
        messages: MessageQueue,
    ) -> None:
        request_id: object = None
        try:
            request = json.loads(payload.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("message must be a JSON object")
            request_id = request.get("requestId")
            action = request.get("type")

            if action == "get":
                response = self.store.snapshot(paste_id)
                response["requestId"] = request_id
                messages.put_nowait(response)
                return

            if action == "put":
                content = request.get("content")
                if not isinstance(content, str):
                    raise ValueError("put.content must be a string")
                if len(content.encode("utf-8")) > self.settings.max_bytes:
                    raise ValueError(f"content exceeds {self.settings.max_bytes} bytes")
                event = self.store.write(paste_id, content)
                messages.put_nowait(
                    {
                        "type": "ack",
                        "action": "put",
                        "requestId": request_id,
                        "revision": event["revision"],
                    }
                )
                return

            raise ValueError("unsupported message type")
        except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError, ValueError) as error:
            messages.put_nowait(
                {
                    "type": "error",
                    "requestId": request_id,
                    "message": str(error),
                }
            )

    def write_websocket_messages(self, messages: MessageQueue, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                try:
                    message = messages.get(timeout=25)
                except queue.Empty:
                    self.send_websocket_frame(b"", opcode=0x9)
                    continue
                if message is None:
                    return
                outgoing = dict(message)
                if outgoing.get("type") == "paste":
                    outgoing["maxBytes"] = self.settings.max_bytes
                payload = json.dumps(outgoing, separators=(",", ":")).encode("utf-8")
                self.send_websocket_frame(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            stop.set()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def read_websocket_frame(self, max_message_bytes: int) -> tuple[bool, int, bytes]:
        first, second = self.read_exact(2)
        if first & 0x70:
            raise WebSocketProtocolError("reserved frame bits are set")
        final = bool(first & 0x80)
        opcode = first & 0x0F
        if not second & 0x80:
            raise WebSocketProtocolError("client frames must be masked")

        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.read_exact(8))[0]
        if length > max_message_bytes:
            self.send_websocket_close(1009, "message too large")
            raise WebSocketProtocolError("message too large")
        if opcode >= 0x8 and (not final or length > 125):
            raise WebSocketProtocolError("invalid control frame")

        mask = self.read_exact(4)
        payload = self.read_exact(length)
        return final, opcode, bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

    def read_exact(self, length: int) -> bytes:
        data = self.rfile.read(length)
        if data is None or len(data) != length:
            raise ConnectionError("WebSocket connection closed")
        return data

    def send_websocket_close(self, code: int, reason: str) -> None:
        payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
        self.send_websocket_frame(payload, opcode=0x8)

    def send_websocket_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))
        with self._websocket_write_lock:
            self.wfile.write(header + payload)
            self.wfile.flush()


class PasteServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> None:
    settings = Settings()
    store = PasteStore()
    server = PasteServer((settings.host, settings.port), PasteHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.store = store  # type: ignore[attr-defined]
    print(f"Listening on http://{settings.host}:{settings.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
