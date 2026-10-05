"""Daily lifecycle tests: process metadata fakes; no app, login or history access."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from codex_bar.daily_host import DailyHost, daily_environment
from codex_bar.host import EXECUTABLE, HostError, Process
from codex_bar.manager import recovery_token, run_daily


class Child:
    pid = 100

    def __init__(self, system):
        self.system = system

    def poll(self):
        return None if self.pid in self.system.rows else 0


class DailySystem:
    def __init__(self):
        self.rows = {}
        self.commands = {}
        self.spawns = []
        self.signals = []
        self.now = 0
        self.profile_owners = set()
        self.opened = {}
        self.endpoint_override = None
        self.listener_enabled = True
        self.preflight_checks = 0
        self.appear_on_check = None
        self.signatures = 0

    def snapshot(self):
        return dict(self.rows)

    def command(self, pid):
        return self.commands[pid]

    def listeners(self, port=None, pids=None):
        if self.endpoint_override is not None:
            return list(self.endpoint_override)
        return [(101, '127.0.0.1:54321')] if 101 in self.rows and self.listener_enabled else []

    def open_files(self, pids):
        return [name for pid in pids for name in self.opened.get(pid, [])]

    def profile_users(self, root):
        self.preflight_checks += 1
        if self.appear_on_check == self.preflight_checks:
            self.add_daily()
        return set(self.profile_owners)

    def verify_signature(self):
        self.signatures += 1

    @contextmanager
    def reserve_port(self):
        yield 54321

    def spawn(self, args, env, cwd):
        self.spawns.append((args, env, cwd))
        self.rows[100] = Process(100, 10, 100, 'daily-wrapper-start', str(EXECUTABLE))
        self.rows[101] = Process(101, 100, 100, 'daily-child-start', '/Applications/Codex.app/helper')
        self.commands[100] = ' '.join(args)
        self.profile_owners = {100, 101}
        return Child(self)

    def add_daily(self):
        self.rows[20] = Process(20, 1, 20, 'preexisting-daily', str(EXECUTABLE))
        self.commands[20] = str(EXECUTABLE)
        self.profile_owners.add(20)

    def send_signal(self, *args, **kwargs):
        self.signals.append((args, kwargs))
        raise AssertionError('DailyHost must never signal user processes')

    def stop_created_child(self, *args):
        raise AssertionError('DailyHost must never terminate a created daily app')

    def sleep(self, duration):
        self.now += duration

    def monotonic(self):
        return self.now


class DailyHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.project = self.home / 'project'
        self.project.mkdir()
        self.profile = self.home / 'Library/Application Support/Codex'
        self.profile.mkdir(parents=True)
        self.quota = self.home / '.codex'
        self.quota.mkdir()
        app = self.home / 'Applications/codex-usage-bar.app'
        self.layout = {'home': self.home, 'app': app,
                       'resources': app / 'Contents/Resources/codex-usage-bar',
                       'support': self.home / 'Library/Application Support/codex-usage-bar'}
        self.layout['receipt'] = self.layout['support'] / 'install.json'
        self.binding = patch('codex_bar.daily_host.installation_paths', return_value=self.layout)
        self.binding.start()
        self.system = DailySystem()

    def tearDown(self):
        self.binding.stop()
        self.tmp.cleanup()

    def host(self, project=None):
        return DailyHost(_system=self.system, _project_root=project or self.project)

    @staticmethod
    def write_private(path, record):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(json.dumps(record))
        path.chmod(0o600)

    def test_import_construction_and_lock_do_not_spawn(self):
        with self.host() as host:
            self.assertEqual(host.quota_home, self.quota)
            self.assertEqual(host.profile_root, self.profile)
            self.assertEqual(host.status()['status'], 'not_created')
        self.assertEqual(self.system.spawns, [])

    def test_observer_never_creates_host_state_or_launches_without_permission(self):
        with self.host() as host, patch.object(self.system, 'profile_users', return_value=set()) as scan:
            for _ in range(3):
                self.assertIsNone(host.launch_or_attach(allow_launch=False))
            self.assertFalse(host.state_file.exists())
            scan.assert_not_called()
        self.assertFalse(self.system.spawns)
        self.assertEqual(self.system.signatures, 0)

    def test_observer_attaches_only_existing_verified_identity(self):
        with self.host() as host:
            expected = host.launch_or_attach()
        with self.host() as host:
            self.assertEqual(host.launch_or_attach(allow_launch=False), expected)
        self.assertEqual(len(self.system.spawns), 1)

    def test_observer_refuses_plain_app_without_changing_or_spawning_it(self):
        self.system.add_daily()
        with self.host() as host, patch.object(self.system, 'profile_users', return_value=set()) as scan:
            with self.assertRaisesRegex(HostError, 'daily_instance_running'):
                host.launch_or_attach(allow_launch=False)
            scan.assert_not_called()
        self.assertIn(20, self.system.rows)
        self.assertFalse(self.system.spawns)
        self.assertFalse(self.system.signals)

    def test_observer_ignores_reused_old_port_when_no_codex_is_alive(self):
        with self.host() as host:
            host.launch_or_attach()
            self.system.rows.clear()
            self.system.profile_owners.clear()
            self.system.endpoint_override = [(999, '127.0.0.1:54321')]
            self.assertIsNone(host.launch_or_attach(allow_launch=False))
            with self.assertRaisesRegex(HostError, 'stale_state_port_in_use'):
                host.launch_or_attach()
        self.assertEqual(len(self.system.spawns), 1)

    def test_launch_permission_must_be_a_boolean(self):
        with self.host() as host:
            for invalid in (0, 1, None, 'false', []):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(HostError, 'invalid_launch_options'):
                    host.launch_or_attach(allow_launch=invalid)
        self.assertFalse(self.system.spawns)

    def test_cancel_during_final_blocking_check_never_spawns_and_releases_lock(self):
        for resident in (False, True):
            with self.subTest(resident=resident):
                stop = [False]
                entered, resume = threading.Event(), threading.Event()
                checks = [0]

                def blocked_profile_check(_profile):
                    checks[0] += 1
                    if checks[0] == 3:  # Final scan, after both preflights.
                        entered.set()
                        if not resume.wait(2):
                            raise AssertionError('cancellation fixture did not resume')
                    return set()

                def request_cancel():
                    if entered.wait(2):
                        # Both SIGTERM and supervisor loss set this shared latch.
                        stop[0] = True
                        resume.set()

                waiter = threading.Thread(target=request_cancel)
                host = self.host()
                waiter.start()
                try:
                    with patch('codex_bar.manager.DailyHost', return_value=host), \
                         patch('codex_bar.manager.read_assets', return_value={}), \
                         patch('codex_bar.manager.stop_signals') as signals, \
                         patch('codex_bar.manager._run_daily_session') as session, \
                         patch.object(self.system, 'profile_users', side_effect=blocked_profile_check), \
                         patch('builtins.print') as output:
                        signals.return_value.__enter__.return_value = stop
                        run_daily(resident=resident, launch_once=resident)
                finally:
                    resume.set()
                    waiter.join(timeout=3)
                self.assertFalse(waiter.is_alive())
                self.assertTrue(entered.is_set())
                self.assertTrue(stop[0])
                self.assertEqual(self.system.spawns, [])
                self.assertEqual(self.system.signals, [])
                self.assertIsNone(host._lock_fd)
                self.assertFalse(host.state_file.exists())
                session.assert_not_called()
                self.assertEqual([call.args[0] for call in output.call_args_list],
                                 [] if resident else ['已取消等待；日常 Codex 未改变。'])
                with self.host() as replacement:
                    self.assertIsNotNone(replacement._lock_fd)

    def test_launch_uses_existing_daily_paths_and_preserves_modes(self):
        self.profile.chmod(0o755)
        self.quota.chmod(0o755)
        with self.host() as host:
            endpoint = host.launch_or_attach()
            args, env, cwd = self.system.spawns[0]
            self.assertEqual(endpoint.profile_root, self.profile)
            self.assertEqual(env['CODEX_HOME'], str(self.quota))
            self.assertEqual(env['CODEX_ELECTRON_USER_DATA_PATH'], str(self.profile))
            self.assertEqual(env['HOME'], str(self.home))
            self.assertEqual(cwd, self.home)
            self.assertIn('--user-data-dir=' + str(self.profile), args)
            self.assertIn('--remote-debugging-address=127.0.0.1', args)
            self.assertEqual(host.state_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(host.state_file.read_text())['mode'], 'daily')
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.quota.stat().st_mode & 0o777, 0o755)
        self.assertIn(100, self.system.rows)

    def test_environment_ignores_parent_home_secrets_and_flavor(self):
        with patch.dict(os.environ, {'HOME': '/bad/home', 'OPENAI_API_KEY': 'secret',
                                     'CODEX_HOME': '/bad/codex', 'NODE_OPTIONS': 'bad',
                                     'CODEX_ELECTRON_BUILD_FLAVOR': 'agent'}):
            env = daily_environment(self.home, self.quota, self.profile)
        self.assertEqual(env['HOME'], str(self.home))
        for name in ('OPENAI_API_KEY', 'NODE_OPTIONS', 'CODEX_ELECTRON_BUILD_FLAVOR'):
            self.assertNotIn(name, env)

    def test_missing_profile_is_not_created(self):
        self.profile.rmdir()
        with self.host() as host, self.assertRaisesRegex(HostError, 'daily_profile_missing_or_unsafe'):
            host.launch_or_attach()
        self.assertFalse(self.profile.exists())
        self.assertFalse(self.system.spawns)

    def test_symlink_daily_profile_is_refused(self):
        self.profile.rmdir()
        self.profile.symlink_to(self.quota, target_is_directory=True)
        with self.host() as host, self.assertRaisesRegex(HostError, 'daily_profile_missing_or_unsafe'):
            host.launch_or_attach()
        self.assertTrue(self.profile.is_symlink())

    def test_preexisting_daily_instance_refused_without_signals(self):
        self.system.add_daily()
        with self.host() as host, self.assertRaisesRegex(HostError, 'daily_instance_running'):
            host.launch_or_attach()
        self.assertFalse(self.system.spawns)
        self.assertFalse(self.system.signals)
        self.assertIn(20, self.system.rows)

    def test_unrecognized_profile_holder_refused_without_main(self):
        self.system.profile_owners.add(900)
        with self.host() as host, self.assertRaisesRegex(HostError, 'daily_instance_running'):
            host.preflight()
        self.assertFalse(self.system.spawns)

    def test_rechecks_for_daily_app_appearing_before_spawn(self):
        self.system.appear_on_check = 2
        with self.host() as host, self.assertRaisesRegex(HostError, 'daily_instance_running'):
            host.launch_or_attach()
        self.assertFalse(self.system.spawns)

    def test_shared_manager_lock_excludes_second_manager(self):
        with self.host():
            with self.assertRaisesRegex(HostError, 'another_manager_is_running'):
                with self.host():
                    pass

    def test_recovery_seed_survives_manager_replacement_and_serialized_reload(self):
        with self.host() as host:
            host.launch_or_attach()
            original = host.recovery_seed()
            record = json.loads(host.state_file.read_text())
            self.assertEqual(record['recoverySeed'], original)
            self.assertEqual(host.state_file.stat().st_mode & 0o777, 0o600)
            # Simulate a hard manager exit: no detach or renderer cleanup.
        with self.host() as replacement:
            replacement.launch_or_attach()
            restored = replacement.recovery_seed()
            self.assertEqual(restored, original)
            self.assertEqual(recovery_token(restored, 'same-page'),
                             recovery_token(original, 'same-page'))
            self.assertNotIn('recoverySeed', replacement.status())
        self.assertEqual(len(self.system.spawns), 1)

    def test_new_codex_generation_rotates_recovery_seed(self):
        with self.host() as host:
            host.launch_or_attach()
            original = host.recovery_seed()
            self.system.rows.clear()
            self.system.profile_owners.clear()
            host.detach()
            host.launch_or_attach()
            replacement = host.recovery_seed()
            self.assertNotEqual(original, replacement)
            self.assertNotEqual(recovery_token(original, 'reused-target'),
                                recovery_token(replacement, 'reused-target'))
        self.assertEqual(len(self.system.spawns), 2)

    def test_legacy_record_migrates_only_after_lock_and_live_identity_validation(self):
        with self.host() as host:
            host.launch_or_attach()
            record = json.loads(host.state_file.read_text())
            record.pop('recoverySeed')
            self.write_private(host.state_file, record)
            self.system.commands[100] = str(EXECUTABLE)
            with self.assertRaisesRegex(HostError, 'owned_command_mismatch'):
                host.recovery_seed()
            self.assertNotIn('recoverySeed', json.loads(host.state_file.read_text()))
            self.system.commands[100] = ' '.join(host._args(54321))
            seed = host.recovery_seed()
            self.assertRegex(seed, r'^[0-9a-f]{64}$')
        with self.assertRaisesRegex(HostError, 'manager_lock_required'):
            host.recovery_seed()
        with self.host() as replacement:
            self.assertEqual(replacement.recovery_seed(), seed)

    def test_invalid_recovery_seed_is_rejected_without_repair(self):
        with self.host() as host:
            host.launch_or_attach()
            record = json.loads(host.state_file.read_text())
            for value in (None, True, 5, '', 'f' * 63, 'f' * 65, 'F' * 64, 'g' * 64, []):
                with self.subTest(value=value):
                    record['recoverySeed'] = value
                    self.write_private(host.state_file, record)
                    before = host.state_file.read_bytes()
                    with self.assertRaisesRegex(HostError, 'invalid_daily_recovery_seed'):
                        host.recovery_seed()
                    self.assertEqual(host.state_file.read_bytes(), before)

    def run_resident_fixture(self, *, duration=3, launch_once=False, on_sleep=None, wait_for_exit=False):
        clock, stop = [0.0], [False]
        def sleep(seconds):
            clock[0] += seconds
            if on_sleep:
                on_sleep(clock[0], stop)
        renderer = MagicMock()
        renderer.sync.return_value = {'mounted': 1, 'visible': 1, 'unavailable': 0}
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        with patch('codex_bar.manager.DailyHost', side_effect=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.RendererCollection', return_value=renderer) as collections, \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.stop_signals') as signals, \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=sleep), patch('builtins.print') as output:
            signals.return_value.__enter__.return_value = stop
            run_daily(resident=True, duration=duration, launch_once=launch_once,
                      wait_for_exit=wait_for_exit)
        return SimpleNamespace(messages=[call.args[0] for call in output.call_args_list],
                               collections=collections, renderer=renderer, worker=worker)

    def test_resident_login_without_codex_never_spawns_or_queries_quota(self):
        result = self.run_resident_fixture()
        self.assertFalse(self.system.spawns)
        self.assertFalse(self.system.signals)
        self.assertEqual(self.system.signatures, 0)
        self.assertEqual(self.system.preflight_checks, 0)
        result.collections.assert_not_called()
        result.worker.submit.assert_not_called()
        self.assertEqual(result.messages, ['codex-usage-bar-resident:waiting'])

    def test_resident_observes_plain_app_then_stays_closed_when_user_quits(self):
        self.system.add_daily()
        def quit_plain(now, stop):
            if now >= 1:
                self.system.rows.clear()
                self.system.profile_owners.clear()
        # The legacy flag must not implicitly authorize a resident observer to launch.
        result = self.run_resident_fixture(on_sleep=quit_plain, wait_for_exit=True)
        self.assertFalse(self.system.spawns)
        self.assertFalse(self.system.signals)
        result.collections.assert_not_called()
        self.assertEqual(result.messages, ['codex-usage-bar-resident:needs-launcher',
                                           'codex-usage-bar-resident:waiting'])

    def test_resident_tracks_plain_app_open_and_close_without_ever_spawning(self):
        def user_actions(now, stop):
            if 1 <= now < 2 and 20 not in self.system.rows:
                self.system.add_daily()
            elif now >= 2:
                self.system.rows.clear()
                self.system.profile_owners.clear()
        result = self.run_resident_fixture(on_sleep=user_actions)
        self.assertFalse(self.system.spawns)
        self.assertFalse(self.system.signals)
        self.assertEqual(self.system.preflight_checks, 0)
        self.assertEqual(result.messages, ['codex-usage-bar-resident:waiting',
                                           'codex-usage-bar-resident:needs-launcher',
                                           'codex-usage-bar-resident:waiting'])

    def test_resident_launch_once_waits_for_plain_app_but_never_reopens_second_exit(self):
        self.system.add_daily()
        def quit_apps(now, stop):
            if now >= 1 and 20 in self.system.rows:
                self.system.rows.clear()
                self.system.profile_owners.clear()
            if now >= 2 and self.system.spawns:
                self.system.rows.clear()
                self.system.profile_owners.clear()
        result = self.run_resident_fixture(duration=4, launch_once=True, on_sleep=quit_apps)
        self.assertEqual(len(self.system.spawns), 1)
        self.assertFalse(self.system.rows)
        self.assertFalse(self.system.signals)
        self.assertEqual(result.collections.call_count, 1)
        self.assertIn('codex-usage-bar-resident:waiting-for-quit', result.messages)
        self.assertEqual(result.messages.count('codex-usage-bar-resident:attached'), 1)
        self.assertEqual(result.messages[-1], 'codex-usage-bar-resident:waiting')
        with self.host() as host:
            self.assertEqual(host.status()['status'], 'stopped')

    def test_resident_launch_once_opens_from_closed_then_respects_user_quit(self):
        def quit_managed(now, stop):
            if now >= .4:
                self.system.rows.clear()
                self.system.profile_owners.clear()
        result = self.run_resident_fixture(launch_once=True, on_sleep=quit_managed)
        self.assertEqual(len(self.system.spawns), 1)
        self.assertFalse(self.system.rows)
        self.assertEqual(result.collections.call_count, 1)
        self.assertEqual(result.messages[-1], 'codex-usage-bar-resident:waiting')

    def test_launch_once_consumed_by_existing_instance_and_manager_retry_cannot_reopen(self):
        with self.host() as previous:
            previous.launch_or_attach()
        def quit_managed(now, stop):
            if now >= .4:
                self.system.rows.clear()
                self.system.profile_owners.clear()
        result = self.run_resident_fixture(launch_once=True, on_sleep=quit_managed)
        self.assertEqual(len(self.system.spawns), 1)
        self.assertEqual(result.collections.call_count, 1)
        self.assertEqual(result.messages[-1], 'codex-usage-bar-resident:waiting')
        retry = self.run_resident_fixture()
        retry.collections.assert_not_called()
        self.assertEqual(len(self.system.spawns), 1)
        self.assertEqual(retry.messages, ['codex-usage-bar-resident:waiting'])

    def test_manager_crash_followed_by_codex_exit_does_not_restore_closed_app(self):
        with self.host() as previous:
            previous.launch_or_attach()
            self.assertEqual(previous._load()['status'], 'running')
        # No detach: emulate a crashed manager, then the user quits Codex.
        self.system.rows.clear()
        self.system.profile_owners.clear()
        result = self.run_resident_fixture()
        self.assertEqual(len(self.system.spawns), 1)
        self.assertFalse(self.system.rows)
        result.collections.assert_not_called()
        self.assertEqual(result.messages, ['codex-usage-bar-resident:waiting'])

    def test_resident_attaches_existing_generation_without_spawning(self):
        with self.host() as previous:
            previous.launch_or_attach()
            seed = previous.recovery_seed()
        stop = [False]
        renderer = MagicMock()
        renderer.sync.return_value = {'mounted': 1, 'visible': 1, 'unavailable': 0}
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        with patch('codex_bar.manager.DailyHost', side_effect=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.RendererCollection', return_value=renderer) as collection, \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.stop_signals') as signals, \
             patch('codex_bar.manager.time.sleep', side_effect=lambda _: stop.__setitem__(0, True)), \
             patch('builtins.print'):
            signals.return_value.__enter__.return_value = stop
            run_daily(resident=True)
        self.assertEqual(len(self.system.spawns), 1)
        self.assertEqual(collection.call_args.kwargs['recovery_seed'], seed)
        renderer.dispose.assert_called_once()

    def test_detach_leaves_app_and_port_and_allows_reattach(self):
        with self.host() as host:
            original = host.launch_or_attach()
            status = host.detach()
            self.assertEqual(status['status'], 'detached')
            self.assertTrue(status['appLeftRunning'])
            self.assertTrue(status['debugPortOpen'])
        with self.host() as replacement:
            attached = replacement.launch_or_attach()
            self.assertEqual(attached, original)
            self.assertTrue(replacement.is_running())
        self.assertEqual(len(self.system.spawns), 1)
        self.assertFalse(self.system.signals)

    def test_manual_quit_is_observed_without_signals(self):
        with self.host() as host:
            host.launch_or_attach()
            self.system.rows.clear()
            self.system.profile_owners.clear()
            self.assertFalse(host.is_running())
            self.assertFalse(host.any_alive())
            status = host.detach()
            self.assertEqual(status['status'], 'stopped')
            self.assertFalse(status['debugPortOpen'])
        self.assertFalse(self.system.signals)

    def test_liveness_allows_new_child_without_signals_or_recursive_profile_scan(self):
        with self.host() as host:
            host.launch_or_attach()
            checks = self.system.preflight_checks
            self.system.rows[102] = Process(102, 100, 100, 'new-worker', '/Applications/Codex.app/helper')
            self.assertTrue(host.is_running())
            host.validate()
            self.assertEqual(self.system.preflight_checks, checks)
        self.assertFalse(self.system.signals)

    def test_orphaned_child_prevents_new_spawn(self):
        with self.host() as host:
            host.launch_or_attach()
            del self.system.rows[100]
            self.system.profile_owners.remove(100)
            self.assertFalse(host.is_running())
            self.assertTrue(host.any_alive())
            with self.assertRaisesRegex(HostError, 'daily_instance_running'):
                host.launch_or_attach()
        self.assertEqual(len(self.system.spawns), 1)
        self.assertFalse(self.system.signals)

    def test_pid_reuse_never_authorizes_attachment(self):
        with self.host() as host:
            host.launch_or_attach()
            self.system.rows[100] = Process(100, 1, 100, 'new-unrelated-start', str(EXECUTABLE))
            self.assertFalse(host.is_running())
            with self.assertRaises(HostError):
                host.validate()
            with self.assertRaises(HostError):
                host.launch_or_attach()
        self.assertEqual(len(self.system.spawns), 1)
        self.assertFalse(self.system.signals)

    def test_changed_command_never_authorizes_attachment(self):
        with self.host() as host:
            host.launch_or_attach()
            self.system.commands[100] += ' --unrecognized-option'
            with self.assertRaisesRegex(HostError, 'owned_command_mismatch'):
                host.validate()

    def test_wrong_listener_owner_and_nonloopback_are_refused(self):
        with self.host() as host:
            host.launch_or_attach()
            for endpoint in ((999, '127.0.0.1:54321'), (101, '*:54321'), (101, '[::1]:54321')):
                self.system.endpoint_override = [endpoint]
                with self.assertRaisesRegex(HostError, 'debug_listener_not_owned_loopback'):
                    host.validate()
        self.assertFalse(self.system.signals)

    def test_unrecognized_second_daily_main_invalidates_scope(self):
        with self.host() as host:
            host.launch_or_attach()
            self.system.add_daily()
            with self.assertRaisesRegex(HostError, 'daily_instance_running'):
                host.validate()

    def test_timeout_leaves_app_running_and_recoverable(self):
        self.system.listener_enabled = False
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'owned_listener_start_timeout'):
                host.launch_or_attach(timeout=.2)
            self.assertEqual(host.last_launch_failure['status'], 'daily_launch_failed_app_not_stopped')
            self.assertTrue(host.last_launch_failure['debugPortMayRemain'])
            self.assertIn(100, self.system.rows)
            self.assertTrue(host.state_file.exists())
            self.system.listener_enabled = True
            host.launch_or_attach()
        self.assertEqual(len(self.system.spawns), 1)
        self.assertFalse(self.system.signals)

    def test_failed_state_write_does_not_terminate_app(self):
        with self.host() as host:
            with patch.object(host, '_save', side_effect=OSError('full')):
                with self.assertRaises(OSError):
                    host.launch_or_attach()
            self.assertTrue(host.last_launch_failure['appMayBeRunning'])
            self.assertIn(100, self.system.rows)
        self.assertFalse(self.system.signals)

    def test_stop_and_inherited_launch_are_disabled(self):
        with self.host() as host:
            for operation in (host.stop, host.launch, lambda: host._stop_state({}),
                              lambda: host._cleanup_failed_launch({}, 54321, RuntimeError())):
                with self.assertRaises(HostError):
                    operation()
        self.assertFalse(self.system.signals)

    def test_wrong_mode_state_cannot_grant_daily_authority(self):
        with self.host() as host:
            host.launch_or_attach()
            state = json.loads(host.state_file.read_text())
            state['mode'] = 'isolated'
            self.write_private(host.state_file, state)
            with self.assertRaisesRegex(HostError, 'invalid_daily_host_state'):
                host.launch_or_attach()

    def test_installed_identity_ignores_retired_private_login_binding(self):
        self.layout['resources'].mkdir(parents=True)
        self.write_private(self.layout['receipt'], {
            'version': 1, 'appPath': str(self.layout['app']),
            'resourceRoot': str(self.layout['resources']),
            'profileBinding': 'approved_previous', 'profileRoot': '/missing/private/profile'})
        with self.host(self.layout['resources']) as host:
            self.assertEqual(host.state_root, self.layout['support'])
            self.assertEqual(host.quota_home, self.quota)
            host.preflight()
        self.assertFalse(self.system.spawns)

    def test_installed_wrong_app_identity_is_refused(self):
        self.layout['resources'].mkdir(parents=True)
        self.write_private(self.layout['receipt'], {
            'version': 1, 'appPath': '/wrong/app',
            'resourceRoot': str(self.layout['resources'])})
        with self.assertRaisesRegex(HostError, 'installation_receipt_app_mismatch'):
            self.host(self.layout['resources'])

    def test_custom_profile_flag_alone_does_not_prove_isolation(self):
        self.system.add_daily()
        self.system.profile_owners.clear()
        self.system.commands[20] += ' --user-data-dir=/somewhere/private'
        with self.host() as host, self.assertRaisesRegex(HostError, 'daily_instance_running'):
            host.preflight()

    def test_verified_old_dedicated_instance_is_ignored(self):
        private = self.home / 'old-project/private-session'
        (private / 'electron-profile').mkdir(parents=True)
        main = Process(20, 1, 20, 'verified-isolated', str(EXECUTABLE))
        self.system.rows[20] = main
        self.system.commands[20] = str(EXECUTABLE) + ' --user-data-dir=' + str(private / 'electron-profile')
        self.system.opened[20] = [str(private / 'electron-profile/Preferences')]
        self.write_private(self.layout['support'] / 'host.json', {
            'version': 1, 'pid': 20, 'port': None, 'profileRoot': str(private),
            'initialPids': [], 'known': {'20': list(main.fingerprint)}})
        with self.host() as host:
            host.preflight()
            host.launch_or_attach()
        self.assertEqual(len(self.system.spawns), 1)
        self.assertIn(20, self.system.rows)
        self.assertFalse(self.system.signals)

    def test_recorded_isolated_app_opening_daily_profile_is_not_ignored(self):
        private = self.home / 'old-project/private-session'
        (private / 'electron-profile').mkdir(parents=True)
        main = Process(20, 1, 20, 'verified-isolated', str(EXECUTABLE))
        self.system.rows[20] = main
        self.system.commands[20] = str(EXECUTABLE) + ' --user-data-dir=' + str(private / 'electron-profile')
        self.system.opened[20] = [str(private / 'electron-profile/Preferences'), str(self.quota / 'config.toml')]
        self.write_private(self.layout['support'] / 'host.json', {
            'version': 1, 'pid': 20, 'port': None, 'profileRoot': str(private),
            'initialPids': [], 'known': {'20': list(main.fingerprint)}})
        with self.host() as host, self.assertRaisesRegex(HostError, 'daily_instance_running'):
            host.preflight()


if __name__ == '__main__':
    unittest.main()
