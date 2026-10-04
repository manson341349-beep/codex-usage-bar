"""Own protocol fixtures only: no sockets, browser, or extracted JS execute."""

import base64
import hashlib
import json
import re
import struct
import time
import unittest
from unittest.mock import Mock, patch

from codex_bar import cdp


PORT = 49123
ENDPOINT = f"ws://127.0.0.1:{PORT}/devtools/browser/browser-123"


class FakeSocket:
    def __init__(self, data=b"", on_send=None, chunk_size=16384):
        self.incoming = bytearray(data)
        self.sent = []
        self.timeouts = []
        self.closed = False
        self.on_send = on_send
        self.chunk_size = chunk_size

    def recv(self, size):
        size = min(size, self.chunk_size)
        value = bytes(self.incoming[:size])
        del self.incoming[:size]
        return value

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def sendall(self, data):
        self.sent.append(data)
        if self.on_send:
            self.on_send(self, data)

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.value = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.value += duration


def http_response(document=None, *, status=200, headers=None, raw_body=None):
    body = raw_body if raw_body is not None else json.dumps(
        document if document is not None else {"webSocketDebuggerUrl": ENDPOINT}
    ).encode()
    fields = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    if headers:
        fields.update(headers)
    head = f"HTTP/1.1 {status} Fixture\r\n"
    head += "".join(f"{key}: {value}\r\n" for key, value in fields.items())
    return head.encode() + b"\r\n" + body


def frame(payload, opcode=1, *, final=True, masked=False, rsv=0):
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
    first = opcode | (0x80 if final else 0) | rsv
    mask_bit = 0x80 if masked else 0
    if len(payload) < 126:
        head = bytes((first, mask_bit | len(payload)))
    elif len(payload) <= 65535:
        head = bytes((first, mask_bit | 126)) + struct.pack("!H", len(payload))
    else:
        head = bytes((first, mask_bit | 127)) + struct.pack("!Q", len(payload))
    return head + payload


def decode_client_frame(raw):
    first, second = raw[:2]
    assert second & 0x80, "RFC client frames must be masked"
    size = second & 127
    offset = 2
    if size == 126:
        size = struct.unpack("!H", raw[2:4])[0]
        offset = 4
    elif size == 127:
        size = struct.unpack("!Q", raw[2:10])[0]
        offset = 10
    key = raw[offset : offset + 4]
    encoded = raw[offset + 4 :]
    assert len(encoded) == size
    return first & 15, bytes(byte ^ key[index % 4] for index, byte in enumerate(encoded))


def handshake_socket(frames=b"", *, extra_headers="", bad_accept=False, status=101):
    def on_send(sock, request):
        if not request.startswith(b"GET "):
            return
        key = re.search(br"Sec-WebSocket-Key: ([^\r]+)", request).group(1).decode()
        accept = base64.b64encode(hashlib.sha1((key + cdp._GUID).encode()).digest()).decode()
        if bad_accept:
            accept = "bad-accept"
        response = (f"HTTP/1.1 {status} Fixture\r\nUpgrade: websocket\r\n"
                    f"Connection: keep-alive, Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n"
                    f"{extra_headers}\r\n").encode()
        sock.incoming.extend(response + frames)
    return FakeSocket(on_send=on_send)


def direct_session(incoming, callback=lambda: None):
    sock = FakeSocket(incoming)
    transport = cdp._WebSocket(sock, callback)
    return cdp.BrowserSession(transport, callback, 1.0), sock


