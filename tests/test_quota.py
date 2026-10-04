"""Owned offline fixtures only: no bundled CLI or friend's JavaScript executes."""

import json
import math
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

from codex_bar.quota import (
    CodexQuotaClient, QuotaBridge, QuotaReadError, RateWindow, ReadResult,
    account_fingerprint, minimal_environment, parse_windows,
)


ACCOUNT = {
    "account": {"type": "chatgpt", "email": "fixture@example.invalid", "planType": "pro"},
    "workspaceRouting": {"chatgptAccountId": "offline-fixture-workspace"},
}


def rates(primary=None, secondary=None):
    return {"rateLimitsByLimitId": {"codex": {"primary": primary, "secondary": secondary}}}


def window(minutes=300, used=12, reset=2000):
    return {"windowDurationMins": minutes, "usedPercent": used, "resetsAt": reset}


class ParseTests(unittest.TestCase):
    def test_duration_not_position(self):
        parsed = parse_windows(rates(window(10080, 73), window(300, 0)))
        self.assertEqual([(x.window_minutes, x.used_percent) for x in parsed], [(300, 0), (10080, 73)])

    def test_only_codex_bucket(self):
        parsed = parse_windows({"rateLimitsByLimitId": {"model": {"primary": window()}},
                                "rateLimits": {"primary": window()}})
        self.assertEqual(parsed, ())

    def test_empty_map_does_not_fallback(self):
        self.assertEqual(parse_windows({"rateLimitsByLimitId": {}, "rateLimits": {"primary": window()}}), ())

    def test_legacy_only_when_map_unavailable(self):
        for payload in ({"rateLimits": {"primary": window()}},
                        {"rateLimitsByLimitId": None, "rateLimits": {"primary": window()}}):
            self.assertEqual(parse_windows(payload)[0].used_percent, 12)

    def test_malformed_map_rejected(self):
        with self.assertRaises(QuotaReadError):
            parse_windows({"rateLimitsByLimitId": [], "rateLimits": {"primary": window()}})

    def test_unknown_numbers_remain_unknown(self):
        for used in (None, True, False, "12", -1, 101, math.inf, math.nan, 10**400):
            with self.subTest(used=used):
                self.assertIsNone(parse_windows(rates(window(used=used)))[0].used_percent)

    def test_zero_and_full_are_valid(self):
        for used in (0, 100, 22.5):
            self.assertEqual(parse_windows(rates(window(used=used)))[0].used_percent, used)

    def test_invalid_timestamp_is_unknown(self):
        for reset in (None, 0, -1, True, "2000", math.nan, math.inf, 253402300800):
            self.assertIsNone(parse_windows(rates(window(reset=reset)))[0].resets_at)

    def test_missing_window_is_not_unlimited(self):
        self.assertEqual(parse_windows(rates()), ())

    def test_other_duration_is_not_five_hour(self):
        self.assertEqual(parse_windows(rates(window(minutes=240))), ())

    def test_duplicate_duration_is_ambiguous(self):
        self.assertEqual(parse_windows(rates(window(300, 12), window(300, 34))), ())

    def test_wrong_window_structure_rejected(self):
        with self.assertRaises(QuotaReadError):
            parse_windows(rates(primary=[]))


