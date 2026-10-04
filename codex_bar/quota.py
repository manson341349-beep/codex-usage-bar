"""Read-only subscription quota bridge. No conversation, auth-file, or log reads.

The manager owns scheduling (60 seconds recommended). Only fetch() starts the
bundled official CLI. Importing this module and constructing a bridge do not.
No identity, raw protocol data, credentials, or disk cache is exported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time
from typing import Any

from . import __version__


DEFAULT_CLI = "/Applications/Codex.app/Contents/Resources/codex-cli/bin/codex"
STALE_AFTER = 180
HIDE_AFTER = 600
REFRESH_SECONDS = 60
MAX_MESSAGE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 4_194_304
MAX_TIMESTAMP = 253_402_300_799
SOURCE = "Codex 订阅额度 · 官方 account/rateLimits/read"

STATUS_LABELS = {
    "waiting": "等待首次读取",
    "fresh": "已同步",
    "partial": "部分额度窗口未提供",
    "unavailable": "订阅额度未提供",
    "stale": "读数已过期",
    "expired": "超过 10 分钟未更新",
    "reset_pending": "已到重置时间，等待服务端更新",
    "identity_unknown": "账户未确认，额度已隐藏",
    "account_changed": "读取期间账户发生变化，等待重新同步",
    "api_key_unsupported": "API 密钥模式不提供订阅额度",
    "cli_missing": "未找到 Codex 官方 CLI",
    "launch_failed": "额度读取进程启动失败",
    "timeout": "额度读取超时",
    "protocol_error": "额度响应无法识别",
    "read_failed": "额度读取失败",
}


def _number(value: Any, minimum: float = 0, maximum: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        return None
    return result


@dataclass(frozen=True)
class RateWindow:
    used_percent: float | None
    window_minutes: int
    resets_at: float | None


@dataclass(frozen=True)
class ReadResult:
    # Internal only. repr intentionally omits identity even in a local traceback.
    account_fingerprint: str = field(repr=False)
    windows: tuple[RateWindow, ...]
    fetched_at: float


class QuotaReadError(Exception):
    """Fixed public error code, never raw stderr/remote error text."""

    def __init__(self, code: str, account_fingerprint: str | None = None):
        self.code = code if code in STATUS_LABELS else "read_failed"
        self.account_fingerprint = account_fingerprint
        super().__init__(STATUS_LABELS[self.code])


def account_fingerprint(result: Any) -> str:
    """Bind a read to an identified ChatGPT workspace + email + plan, in memory.

    The official CLI handles its existing login. This function sees only the
    account/read response; it neither inspects nor refreshes credentials.
    Missing identity is not permission to reuse the previous account's quota.
    """
    if not isinstance(result, dict) or not isinstance(result.get("account"), dict):
        raise QuotaReadError("identity_unknown")
    account = result["account"]
    kind = account.get("type")
    if kind == "apiKey":
        raise QuotaReadError("api_key_unsupported")
    if kind != "chatgpt":
        raise QuotaReadError("identity_unknown")
    routing = result.get("workspaceRouting")
    workspace = routing.get("chatgptAccountId") if isinstance(routing, dict) else None
    email, plan = account.get("email"), account.get("planType")
    if not all(isinstance(value, str) and value.strip() for value in (workspace, email, plan)):
        raise QuotaReadError("identity_unknown")
    # JSON encoding avoids delimiter collisions. No raw metadata survives this call.
    material = json.dumps(["codex-usage-bar-account-v1", workspace, email.lower(), plan],
                          ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def parse_windows(result: Any) -> tuple[RateWindow, ...]:
    """Take only codex subscription windows; never model reserves/API spend."""
    if not isinstance(result, dict):
        raise QuotaReadError("protocol_error")
    buckets = result.get("rateLimitsByLimitId")
    if buckets is None:
        bucket = result.get("rateLimits")
    elif isinstance(buckets, dict):
        bucket = buckets.get("codex")  # An empty map is also authoritative.
    else:
        raise QuotaReadError("protocol_error")
    if bucket is None:
        return ()
    if not isinstance(bucket, dict):
        raise QuotaReadError("protocol_error")
    candidates: dict[int, list[RateWindow]] = {300: [], 10080: []}
    for key in ("primary", "secondary"):
        window = bucket.get(key)
        if window is None:
            continue
        if not isinstance(window, dict):
            raise QuotaReadError("protocol_error")
        duration = _number(window.get("windowDurationMins"))
        if duration not in candidates:
            continue
        used = _number(window.get("usedPercent"), maximum=100)
        reset = _number(window.get("resetsAt"), minimum=1, maximum=MAX_TIMESTAMP)
        candidates[int(duration)].append(RateWindow(used, int(duration), reset))
    # Ambiguous duplicate durations are unknown instead of choosing by position.
    return tuple(windows[0] for windows in candidates.values() if len(windows) == 1)


def minimal_environment(home: str | Path | None = None,
                        codex_home: str | Path | None = None) -> dict[str, str]:
    """Do not inherit API keys, proxy credentials, node options, or debug flags."""
    environment = {
        "HOME": str(Path(home) if home is not None else Path.home()),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "RUST_LOG": "off",
    }
    if codex_home is not None:
        environment["CODEX_HOME"] = str(Path(codex_home).absolute())
    return environment


class _Protocol:
    def __init__(self, process: subprocess.Popen[bytes], deadline: float):
        self.process = process
        self.deadline = deadline
        self.buffer = bytearray()
        self.total_bytes = 0
        self.selector = selectors.DefaultSelector()
        assert process.stdout is not None
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def close(self) -> None:
        self.buffer.clear()
        self.selector.close()

    def send(self, message: dict[str, Any]) -> None:
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise QuotaReadError("read_failed") from None

    def receive(self, expected_id: int) -> dict[str, Any]:
        while True:
            if time.monotonic() >= self.deadline:
                raise QuotaReadError("timeout")
            while b"\n" in self.buffer:
                line, _, remaining = self.buffer.partition(b"\n")
                self.buffer = bytearray(remaining)
                if len(line) > MAX_MESSAGE_BYTES:
                    raise QuotaReadError("protocol_error")
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    raise QuotaReadError("protocol_error") from None
                if not isinstance(message, dict):
                    raise QuotaReadError("protocol_error")
                # Discard every notification and unrelated response, without storage.
                if type(message.get("id")) is not int or message["id"] != expected_id:
                    continue
                if "error" in message or not isinstance(message.get("result"), dict):
                    raise QuotaReadError("read_failed")
                return message["result"]
            if len(self.buffer) > MAX_MESSAGE_BYTES:
                raise QuotaReadError("protocol_error")
            remaining_time = self.deadline - time.monotonic()
            if not self.selector.select(max(0, remaining_time)):
                raise QuotaReadError("timeout")
            try:
                assert self.process.stdout is not None
                chunk = os.read(self.process.stdout.fileno(), 65_536)
            except OSError:
                raise QuotaReadError("read_failed") from None
            if not chunk:
                raise QuotaReadError("read_failed")
            self.total_bytes += len(chunk)
            if self.total_bytes > MAX_TOTAL_BYTES:
                raise QuotaReadError("protocol_error")
            self.buffer.extend(chunk)

    def request(self, request_id: int, method: str,
                params: dict[str, Any] | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        return self.receive(request_id)


class CodexQuotaClient:
    """Own one fresh stdio child for one read, never attach to a chat daemon."""

    def __init__(self, cli_path: str | Path = DEFAULT_CLI, *,
                 home: str | Path | None = None, codex_home: str | Path | None = None,
                 timeout: float = 25):
        self.cli_path = str(cli_path)
        self.home = home
        self.codex_home = codex_home
        self.timeout = timeout

    def fetch(self) -> ReadResult:
        if not Path(self.cli_path).is_file() or not os.access(self.cli_path, os.X_OK):
            raise QuotaReadError("cli_missing")
        try:
            process = subprocess.Popen(
                [self.cli_path, "app-server", "--listen", "stdio://", "-c", "analytics.enabled=false"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=minimal_environment(self.home, self.codex_home),
                cwd=str(Path(self.home) if self.home is not None else Path.home()),
                start_new_session=True, bufsize=0,
            )
        except OSError:
            raise QuotaReadError("launch_failed") from None
        protocol = _Protocol(process, time.monotonic() + self.timeout)
        try:
            protocol.request(1, "initialize", {
                "clientInfo": {"name": "codex_usage_bar", "version": __version__},
                "capabilities": {"experimentalApi": True},
            })
            protocol.send({"method": "initialized", "params": {}})
            before = account_fingerprint(protocol.request(2, "account/read", {"refreshToken": False}))
            # The second identity check also runs after a recoverable quota error.
            quota_error = None
            try:
                windows = parse_windows(protocol.request(3, "account/rateLimits/read"))
            except QuotaReadError as error:
                quota_error = error
                windows = ()
            after = account_fingerprint(protocol.request(4, "account/read", {"refreshToken": False}))
            if before != after:
                raise QuotaReadError("account_changed")
            if quota_error is not None:
                raise QuotaReadError(quota_error.code, before)
            return ReadResult(before, windows, time.time())
        finally:
            protocol.close()
            # Terminate/reap only the direct child we created; no pgrep/pkill.
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass  # Child exited between poll() and terminate().
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=1)
            if process.stdout:
                process.stdout.close()


class QuotaBridge:
    """Thread-safe, memory-only state with account isolation and age policy."""

    def __init__(self, client: CodexQuotaClient | None = None):
        self.client = client if client is not None else CodexQuotaClient()
        self._read: ReadResult | None = None
        self._failure: str | None = None
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()

    def refresh(self) -> dict[str, Any]:
        with self._refresh_lock:
            try:
                reading = self.client.fetch()
            except QuotaReadError as error:
                with self._state_lock:
                    if (self._read is None or error.account_fingerprint is None or
                            error.account_fingerprint != self._read.account_fingerprint):
                        self._read = None
                    self._failure = error.code
            else:
                with self._state_lock:
                    self._read = reading  # New account replaces, never merges, old windows.
                    self._failure = None
        return self.snapshot()

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._state_lock:
            reading, failure = self._read, self._failure
        age = max(0, now - reading.fetched_at) if reading else None
        expired = age is not None and age >= HIDE_AFTER
        stale = failure is not None or (age is not None and age >= STALE_AFTER)
        windows = {window.window_minutes: window for window in reading.windows} if reading else {}
        status = failure or ("waiting" if reading is None else "fresh")
        if not failure and reading is not None:
            status = "expired" if expired else "stale" if stale else "fresh"
            if not stale and (len(windows) < 2 or any(window.used_percent is None for window in windows.values())):
                status = "partial" if windows else "unavailable"
        limits = {}
        for key, duration in (("primary", 300), ("secondary", 10080)):
            window = windows.get(duration)
            reset_passed = window is not None and window.resets_at is not None and window.resets_at <= now
            window_status = "missing"
            if window is not None:
                window_status = "expired" if expired else "reset_pending" if reset_passed else "stale" if stale else "fresh"
                if window.used_percent is None and window_status == "fresh":
                    window_status = "unknown"
                if reset_passed:
                    stale = True
                    if not failure and not expired:
                        status = "reset_pending"
            limits[key] = {
                "usedPercent": None if window is None or expired or reset_passed else window.used_percent,
                "windowMinutes": duration,
                "resetsAt": window.resets_at if window is not None else None,
                "status": window_status,
            }
        updated = datetime.fromtimestamp(reading.fetched_at, timezone.utc).isoformat().replace("+00:00", "Z") if reading else None
        return {
            "limits": limits,
            "stale": stale,
            "status": status,
            "statusLabel": STATUS_LABELS[status],
            "sourceLabel": SOURCE,
            "updatedAt": updated,
            "working": None,
            "model": None,
            "context": {},
            "cache": {},
            "sessionUsage": {},
        }