class DiscoveryTests(unittest.TestCase):
    def test_fixed_loopback_paths_masking_and_revalidation(self):
        verifier = Mock(return_value=True)
        discovery = FakeSocket(http_response(), chunk_size=3)
        browser = handshake_socket(frame({"id": 1, "result": {"ok": True}}))
        with patch.object(cdp.socket, "create_connection", side_effect=[discovery, browser]) as connect:
            with cdp.BrowserSession.connect(PORT, verifier) as session:
                self.assertEqual(session.call("Target.getTargets"), {"ok": True})
        self.assertEqual([call.args[0] for call in connect.call_args_list], [("127.0.0.1", PORT)] * 2)
        self.assertTrue(discovery.sent[0].startswith(b"GET /json/version HTTP/1.1\r\n"))
        self.assertTrue(browser.sent[0].startswith(b"GET /devtools/browser/browser-123 HTTP/1.1\r\n"))
        self.assertNotIn(b"Origin:", browser.sent[0])
        opcode, command = decode_client_frame(browser.sent[1])
        self.assertEqual(opcode, 1)
        self.assertEqual(json.loads(command)["method"], "Target.getTargets")
        self.assertGreaterEqual(verifier.call_count, 6)
        self.assertTrue(discovery.closed)
        self.assertTrue(browser.closed)

    def test_no_connection_when_scope_is_unverified(self):
        for callback in (lambda: False, Mock(side_effect=RuntimeError("PRIVATE fixture"))):
            with self.subTest(callback=type(callback).__name__):
                with patch.object(cdp.socket, "create_connection") as connect:
                    with self.assertRaises(cdp.CDPError) as error:
                        cdp.BrowserSession.connect(PORT, callback)
                self.assertNotIn("PRIVATE", str(error.exception))
                connect.assert_not_called()

    def test_callback_runs_again_before_each_http_request(self):
        sock = FakeSocket(http_response())
        with patch.object(cdp.socket, "create_connection", return_value=sock):
            with self.assertRaises(cdp.CDPError):
                cdp.BrowserSession.connect(PORT, Mock(side_effect=[True, False]))
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)

    def test_rejects_invalid_port_timeout_and_missing_validator(self):
        with patch.object(cdp.socket, "create_connection") as connect:
            for port in (0, 65536, True, "49123", 4.5):
                with self.subTest(port=port), self.assertRaises(cdp.CDPError):
                    cdp.BrowserSession.connect(port, lambda: None)
            for timeout in (0, -1, 61, float("inf"), float("nan"), True, "1"):
                with self.subTest(timeout=timeout), self.assertRaises(cdp.CDPError):
                    cdp.BrowserSession.connect(PORT, lambda: None, timeout=timeout)
            with self.assertRaises(cdp.CDPError):
                cdp.BrowserSession.connect(PORT, None)
        connect.assert_not_called()

    def test_rejects_redirect_and_unbounded_or_oversize_http(self):
        fixtures = [
            http_response(status=302, headers={"Location": "https://example.invalid/private"}),
            http_response(headers={"Transfer-Encoding": "chunked"}),
            http_response(headers={"Content-Length": str(cdp.MAX_HTTP_BODY + 1)}),
            http_response(headers={"Content-Length": "-1"}),
            http_response(headers={"Content-Length": "0"}),
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}",
            b"HTTP/1.1 200 OK\r\nX-Test: " + b"x" * cdp.MAX_HEADERS,
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\ncontent-length: 2\r\n\r\n{}",
        ]
        for fixture in fixtures:
            with self.subTest(length=len(fixture)):
                sock = FakeSocket(fixture)
                with patch.object(cdp.socket, "create_connection", return_value=sock) as connect:
                    with self.assertRaises(cdp.CDPError):
                        cdp.BrowserSession.connect(PORT, lambda: None)
                self.assertEqual(connect.call_count, 1)
                self.assertTrue(sock.closed)

    def test_rejects_foreign_endpoint_and_non_browser_paths(self):
        endpoints = [
            f"ws://localhost:{PORT}/devtools/browser/id",
            f"ws://127.0.0.1:{PORT + 1}/devtools/browser/id",
            f"wss://127.0.0.1:{PORT}/devtools/browser/id",
            f"ws://user@127.0.0.1:{PORT}/devtools/browser/id",
            f"ws://127.0.0.1:{PORT}/devtools/browser/id?token=PRIVATE",
            f"ws://127.0.0.1:{PORT}/devtools/browser/id#fragment",
            f"ws://127.0.0.1:{PORT}/devtools/page/id",
            f"ws://127.0.0.1:{PORT}/devtools/browser/../id",
            f"ws://127.0.0.1:{PORT}/devtools/browser/%69d",
            "\n" + ENDPOINT,
            ENDPOINT.replace("127.0.0.1", "127.0.0.2"),
            None,
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                sock = FakeSocket(http_response({"webSocketDebuggerUrl": endpoint}))
                with patch.object(cdp.socket, "create_connection", return_value=sock) as connect:
                    with self.assertRaises(cdp.CDPError) as error:
                        cdp.BrowserSession.connect(PORT, lambda: None)
                self.assertEqual(connect.call_count, 1)
                self.assertNotIn("PRIVATE", str(error.exception))

    def test_handshake_rejects_invalid_accept_extensions_or_protocol(self):
        options = [
            {"bad_accept": True}, {"status": 200},
            {"extra_headers": "Sec-WebSocket-Extensions: permessage-deflate\r\n"},
            {"extra_headers": "Sec-WebSocket-Protocol: undocumented\r\n"},
            {"extra_headers": "Transfer-Encoding: chunked\r\n"},
        ]
        for option in options:
            with self.subTest(option=option):
                browser = handshake_socket(**option)
                with patch.object(cdp.socket, "create_connection", side_effect=[FakeSocket(http_response()), browser]):
                    with self.assertRaises(cdp.CDPError):
                        cdp.BrowserSession.connect(PORT, lambda: None)
                self.assertTrue(browser.closed)

    def test_connection_error_is_sanitized(self):
        with patch.object(cdp.socket, "create_connection", side_effect=OSError("PRIVATE fixture")):
            with self.assertRaises(cdp.CDPError) as error:
                cdp.BrowserSession.connect(PORT, lambda: None)
        self.assertNotIn("PRIVATE", str(error.exception))


class FrameTests(unittest.TestCase):
    def receive(self, wire):
        sock = FakeSocket(wire, chunk_size=3)
        transport = cdp._WebSocket(sock, lambda: None)
        return transport.receive(time.monotonic() + 1), sock

    def test_text_lengths_and_masked_outbound_roundtrip(self):
        for length in (0, 125, 126, 65535, 65536):
            with self.subTest(length=length):
                payload = b"x" * length
                actual, sock = self.receive(frame(payload))
                self.assertEqual(actual, payload)
                cdp._WebSocket(sock, lambda: None).send_frame(1, payload, time.monotonic() + 1)
                self.assertEqual(decode_client_frame(sock.sent[-1]), (1, payload))

    def test_fragmented_utf8_and_ping_pong(self):
        payload = json.dumps({"fixture": "你好"}, ensure_ascii=False).encode()
        wire = frame(payload[:14], final=False) + frame(b"ping", opcode=9)
        wire += frame(b"", opcode=10) + frame(payload[14:], opcode=0)
        actual, sock = self.receive(wire)
        self.assertEqual(actual, payload)
        self.assertEqual(decode_client_frame(sock.sent[0]), (10, b"ping"))

    def test_rejects_rsv_masked_binary_and_reserved_opcodes(self):
        fixtures = [frame(b"x", rsv=0x40), frame(b"x", masked=True), frame(b"x", opcode=2),
                    frame(b"x", opcode=3), frame(b"x", opcode=11)]
        for wire in fixtures:
            with self.subTest(wire=wire), self.assertRaises(cdp.CDPError):
                self.receive(wire)

    def test_rejects_invalid_control_frames(self):
        fixtures = [frame(b"x", opcode=9, final=False), frame(b"x" * 126, opcode=9),
                    frame(b"", opcode=10) * (cdp.MAX_CONTROL_FRAMES + 1)]
        for wire in fixtures:
            with self.subTest(length=len(wire)), self.assertRaises(cdp.CDPError):
                self.receive(wire)

    def test_rejects_invalid_and_excessive_fragments(self):
        fixtures = [frame(b"x", opcode=0), frame(b"x", final=False) + frame(b"y"),
                    frame(b"", final=False) + frame(b"", opcode=0, final=False) * cdp.MAX_FRAGMENTS]
        for wire in fixtures:
            with self.subTest(length=len(wire)), self.assertRaises(cdp.CDPError):
                self.receive(wire)

    def test_rejects_nonminimal_and_oversize_lengths_before_reading_body(self):
        fixtures = [b"\x81\x7e\x00\x01", b"\x81\x7f" + struct.pack("!Q", 126),
                    b"\x81\x7f" + struct.pack("!Q", 1 << 63),
                    b"\x81\x7f" + struct.pack("!Q", cdp.MAX_MESSAGE + 1)]
        for wire in fixtures:
            with self.subTest(wire=wire), self.assertRaises(cdp.CDPError):
                self.receive(wire)

    def test_aggregate_fragment_limit(self):
        with patch.object(cdp, "MAX_MESSAGE", 8):
            with self.assertRaises(cdp.CDPError):
                self.receive(frame(b"12345", final=False) + frame(b"6789", opcode=0))

    def test_close_eof_and_expired_deadline(self):
        for wire in (frame(b"PRIVATE fixture", opcode=8), b""):
            with self.subTest(wire=wire), self.assertRaises(cdp.CDPError) as error:
                self.receive(wire)
            self.assertNotIn("PRIVATE", str(error.exception))
        transport = cdp._WebSocket(FakeSocket(), lambda: None, frame(b"already buffered"))
        with self.assertRaises(cdp.CDPError):
            transport.receive(time.monotonic() - 1)


class CommandTests(unittest.TestCase):
    def test_drops_events_other_requests_and_other_sessions(self):
        incoming = frame({"method": "Private.fixture", "params": {"secret": "PRIVATE"}})
        incoming += frame({"id": 6, "result": {"private": "PRIVATE"}})
        incoming += frame({"id": 1, "sessionId": "other", "result": {"private": "PRIVATE"}})
        incoming += frame({"id": 1, "sessionId": "owned", "result": {"ok": True}})
        session, sock = direct_session(incoming)
        self.assertEqual(session.call("Runtime.evaluate", {"expression": "1"}, "owned"), {"ok": True})
        request = json.loads(decode_client_frame(sock.sent[0])[1])
        self.assertEqual(request["sessionId"], "owned")

    def test_scope_revocation_stops_requests_and_closes_socket(self):
        callback = Mock(side_effect=[True, False])
        session, sock = direct_session(b"", callback)
        with self.assertRaises(cdp.CDPError):
            session.call("Target.getTargets")
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)

    def test_remote_errors_never_include_payload_and_close_transport(self):
        incoming = frame({"id": 1, "error": {"message": "PRIVATE fixture", "data": "PRIVATE"}})
        session, sock = direct_session(incoming)
        with self.assertRaises(cdp.CDPError) as error:
            session.call("Target.getTargets")
        self.assertEqual(str(error.exception), "Browser command was rejected.")
        self.assertTrue(sock.closed)

    def test_subscription_commands_are_rejected_without_send(self):
        for method in ("Runtime.enable", "Network.enable", "Page.enable", "Target.setDiscoverTargets", "Target.setAutoAttach"):
            session, sock = direct_session(b"")
            with self.subTest(method=method), self.assertRaises(cdp.CDPError):
                session.call(method)
            self.assertEqual(sock.sent, [])

    def test_invalid_commands_parameters_and_session_ids(self):
        cases = [("Runtime.evaluate\r\n", {}), ("Runtime.evaluate", []),
                 ("Runtime.evaluate", {"bad": float("nan")}), ("Runtime.evaluate", {"bad": object()})]
        for method, params in cases:
            session, sock = direct_session(b"")
            with self.subTest(method=method), self.assertRaises(cdp.CDPError):
                session.call(method, params)
            self.assertEqual(sock.sent, [])
        session, _ = direct_session(b"")
        with self.assertRaises(cdp.CDPError):
            session.call("Runtime.evaluate", {}, "../../other")

    def test_event_storm_is_bounded(self):
        session, sock = direct_session(frame({"method": "Fixture.event"}) * (cdp.MAX_SKIPPED_MESSAGES + 1))
        with self.assertRaises(cdp.CDPError) as error:
            session.call("Target.getTargets")
        self.assertIn("event limit", str(error.exception))
        self.assertTrue(sock.closed)

    def test_invalid_json_duplicate_keys_utf8_and_non_object_results(self):
        fixtures = [b"\xff", b"not-json", b'{"id":1,"id":1,"result":{}}',
                    b'{"id":1,"result":{"value":NaN}}', b"[]", b'{"id":1,"result":[]}']
        for fixture in fixtures:
            session, sock = direct_session(frame(fixture))
            with self.subTest(fixture=fixture), self.assertRaises(cdp.CDPError):
                session.call("Target.getTargets")
            self.assertTrue(sock.closed)

    def test_boolean_id_is_not_accepted_as_integer_id(self):
        session, _ = direct_session(frame({"id": True, "result": {"bad": True}})
                                    + frame({"id": 1, "result": {"ok": True}}))
        self.assertEqual(session.call("Target.getTargets"), {"ok": True})

    def test_oversize_outbound_command_is_rejected(self):
        session, sock = direct_session(b"")
        with patch.object(cdp, "MAX_MESSAGE", 64), self.assertRaises(cdp.CDPError):
            session.call("Runtime.evaluate", {"expression": "x" * 100})
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)

    def test_single_app_page_selected_without_title_or_full_url(self):
        targets = [
            {"targetId": "foreign", "type": "page", "url": "https://example.invalid", "title": "PRIVATE"},
            {"targetId": "worker", "type": "worker", "url": "app://-/worker.js"},
            {"targetId": "owned", "type": "page", "url": "app://-/index.html", "title": "PRIVATE"},
        ]
        incoming = frame({"id": 1, "result": {"targetInfos": targets}})
        incoming += frame({"id": 2, "result": {"sessionId": "session-owned"}})
        session, sock = direct_session(incoming)
        page = session.attach_codex_page()
        self.assertEqual(page, cdp.AttachedPage("owned", "session-owned"))
        self.assertNotIn("PRIVATE", repr(page))
        attach = json.loads(decode_client_frame(sock.sent[1])[1])
        self.assertEqual(attach["params"], {"targetId": "owned", "flatten": True})

    def test_ambiguous_or_non_codex_target_never_attaches(self):
        target = {"targetId": "one", "type": "page", "url": "app://-/index.html"}
        inventories = [[target, dict(target, targetId="two")],
                       [dict(target, targetId="../unsafe")], "not-list", [target] * 257,
                       [None], [dict(target, url=None)],
                       [dict(target, url="app://-/index.html?fixture=PRIVATE")],
                       [dict(target, url="app://-/index.html#PRIVATE")],
                       [dict(target, url="app://-/threads/PRIVATE")]]
        for targets in inventories:
            session, sock = direct_session(frame({"id": 1, "result": {"targetInfos": targets}}))
            with self.subTest(type=type(targets).__name__), self.assertRaises(cdp.CDPError):
                session.attach_codex_page()
            self.assertEqual(len(sock.sent), 1)

    def test_daily_route_option_accepts_only_one_exact_app_origin_and_omits_url(self):
        targets = [
            {'targetId': 'external', 'type': 'page', 'url': 'https://example.invalid'},
            {'targetId': 'owned', 'type': 'page', 'url': 'app://-/threads/PRIVATE'},
        ]
        incoming = frame({'id': 1, 'result': {'targetInfos': targets}})
        incoming += frame({'id': 2, 'result': {'sessionId': 'daily-session'}})
        session, sock = direct_session(incoming)
        page = session.attach_codex_page(allow_application_routes=True)
        self.assertEqual(page.target_id, 'owned')
        self.assertNotIn('PRIVATE', repr(page))
        self.assertNotIn(b'PRIVATE', b''.join(sock.sent))

    def test_daily_route_option_still_refuses_multiple_app_pages(self):
        targets = [
            {'targetId': 'one', 'type': 'page', 'url': 'app://-/threads/PRIVATE'},
            {'targetId': 'two', 'type': 'page', 'url': 'app://-/index.html'},
        ]
        session, sock = direct_session(frame({'id': 1, 'result': {'targetInfos': targets}}))
        with self.assertRaises(cdp.CDPError):
            session.attach_codex_page(allow_application_routes=True)
        self.assertEqual(len(sock.sent), 1)

    def test_daily_route_option_requires_boolean(self):
        session, sock = direct_session(b'')
        with self.assertRaises(cdp.CDPError):
            session.attach_codex_page(allow_application_routes=1)
        self.assertEqual(sock.sent, [])

    def test_retries_only_empty_app_inventory_then_attaches_within_deadline(self):
        target = {"targetId": "owned", "type": "page", "url": "app://-/"}
        incoming = frame({"id": 1, "result": {"targetInfos": []}})
        incoming += frame({"id": 2, "result": {"targetInfos": [
            dict(target, targetId="foreign", url="app://-@external/index.html")
        ]}})
        incoming += frame({"id": 3, "result": {"targetInfos": [target]}})
        incoming += frame({"id": 4, "result": {"sessionId": "owned-session"}})
        clock = FakeClock()
        session, sock = direct_session(incoming)
        with patch.object(cdp.time, "monotonic", clock.monotonic), patch.object(cdp.time, "sleep", clock.sleep):
            page = session.attach_codex_page(wait_timeout=0.5)
        self.assertEqual(page.session_id, "owned-session")
        self.assertEqual(clock.sleeps, [0.2, 0.2])
        self.assertEqual(len(sock.sent), 4)
        self.assertLessEqual(max(sock.timeouts), 0.5)

    def test_empty_app_startup_wait_obeys_overall_deadline(self):
        incoming = frame({"id": 1, "result": {"targetInfos": []}})
        incoming += frame({"id": 2, "result": {"targetInfos": []}})
        clock = FakeClock()
        session, sock = direct_session(incoming)
        with patch.object(cdp.time, "monotonic", clock.monotonic), patch.object(cdp.time, "sleep", clock.sleep):
            with self.assertRaises(cdp.CDPError) as error:
                session.attach_codex_page(wait_timeout=0.25)
        self.assertIn("timed out", str(error.exception))
        self.assertEqual(len(sock.sent), 2)
        self.assertAlmostEqual(sum(clock.sleeps), 0.25)

    def test_unknown_route_or_ambiguity_is_not_retried(self):
        target = {"targetId": "owned", "type": "page", "url": "app://-/"}
        for targets in ([target, dict(target, targetId="two")],
                        [target, dict(target, targetId="other", url="app://-/unknown")]):
            session, sock = direct_session(frame({"id": 1, "result": {"targetInfos": targets}}))
            with patch.object(cdp.time, "sleep") as sleep:
                with self.assertRaises(cdp.CDPError):
                    session.attach_codex_page()
                sleep.assert_not_called()
            self.assertEqual(len(sock.sent), 1)


if __name__ == "__main__":
    unittest.main()