class IdentityTests(unittest.TestCase):
    def test_hash_no_raw_identity(self):
        fingerprint = account_fingerprint(ACCOUNT)
        self.assertEqual(len(fingerprint), 64)
        self.assertNotIn("fixture", fingerprint)

    def test_email_case_normalization(self):
        changed = json.loads(json.dumps(ACCOUNT))
        changed["account"]["email"] = changed["account"]["email"].upper()
        self.assertEqual(account_fingerprint(ACCOUNT), account_fingerprint(changed))

    def test_all_binding_dimensions_matter(self):
        for section, key in (("account", "email"), ("account", "planType"), ("workspaceRouting", "chatgptAccountId")):
            changed = json.loads(json.dumps(ACCOUNT))
            changed[section][key] += "other"
            self.assertNotEqual(account_fingerprint(ACCOUNT), account_fingerprint(changed))

    def test_missing_identity_rejected(self):
        for value in ({}, {"account": None}, {"account": ACCOUNT["account"]},
                      {"account": {"type": "chatgpt"}, "workspaceRouting": ACCOUNT["workspaceRouting"]}):
            with self.assertRaises(QuotaReadError) as context:
                account_fingerprint(value)
            self.assertEqual(context.exception.code, "identity_unknown")

    def test_api_key_is_not_subscription(self):
        with self.assertRaises(QuotaReadError) as context:
            account_fingerprint({"account": {"type": "apiKey"}})
        self.assertEqual(context.exception.code, "api_key_unsupported")

    def test_no_sensitive_environment_inherited(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-secret", "HTTPS_PROXY": "fixture-proxy",
                                          "NODE_OPTIONS": "fixture-options", "CODEX_HOME": "/unrelated-home"}):
            environment = minimal_environment("/fixture-home", "/fixture-codex")
        self.assertEqual(set(environment), {"HOME", "PATH", "LANG", "RUST_LOG", "CODEX_HOME"})
        self.assertEqual(environment["HOME"], "/fixture-home")
        self.assertEqual(environment["CODEX_HOME"], "/fixture-codex")


class SequenceClient:
    def __init__(self, *values):
        self.values = iter(values)

    def fetch(self):
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def reading(identity="a", at=1000, windows=None):
    return ReadResult(identity, windows if windows is not None else (
        RateWindow(12, 300, 2000), RateWindow(73, 10080, 10000)), at)


