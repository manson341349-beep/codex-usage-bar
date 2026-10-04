"""Minimal, bounded CDP transport for one caller-owned loopback Codex instance.

This module neither launches an application nor grants runtime authorization.
The manager must obtain approval and verify process/port/profile ownership in
``validate_scope``. Only the manager's fixed, audited expressions belong in
Runtime.evaluate. Incoming events are discarded without logging their contents.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import math
import re
import secrets
import socket
import struct
import threading
import time
from typing import Callable
from urllib.parse import urlsplit


MAX_HTTP_BODY = 256 * 1024
MAX_HEADERS = 16 * 1024
MAX_MESSAGE = 2 * 1024 * 1024
MAX_FRAGMENTS = 64
MAX_CONTROL_FRAMES = 128
MAX_SKIPPED_MESSAGES = 256
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_METHOD = re.compile(r"[A-Z][A-Za-z0-9]*\.[a-z][A-Za-z0-9]{0,100}\Z")


class CDPError(RuntimeError):
    """Sanitized transport, scope, protocol, or remote-command failure."""


@dataclass(frozen=True)
class AttachedPage:
    target_id: str
    session_id: str
    # Canonical application origin, never a title or a potentially private URL.
    url: str = "app://-/"


def _scope(callback: Callable[[], object]) -> None:
    try:
        if callback() is False:
            raise CDPError("Owned browser scope could not be verified.")
    except Exception:
        raise CDPError("Owned browser scope could not be verified.") from None


def _duration(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise CDPError("Invalid transport timeout.")
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise CDPError("Invalid transport timeout.")
    return float(timeout)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CDPError("Browser request timed out.")
    return remaining


def _recv(sock: socket.socket, count: int, deadline: float) -> bytes:
    sock.settimeout(_remaining(deadline))
    try:
        data = sock.recv(count)
    except (OSError, TimeoutError):
        raise CDPError("Browser transport receive failed.") from None
    if not data:
        raise CDPError("Browser transport closed.")
    return data


def _send(sock: socket.socket, data: bytes, deadline: float) -> None:
    sock.settimeout(_remaining(deadline))
    try:
        sock.sendall(data)
    except (OSError, TimeoutError):
        raise CDPError("Browser transport send failed.") from None


def _connect(port: int, callback: Callable[[], object], deadline: float) -> socket.socket:
    _scope(callback)
    try:
        # A literal IPv4 address: no DNS, proxy, redirect, or external endpoint.
        return socket.create_connection(("127.0.0.1", port), _remaining(deadline))
    except (OSError, TimeoutError):
        raise CDPError("Owned browser connection failed.") from None


def _headers(sock: socket.socket, deadline: float) -> tuple[int, dict[str, str], bytes]:
    data = bytearray()
    while True:
        end = data.find(b"\r\n\r\n")
        if end >= 0:
            if end + 4 > MAX_HEADERS:
                raise CDPError("Browser response headers exceeded the limit.")
            break
        if len(data) >= MAX_HEADERS:
            raise CDPError("Browser response headers exceeded the limit.")
        data.extend(_recv(sock, min(4096, MAX_HEADERS - len(data)), deadline))
    try:
        lines = bytes(data[:end]).decode("ascii").split("\r\n")
        status_parts = lines[0].split(" ", 2)
        if len(status_parts) < 2 or status_parts[0] != "HTTP/1.1":
            raise ValueError
        if not re.fullmatch(r"[1-5][0-9]{2}", status_parts[1]):
            raise ValueError
        headers: dict[str, str] = {}
        if len(lines) > 65:
            raise ValueError
        for line in lines[1:]:
            name, value = line.split(":", 1)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ValueError
            if any(ord(c) < 32 and c != "\t" for c in value) or "\x7f" in value:
                raise ValueError
            name = name.lower()
            if name in headers:
                raise ValueError
            headers[name] = value.strip(" \t")
        return int(status_parts[1]), headers, bytes(data[end + 4 :])
    except (UnicodeError, ValueError):
        raise CDPError("Invalid browser response headers.") from None


def _json(raw: bytes) -> object:
    def reject_constant(_: str) -> None:
        raise ValueError

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), parse_constant=reject_constant,
                          object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise CDPError("Invalid browser JSON response.") from None


def _websocket_path(url: object, port: int) -> str:
    if (not isinstance(url, str) or len(url) > 1024
            or any(ord(char) <= 32 or ord(char) >= 127 for char in url)):
        raise CDPError("Invalid owned browser WebSocket endpoint.")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "ws" and parsed.netloc == f"127.0.0.1:{port}"
                 and not parsed.query and not parsed.fragment
                 and re.fullmatch(r"/devtools/browser/[A-Za-z0-9_-]{1,256}", parsed.path))
    except ValueError:
        valid = False
    if not valid:
        raise CDPError("Invalid owned browser WebSocket endpoint.")
    return parsed.path


def _discover(port: int, callback: Callable[[], object], deadline: float) -> str:
    sock = _connect(port, callback, deadline)
    try:
        _scope(callback)
        request = (f"GET /json/version HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                   "Accept: application/json\r\nConnection: close\r\n\r\n")
        _send(sock, request.encode("ascii"), deadline)
        status, headers, tail = _headers(sock, deadline)
        if status != 200 or "transfer-encoding" in headers:
            raise CDPError("Browser endpoint discovery was refused.")
        length = headers.get("content-length", "")
        if not re.fullmatch(r"[0-9]{1,9}", length):
            raise CDPError("Browser endpoint response has no bounded length.")
        count = int(length)
        if count > MAX_HTTP_BODY or len(tail) > count:
            raise CDPError("Browser endpoint response exceeded the limit.")
        body = bytearray(tail)
        while len(body) < count:
            body.extend(_recv(sock, min(16384, count - len(body)), deadline))
        document = _json(bytes(body))
        if not isinstance(document, dict):
            raise CDPError("Invalid browser endpoint response.")
        return _websocket_path(document.get("webSocketDebuggerUrl"), port)
    finally:
        sock.close()


class _WebSocket:
    def __init__(self, sock: socket.socket, callback: Callable[[], object], tail: bytes = b""):
        self.sock = sock
        self.callback = callback
        self.buffer = bytearray(tail)
        self.closed = False

    @classmethod
    def open(cls, port: int, path: str, callback: Callable[[], object], deadline: float) -> _WebSocket:
        sock = _connect(port, callback, deadline)
        try:
            _scope(callback)
            key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
            request = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                       "Sec-WebSocket-Version: 13\r\n\r\n")
            _send(sock, request.encode("ascii"), deadline)
            status, headers, tail = _headers(sock, deadline)
            expected = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
            connection_tokens = {p.strip().lower() for p in headers.get("connection", "").split(",")}
            if (status != 101 or headers.get("upgrade", "").lower() != "websocket"
                    or "upgrade" not in connection_tokens
                    or headers.get("sec-websocket-accept") != expected
                    or "sec-websocket-extensions" in headers
                    or "sec-websocket-protocol" in headers
                    or "transfer-encoding" in headers):
                raise CDPError("Browser WebSocket handshake was rejected.")
            return cls(sock, callback, tail)
        except Exception:
            sock.close()
            raise

    def _exact(self, length: int, deadline: float) -> bytes:
        _remaining(deadline)
        if self.closed:
            raise CDPError("Browser transport is closed.")
        while len(self.buffer) < length:
            self.buffer.extend(_recv(self.sock, min(16384, length - len(self.buffer)), deadline))
        data = bytes(self.buffer[:length])
        del self.buffer[:length]
        return data

    def send_frame(self, opcode: int, payload: bytes, deadline: float) -> None:
        if self.closed:
            raise CDPError("Browser transport is closed.")
        if len(payload) > MAX_MESSAGE:
            raise CDPError("Browser command exceeded the limit.")
        _scope(self.callback)
        length = len(payload)
        prefix = bytes([0x80 | opcode])
        if length < 126:
            prefix += bytes([0x80 | length])
        elif length <= 65535:
            prefix += b"\xfe" + struct.pack("!H", length)
        else:
            prefix += b"\xff" + struct.pack("!Q", length)
        mask = secrets.token_bytes(4)
        encoded = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        _send(self.sock, prefix + mask + encoded, deadline)

    def receive(self, deadline: float) -> bytes:
        fragments = bytearray()
        fragmented = False
        fragment_count = 0
        control_count = 0
        while True:
            first, second = self._exact(2, deadline)
            opcode = first & 0x0F
            final = bool(first & 0x80)
            if first & 0x70 or second & 0x80 or opcode not in (0, 1, 8, 9, 10):
                raise CDPError("Invalid browser WebSocket frame.")
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exact(2, deadline))[0]
                if length < 126:
                    raise CDPError("Invalid browser WebSocket length.")
            elif length == 127:
                length = struct.unpack("!Q", self._exact(8, deadline))[0]
                if length <= 65535 or length >= 1 << 63:
                    raise CDPError("Invalid browser WebSocket length.")
            if opcode >= 8:
                control_count += 1
                if not final or length > 125 or control_count > MAX_CONTROL_FRAMES:
                    raise CDPError("Invalid browser WebSocket control frame.")
            elif length + len(fragments) > MAX_MESSAGE:
                raise CDPError("Browser message exceeded the limit.")
            payload = self._exact(length, deadline)
            if opcode == 8:
                raise CDPError("Browser transport closed.")
            if opcode == 9:
                self.send_frame(10, payload, deadline)
                continue
            if opcode == 10:
                continue
            if (opcode == 0 and not fragmented) or (opcode == 1 and fragmented):
                raise CDPError("Invalid browser WebSocket fragmentation.")
            fragment_count += 1
            if fragment_count > MAX_FRAGMENTS:
                raise CDPError("Browser fragmentation exceeded the limit.")
            fragments.extend(payload)
            if final:
                return bytes(fragments)
            fragmented = True

    def close(self) -> None:
        self.closed = True
        self.buffer.clear()
        try:
            self.sock.close()
        except OSError:
            pass


class BrowserSession:
    def __init__(self, websocket: _WebSocket, validate_scope: Callable[[], object], timeout: float):
        self._websocket = websocket
        self._validate_scope = validate_scope
        self._timeout = timeout
        self._sequence = 0
        self._lock = threading.Lock()

    @classmethod
    def connect(cls, port: int, validate_scope: Callable[[], object], *, timeout: float = 5.0) -> BrowserSession:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise CDPError("Invalid owned browser port.")
        if not callable(validate_scope):
            raise CDPError("Browser scope validator is required.")
        timeout = _duration(timeout)
        deadline = time.monotonic() + timeout
        try:
            path = _discover(port, validate_scope, deadline)
            websocket = _WebSocket.open(port, path, validate_scope, deadline)
            return cls(websocket, validate_scope, timeout)
        except CDPError:
            raise
        except Exception:
            raise CDPError("Owned browser connection failed.") from None

    def call(self, method: str, params: dict | None = None, session_id: str | None = None,
             *, timeout: float | None = None) -> dict:
        if not isinstance(method, str) or not _METHOD.fullmatch(method):
            raise CDPError("Invalid browser command.")
        # Avoid enabling broad content-bearing event streams. The manager uses
        # explicit requests and never needs these subscriptions.
        if method.endswith(".enable") or method in ("Target.setDiscoverTargets", "Target.setAutoAttach"):
            raise CDPError("Browser event subscriptions are not permitted.")
        if params is not None and not isinstance(params, dict):
            raise CDPError("Invalid browser command parameters.")
        if session_id is not None and (not isinstance(session_id, str) or not _ID.fullmatch(session_id)):
            raise CDPError("Invalid browser target session.")
        duration = _duration(self._timeout if timeout is None else timeout)
        # This API is synchronous; serialize calls rather than retaining foreign
        # responses or private event payloads in a background queue.
        deadline = time.monotonic() + duration
        if not self._lock.acquire(timeout=duration):
            raise CDPError("Browser request timed out.")
        try:
            try:
                _scope(self._validate_scope)
                self._sequence += 1
                request = {"id": self._sequence, "method": method, "params": params or {}}
                if session_id is not None:
                    request["sessionId"] = session_id
                try:
                    raw = json.dumps(request, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")
                except (TypeError, ValueError, UnicodeError, RecursionError):
                    raise CDPError("Invalid browser command parameters.") from None
                self._websocket.send_frame(1, raw, deadline)
                for _ in range(MAX_SKIPPED_MESSAGES + 1):
                    response = _json(self._websocket.receive(deadline))
                    if not isinstance(response, dict):
                        raise CDPError("Invalid browser command response.")
                    response_id = response.get("id")
                    if (type(response_id) is not int or response_id != self._sequence
                            or response.get("sessionId") != session_id):
                        continue
                    if "error" in response:
                        raise CDPError("Browser command was rejected.")
                    result = response.get("result")
                    if not isinstance(result, dict):
                        raise CDPError("Invalid browser command result.")
                    return result
                raise CDPError("Browser event limit exceeded.")
            except Exception as exc:
                self.close()
                if isinstance(exc, CDPError):
                    raise
                raise CDPError("Browser request failed.") from None
        finally:
            self._lock.release()

    def attach_codex_page(self, *, wait_timeout: float = 20.0,
                          allow_application_routes: bool = False) -> AttachedPage:
        """Wait for one canonical application page without returning its URL.

        A listening browser endpoint can precede page creation. Only zero app
        pages is retried; ambiguity, unexpected routes and invalid metadata fail
        immediately. A separate manager guard must still verify the empty home
        composer because a client-side route need not appear in the target URL.
        Daily mode may opt into other routes at the same exact app://- origin;
        this does not grant permission to mount on or read conversation content.
        """
        if type(allow_application_routes) is not bool:
            raise CDPError("Invalid application route option.")
        deadline = time.monotonic() + _duration(wait_timeout)
        while True:
            result = self.call("Target.getTargets", timeout=min(self._timeout, _remaining(deadline)))
            targets = result.get("targetInfos")
            if not isinstance(targets, list) or len(targets) > 256:
                raise CDPError("Invalid browser target inventory.")
            matches = []
            for target in targets:
                if not isinstance(target, dict):
                    raise CDPError("Invalid browser target metadata.")
                if target.get("type") != "page":
                    continue
                url = target.get("url")
                if not isinstance(url, str) or len(url) > 4096:
                    raise CDPError("Invalid browser target metadata.")
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    raise CDPError("Invalid browser target metadata.") from None
                if parsed.scheme != "app" or parsed.netloc != "-":
                    continue
                if not allow_application_routes and url not in ("app://-/", "app://-/index.html"):
                    raise CDPError("Unexpected Codex application route.")
                target_id = target.get("targetId")
                if not isinstance(target_id, str) or not _ID.fullmatch(target_id):
                    raise CDPError("Invalid browser target metadata.")
                matches.append(target_id)
            if len(matches) > 1:
                raise CDPError("A single Codex application page is required.")
            if matches:
                target_id = matches[0]
                break
            time.sleep(min(0.2, _remaining(deadline)))
        attached = self.call("Target.attachToTarget", {"targetId": target_id, "flatten": True},
                             timeout=min(self._timeout, _remaining(deadline)))
        session_id = attached.get("sessionId")
        if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
            raise CDPError("Invalid attached Codex page session.")
        return AttachedPage(target_id=target_id, session_id=session_id)

    def close(self) -> None:
        self._websocket.close()

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
