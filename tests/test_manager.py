"""Tests only our Python orchestration. Does not evaluate browser JavaScript."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from codex_bar.manager import (ASSET_NAMES, ManagerError, Renderer, main,
                               read_assets, install_expression, run_foreground, run_daily)
from codex_bar.host import HostError


class FakeSession:
    def __init__(self):
        self.calls = []
        self.value = {'installed': True}

    def call(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        return {'result': {'value': self.value}}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.renderer = Renderer(self.session, SimpleNamespace(session_id='test'), token='owned-token')

    def test_building_bundle_only_concatenates_and_escapes_owned_test_strings(self):
        assets = {'bar.js': '/* owned-test-one */', 'adaptive.js': '/* owned-test-two */',
                  'bar.css': '"\\\nCSS sentinel'}
        source = install_expression(assets, '"sentinel')
        self.assertIn(json.dumps(assets['bar.css']), source)
        self.assertIn('homeOnly:true', source)
        self.assertIn('existing-information-bar', source)
        self.assertNotIn('eval(', source)

    def test_install_refusal_is_not_success(self):
        self.session.value = {'installed': False, 'reason': 'existing-information-bar'}
        with self.assertRaises(ManagerError):
            self.renderer.install(dict.fromkeys(ASSET_NAMES, '/* test */'))
        self.assertFalse(self.renderer.installed)

    def test_snapshot_never_forwards_thread_content_or_work_claim(self):
        self.session.value = {'owned': True, 'accepted': True, 'mounted': True, 'visible': True}
        self.renderer.update({'working': True, 'context': {'secret':'DO_NOT_FORWARD'},
                              'account_fingerprint':'PRIVATE_ID', 'status':'fresh',
                              'statusLabel':'已同步', 'limits':{}})
        method, params, session_id = self.session.calls[-1]
        self.assertEqual(method, 'Runtime.evaluate')
        self.assertEqual(session_id, 'test')
        self.assertIn('"working": null', params['expression'])
        self.assertIn('"accountOnly": true', params['expression'])
        self.assertNotIn('DO_NOT_FORWARD', params['expression'])
        self.assertNotIn('PRIVATE_ID', params['expression'])

    def test_unsafe_layout_is_failure(self):
        self.session.value = {'owned':True,'accepted':True,'mounted':True,'overlaps':True}
        with self.assertRaisesRegex(ManagerError, 'layout_unsafe'):
            self.renderer.update({})

    def test_missing_owner_is_failure(self):
        self.session.value = {'owned':False}
        with self.assertRaisesRegex(ManagerError, 'bridge_lost'):
            self.renderer.update({})

    def test_frontend_rejected_or_missing_acceptance_is_failure(self):
        for acceptance in (False, None, 1, 'true'):
            with self.subTest(acceptance=acceptance):
                self.session.value = {'owned': True, 'accepted': acceptance,
                                      'mounted': False, 'visible': False}
                with self.assertRaisesRegex(ManagerError, '^renderer_update_refused$'):
                    self.renderer.update({})

    def test_accepted_unmounted_adapter_can_wait_for_home(self):
        self.session.value = {'owned': True, 'accepted': True,
                              'mounted': False, 'visible': False}
        self.assertFalse(self.renderer.update({})['mounted'])

    def test_cleanup_must_confirm_dom_absent(self):
        self.renderer.installed = True
        self.session.value = {'owned':True,'removed':False}
        with self.assertRaisesRegex(ManagerError, 'cleanup_unverified'):
            self.renderer.dispose()

    def test_cleanup_own_token_only(self):
        self.renderer.installed = True
        self.session.value = {'owned':True,'removed':True}
        self.assertTrue(self.renderer.dispose())
        self.assertIn('owned-token', self.session.calls[-1][1]['expression'])
        self.assertIn('h.api.dispose()', self.session.calls[-1][1]['expression'])

    def test_remote_error_details_are_not_output(self):
        self.session.call = lambda *args, **kwargs: {'exceptionDetails': {'text':'SECRET'}}
        with self.assertRaisesRegex(ManagerError,'^renderer_evaluation_failed$'):
            self.renderer.evaluate('/* own test, never executed */')

    def test_no_ack_no_live_action(self):
        with patch('codex_bar.manager.ManagedHost') as host:
            for command in ('run','login','cleanup'):
                with patch('sys.stderr'), self.assertRaises(SystemExit):
                    main([command])
            host.assert_not_called()

    def test_launch_refusal_never_stops_preexisting_owned_instance(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.side_effect = HostError('owned_instance_or_orphan_already_running')
        with patch('codex_bar.manager.ManagedHost', return_value=host), patch('builtins.print'):
            with self.assertRaises(HostError):
                run_foreground(login_only=True, duration=1)
        host.stop.assert_not_called()

    def test_failed_renderer_connect_stops_newly_launched_instance(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345)
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession.connect', side_effect=ManagerError('fixture_failure')), \
             patch('builtins.print'):
            with self.assertRaisesRegex(ManagerError, 'fixture_failure'):
                run_foreground(duration=1)
        host.stop.assert_called_once()

    def test_host_cleanup_failure_does_not_report_success(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345)
        host.stop.side_effect = HostError('cleanup_incomplete')
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession.connect', side_effect=ManagerError('fixture_failure')), \
             patch('builtins.print') as output:
            with self.assertRaisesRegex(ManagerError, 'cleanup_needs_review'):
                run_foreground(duration=1)
        self.assertNotIn('已关闭', str(output.call_args_list))

    def test_assets_tamper_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            web = root/'web'; web.mkdir()
            manifest = {}
            for name in ASSET_NAMES:
                data = b'/* only our test fixture */'
                (web/name).write_bytes(data)
                manifest[name]=hashlib.sha256(data).hexdigest()
            (web/'asset-manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(set(read_assets(root)),set(ASSET_NAMES))
            (web/'bar.js').write_text('tampered')
            with self.assertRaisesRegex(ManagerError,'fingerprint'):
                read_assets(root)
            (web/'bar.js').unlink()
            (web/'bar.js').symlink_to(web/'bar.css')
            with self.assertRaisesRegex(ManagerError,'unsafe_asset_path'):
                read_assets(root)

    def test_asset_manifest_requires_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'web').mkdir()
            for malformed in (list(ASSET_NAMES), None, True):
                (root / 'web' / 'asset-manifest.json').write_text(json.dumps(malformed))
                with self.subTest(manifest=malformed), self.assertRaisesRegex(ManagerError, 'asset_manifest_mismatch'):
                    read_assets(root)

    def test_frontend_rejection_runs_full_cleanup(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345, profile_root=Path('/owned-fixture'))
        renderer = MagicMock()
        renderer.update.side_effect = ManagerError('renderer_update_refused')
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession') as browser, \
             patch('codex_bar.manager.Renderer', return_value=renderer), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('builtins.print'):
            with self.assertRaisesRegex(ManagerError, '^renderer_update_refused$'):
                run_foreground(duration=1)
        renderer.dispose.assert_called_once()
        browser.connect.return_value.close.assert_called_once()
        worker.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        host.stop.assert_called_once()

    def test_pending_quota_request_keeps_heartbeat_and_stop_responsive(self):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345, profile_root=Path('/owned-fixture'))
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        renderer = MagicMock()
        renderer.update.return_value = {'mounted':True, 'visible':True}
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession'), \
             patch('codex_bar.manager.Renderer', return_value=renderer), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda:clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=sleep), patch('builtins.print'):
            run_foreground(duration=11)
        self.assertEqual(renderer.update.call_count, 3)
        worker.submit.assert_called_once()
        renderer.dispose.assert_called_once()
        host.stop.assert_called_once()
        self.assertLess(clock[0],11.3)


class DailyManagerTests(unittest.TestCase):
    def setUp(self):
        self.host = MagicMock()
        self.host.__enter__.return_value = self.host
        self.host.quota_home = Path('/fixture/.codex')
        self.host.launch_or_attach.return_value = SimpleNamespace(port=12345)
        self.host.is_running.return_value = True
        self.host.detach.return_value = {'appLeftRunning': True}

    def test_refuses_active_foreign_instance_without_closing_it(self):
        self.host.launch_or_attach.side_effect = HostError('daily_instance_running')
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}):
            with self.assertRaisesRegex(HostError, 'daily_instance_running'):
                run_daily()
        self.host.stop.assert_not_called()

    def test_wait_can_be_cancelled_without_starting_or_stopping_codex(self):
        stop = [False]
        def cancel(_):
            stop[0] = True
        self.host.launch_or_attach.side_effect = HostError('daily_instance_running')
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.stop_signals') as signals, \
             patch('codex_bar.manager.time.sleep', side_effect=cancel), patch('builtins.print'):
            signals.return_value.__enter__.return_value = stop
            run_daily(wait_for_exit=True)
        self.host.launch_or_attach.assert_called_once()
        self.host.stop.assert_not_called()
        self.host.detach.assert_not_called()

    def test_connection_failure_detaches_and_never_stops_daily_codex(self):
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession.connect', side_effect=ManagerError('fixture')), \
             patch('builtins.print'):
            with self.assertRaisesRegex(ManagerError, 'fixture'):
                run_daily(duration=1)
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()

    def test_daily_deadline_disposes_bar_but_preserves_app_and_uses_daily_quota_home(self):
        clock = [0.0]
        renderer = MagicMock()
        renderer.update.return_value = {'mounted': True, 'visible': True}
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession') as browser, \
             patch('codex_bar.manager.Renderer', return_value=renderer), \
             patch('codex_bar.manager.CodexQuotaClient') as quota, \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0]+s)), \
             patch('builtins.print'):
            run_daily(duration=1)
        quota.assert_called_once_with(codex_home=Path('/fixture/.codex'))
        browser.connect.return_value.attach_codex_page.assert_called_once_with(allow_application_routes=True)
        renderer.dispose.assert_called_once()
        browser.connect.return_value.close.assert_called_once()
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()

    def test_user_quitting_app_is_normal_and_skips_disposing_dead_renderer(self):
        self.host.is_running.return_value = False
        self.host.detach.return_value = {'appLeftRunning': False}
        renderer = MagicMock()
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession'), \
             patch('codex_bar.manager.Renderer', return_value=renderer), \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor'), patch('builtins.print'):
            run_daily(duration=1)
        renderer.dispose.assert_not_called()
        self.host.stop.assert_not_called()
        self.host.detach.assert_called_once()

    def test_daily_ack_and_mixed_profile_flags_are_rejected_before_actions(self):
        for args in (['daily'], ['daily', '--acknowledge-runtime', '--reuse-approved-profile'],
                     ['status', '--wait-for-exit']):
            with patch('codex_bar.manager.DailyHost') as host, patch('sys.stderr'), self.assertRaises(SystemExit):
                main(args)
            host.assert_not_called()

    def test_liveness_failure_cannot_skip_transport_worker_or_host_cleanup(self):
        self.host.is_running.side_effect = HostError('process_inspection_failed')
        worker = MagicMock()
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession') as browser, \
             patch('codex_bar.manager.Renderer'), \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), patch('builtins.print'):
            with self.assertRaisesRegex(HostError, 'process_inspection_failed'):
                run_daily(duration=1)
        browser.connect.return_value.close.assert_called_once()
        worker.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()

    def test_quit_racing_renderer_disposal_is_normal_if_all_owned_processes_and_port_gone(self):
        self.host.is_running.side_effect = [False, True]
        self.host.detach.return_value = {'status': 'stopped', 'appLeftRunning': False,
                                       'ownedProcessCount': 0, 'debugPortOpen': False}
        renderer = MagicMock()
        renderer.dispose.side_effect = ManagerError('renderer_gone')
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession'), \
             patch('codex_bar.manager.Renderer', return_value=renderer), \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor'), patch('builtins.print'):
            run_daily(duration=1)
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