class StateTests(unittest.TestCase):
    def bridge(self, *values):
        bridge = QuotaBridge(SequenceClient(*values))
        with mock.patch("codex_bar.quota.time.time", return_value=1000):
            bridge.refresh()
        return bridge

    def test_initial_unknown(self):
        snapshot = QuotaBridge(SequenceClient()).snapshot(1000)
        self.assertIsNone(snapshot["limits"]["primary"]["usedPercent"])
        self.assertEqual(snapshot["status"], "waiting")
        self.assertIsNone(snapshot["working"])

    def test_fresh_fields_exclude_identity(self):
        snapshot = self.bridge(reading()).snapshot(1000)
        self.assertEqual(snapshot["limits"]["primary"]["usedPercent"], 12)
        self.assertEqual(snapshot["status"], "fresh")
        self.assertEqual(snapshot["updatedAt"], "1970-01-01T00:16:40Z")
        self.assertNotIn("fingerprint", json.dumps(snapshot))
        self.assertFalse({"email", "account", "account_fingerprint", "workspaceRouting"} & set(snapshot))

    def test_stale_boundary(self):
        bridge = self.bridge(reading())
        self.assertFalse(bridge.snapshot(1179.999)["stale"])
        self.assertTrue(bridge.snapshot(1180)["stale"])
        self.assertEqual(bridge.snapshot(1180)["limits"]["primary"]["usedPercent"], 12)

    def test_hide_boundary(self):
        bridge = self.bridge(reading())
        self.assertEqual(bridge.snapshot(1599.999)["limits"]["primary"]["usedPercent"], 12)
        self.assertIsNone(bridge.snapshot(1600)["limits"]["primary"]["usedPercent"])
        self.assertEqual(bridge.snapshot(1600)["status"], "expired")

    def test_reset_boundary_hides_old_value_without_inventing_zero(self):
        bridge = self.bridge(reading(at=1900))
        self.assertEqual(bridge.snapshot(1999.99)["limits"]["primary"]["usedPercent"], 12)
        snapshot = bridge.snapshot(2000)
        self.assertIsNone(snapshot["limits"]["primary"]["usedPercent"])
        self.assertEqual(snapshot["status"], "reset_pending")
        self.assertEqual(snapshot["limits"]["secondary"]["usedPercent"], 73)

    def test_new_response_with_old_reset_is_still_unknown(self):
        bridge = self.bridge(reading(at=2500))
        self.assertIsNone(bridge.snapshot(2500)["limits"]["primary"]["usedPercent"])

    def test_verified_same_account_error_retains_stale(self):
        bridge = self.bridge(reading(), QuotaReadError("read_failed", "a"))
        with mock.patch("codex_bar.quota.time.time", return_value=1001):
            snapshot = bridge.refresh()
        self.assertTrue(snapshot["stale"])
        self.assertEqual(snapshot["limits"]["primary"]["usedPercent"], 12)

    def test_unknown_account_failure_clears_immediately(self):
        bridge = self.bridge(reading(), QuotaReadError("identity_unknown"))
        snapshot = bridge.refresh()
        self.assertIsNone(snapshot["limits"]["primary"]["usedPercent"])
        self.assertIsNone(snapshot["updatedAt"])

    def test_different_account_failure_clears_immediately(self):
        bridge = self.bridge(reading(), QuotaReadError("read_failed", "b"))
        self.assertIsNone(bridge.refresh()["limits"]["primary"]["usedPercent"])

    def test_account_change_replaces_not_merges(self):
        bridge = self.bridge(reading(), reading("b", windows=(RateWindow(9, 10080, 10000),)))
        with mock.patch("codex_bar.quota.time.time", return_value=1001):
            snapshot = bridge.refresh()
        self.assertIsNone(snapshot["limits"]["primary"]["usedPercent"])
        self.assertEqual(snapshot["limits"]["secondary"]["usedPercent"], 9)

    def test_map_missing_codex_does_not_keep_old_value(self):
        bridge = self.bridge(reading(), reading(windows=()))
        with mock.patch("codex_bar.quota.time.time", return_value=1001):
            snapshot = bridge.refresh()
        self.assertIsNone(snapshot["limits"]["primary"]["usedPercent"])
        self.assertEqual(snapshot["status"], "unavailable")

    def test_repr_hides_fingerprint(self):
        self.assertNotIn("private-test-fingerprint", repr(reading("private-test-fingerprint")))

    def test_present_but_unknown_values_are_partial(self):
        bridge = self.bridge(reading(windows=(RateWindow(None, 300, 2000), RateWindow(None, 10080, 10000))))
        snapshot = bridge.snapshot(1000)
        self.assertEqual(snapshot["status"], "partial")
        self.assertIsNone(snapshot["limits"]["primary"]["usedPercent"])

    def test_snapshot_not_blocked_by_in_progress_refresh(self):
        started, release = threading.Event(), threading.Event()
        class SlowClient:
            def fetch(self):
                started.set()
                release.wait(timeout=2)
                return reading()
        bridge = QuotaBridge(SlowClient())
        worker = threading.Thread(target=bridge.refresh)
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=1))
            before = time.monotonic()
            self.assertEqual(bridge.snapshot(1000)["status"], "waiting")
            self.assertLess(time.monotonic() - before, 0.2)
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="codex-quota-owned-test-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def fake_cli(self, scenario="normal", timeout=1):
        # Entire executable fixture is ours. Never imports bundled/friend code.
        script = self.path / "owned-fixture-cli"
        body = '''
import json, os, sys, time
account = ACCOUNT_DATA
scenario = SCENARIO_DATA
assert sys.argv[1:] == ["app-server", "--listen", "stdio://", "-c", "analytics.enabled=false"]
assert "OPENAI_API_KEY" not in os.environ
def send(value):
    print(json.dumps(value), flush=True)
for raw in sys.stdin:
    request = json.loads(raw)
    method = request["method"]
    if method == "initialized":
        assert request == {"method": "initialized", "params": {}}
        continue
    rid = request["id"]
    if method == "initialize":
        if scenario == "timeout":
            time.sleep(10)
        if scenario == "overflow":
            print("x" * 1100000, flush=True)
            continue
        if scenario == "bad-json":
            print("not-json", flush=True)
            continue
        send({"method": "ignored/notification", "params": {"private": "fixture-never-export"}})
        send({"id": True, "result": {"must": "not match integer 1"}})
        send({"id": rid, "result": {"userAgent": "owned-test"}})
    elif method == "account/read":
        assert request["params"] == {"refreshToken": False}
        if scenario == "api-key":
            send({"id": rid, "result": {"account": {"type": "apiKey"}}})
        elif scenario == "account-error-after" and rid == 4:
            send({"id": rid, "error": {"message": "fixture-sensitive-error"}})
        else:
            if scenario == "account-change" and rid == 4:
                account["workspaceRouting"]["chatgptAccountId"] = "different-workspace"
            send({"id": rid, "result": account})
    elif method == "account/rateLimits/read":
        if scenario == "rate-error":
            print("fixture-sensitive-stderr", file=sys.stderr, flush=True)
            send({"id": rid, "error": {"message": "fixture-sensitive-error"}})
        else:
            send({"id": rid, "result": {"rateLimitsByLimitId": {"codex": {
                "secondary": {"windowDurationMins": 300, "usedPercent": 12, "resetsAt": 9999999999},
                "primary": {"windowDurationMins": 10080, "usedPercent": 42, "resetsAt": 9999999999}}}}})
    else:
        raise AssertionError("Unexpected method")
'''
        body = body.replace("ACCOUNT_DATA", repr(ACCOUNT)).replace("SCENARIO_DATA", repr(scenario))
        script.write_text("#!" + sys.executable + "\n" + textwrap.dedent(body))
        script.chmod(0o700)
        return CodexQuotaClient(script, home=self.path, timeout=timeout)

    def test_full_protocol_and_no_secrets(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-secret"}):
            result = self.fake_cli().fetch()
        self.assertEqual([(x.window_minutes, x.used_percent) for x in result.windows], [(300, 12), (10080, 42)])
        self.assertEqual(result.account_fingerprint, account_fingerprint(ACCOUNT))
        self.assertNotIn("fixture-never-export", repr(result))

    def test_account_switch_rejected(self):
        with self.assertRaises(QuotaReadError) as context:
            self.fake_cli("account-change").fetch()
        self.assertEqual(context.exception.code, "account_changed")
        self.assertIsNone(context.exception.account_fingerprint)

    def test_rate_error_still_checks_identity(self):
        with self.assertRaises(QuotaReadError) as context:
            self.fake_cli("rate-error").fetch()
        self.assertEqual(context.exception.account_fingerprint, account_fingerprint(ACCOUNT))
        self.assertNotIn("fixture-sensitive", str(context.exception))

    def test_later_identity_failure_prevents_cache_reuse(self):
        with self.assertRaises(QuotaReadError) as context:
            self.fake_cli("account-error-after").fetch()
        self.assertIsNone(context.exception.account_fingerprint)

    def test_api_key_does_not_request_quota(self):
        with self.assertRaises(QuotaReadError) as context:
            self.fake_cli("api-key").fetch()
        self.assertEqual(context.exception.code, "api_key_unsupported")

    def test_timeout_is_bounded_and_child_reaped(self):
        client = self.fake_cli("timeout", timeout=0.15)
        children = []
        original_popen = __import__("subprocess").Popen
        def tracked(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            children.append(process)
            return process
        before = time.monotonic()
        with mock.patch("codex_bar.quota.subprocess.Popen", side_effect=tracked):
            with self.assertRaises(QuotaReadError) as context:
                client.fetch()
        self.assertEqual(context.exception.code, "timeout")
        self.assertLess(time.monotonic() - before, 2)
        self.assertIsNotNone(children[0].poll())

    def test_malformed_and_oversize_output(self):
        for scenario in ("overflow", "bad-json"):
            with self.subTest(scenario=scenario):
                with self.assertRaises(QuotaReadError) as context:
                    self.fake_cli(scenario).fetch()
                self.assertEqual(context.exception.code, "protocol_error")

    def test_missing_executable_fixed_error(self):
        with self.assertRaises(QuotaReadError) as context:
            CodexQuotaClient(self.path / "missing").fetch()
        self.assertEqual(context.exception.code, "cli_missing")


if __name__ == "__main__":
    unittest.main()
