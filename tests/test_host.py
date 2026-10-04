"""Own lifecycle code tests only: no Codex executable or friend JavaScript runs."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codex_bar.host import (EXECUTABLE, HostError, ManagedHost, Process, System,
                            build_install_receipt, installation_paths, minimal_environment)


class FakeProcess:
    pid = 100

    def __init__(self, backend):
        self.backend = backend

    def poll(self):
        return None if self.pid in self.backend.rows else 0


class FakeSystem:
    def __init__(self):
        self.rows = {20: Process(20, 1, 20, 'daily-start', str(EXECUTABLE))}
        self.commands = {20: str(EXECUTABLE)}
        self.now = 0.0
        self.signatures = 0
        self.spawns = []
        self.signals = []
        self.direct_stops = []
        self.direct_exit_leaves_child = False
        self.opened = []
        self.profile_owners = set()
        self.endpoint_override = None
        self.listener_enabled = True
        self.debug = False

    def snapshot(self):
        return dict(self.rows)

    def command(self, pid):
        return self.commands[pid]

    def listeners(self, port=None, pids=None):
        if self.endpoint_override is not None:
            return list(self.endpoint_override)
        if self.debug and 101 in self.rows and self.listener_enabled:
            return [(101, '127.0.0.1:54321')]
        return []

    def open_files(self, pids):
        return list(self.opened)

    def profile_users(self, root):
        return set(self.profile_owners)

    def verify_signature(self):
        self.signatures += 1

    @contextmanager
    def reserve_port(self):
        yield 54321

    def spawn(self, args, env, cwd):
        self.spawns.append((args, env, cwd))
        self.debug = any('remote-debugging-port=' in item for item in args)
        self.rows[100] = Process(100, 10, 100, 'test-start', str(EXECUTABLE))
        self.rows[101] = Process(101, 100, 100, 'child-start', '/Applications/Codex.app/helper')
        self.commands[100] = ' '.join(args)
        return FakeProcess(self)

    def send_signal(self, pid, sig, *, group=False):
        self.signals.append((pid, sig, group))
        victims = {p for p, row in self.rows.items() if row.pgid == pid} if group else {pid}
        for victim in victims:
            self.rows.pop(victim, None)

    def stop_created_child(self, proc):
        if proc.poll() is None:
            self.direct_stops.append(proc.pid)
            self.rows.pop(proc.pid, None)
            # Simulate Electron's normal helper exit following its parent's exit.
            # The production fallback sends no signal to these unknown helpers.
            if not self.direct_exit_leaves_child:
                self.rows.pop(101, None)

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.system = FakeSystem()

    def tearDown(self):
        self.tmp.cleanup()

    def host(self):
        return ManagedHost(_system=self.system, _project_root=self.project)

    def test_import_and_lock_do_not_launch(self):
        with self.host() as host:
            self.assertEqual(host.status()['status'], 'not_created')
        self.assertEqual(self.system.spawns, [])

    def test_exclusive_lock_released_on_exit(self):
        with self.host():
            with self.assertRaisesRegex(HostError, 'another_manager'):
                with self.host():
                    pass
        with self.host():
            pass

    def test_private_permissions_and_no_profile_deletion(self):
        with self.host() as host:
            endpoint = host.launch()
            self.assertEqual(endpoint.port, 54321)
            self.assertEqual(endpoint.base_url, 'http://127.0.0.1:54321')
            for path in (host.state_root, host.profile_root, host.profile_root / 'codex-home',
                         host.profile_root / 'electron-profile', host.profile_root / 'working-directory'):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual(host.state_file.stat().st_mode & 0o777, 0o600)
            self.assertTrue(host.stop()['profileRetained'])
            self.assertTrue(host.profile_root.is_dir())
            self.assertEqual(host.stop()['status'], 'stopped')
        self.assertIn(20, self.system.rows)
        self.assertEqual(self.system.signals, [(100, signal.SIGTERM, True)])
        self.assertGreaterEqual(self.system.signatures, 6)

    def test_launch_flags_and_secret_free_environment(self):
        with patch.dict(os.environ, {'HOME': '/Users/example', 'OPENAI_API_KEY': 'do-not-pass',
                                     'BUILD_FLAVOR': 'development', 'NODE_OPTIONS': 'unsafe'}):
            with self.host() as host:
                host.launch()
                args, env, cwd = self.system.spawns[0]
                self.assertEqual(args[0], str(EXECUTABLE))
                self.assertIn('--remote-debugging-address=127.0.0.1', args)
                self.assertEqual(env['HOME'], '/Users/example')
                self.assertEqual(env['CODEX_HOME'], str(host.profile_root / 'codex-home'))
                for forbidden in ('OPENAI_API_KEY', 'BUILD_FLAVOR', 'NODE_OPTIONS'):
                    self.assertNotIn(forbidden, env)
                self.assertEqual(cwd, host.profile_root / 'working-directory')

    def test_login_has_no_debug_flags_or_listener(self):
        with self.host() as host:
            endpoint = host.launch(debug=False)
            self.assertIsNone(endpoint.port)
            self.assertFalse(any('debug' in arg for arg in self.system.spawns[0][0]))
            with self.assertRaisesRegex(HostError, 'debugging_not_enabled'):
                _ = endpoint.base_url

    def test_context_does_not_implicitly_stop_owned_login(self):
        with self.host() as host:
            host.launch(debug=False)
        self.assertIn(100, self.system.rows)
        self.assertEqual(self.system.signals, [])

    def test_attach_only_saved_owned_instance(self):
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'no_running_owned_instance'):
                host.attach()
            original = host.launch()
        with self.host() as host:
            self.assertEqual(host.attach(), original)
            self.assertEqual(len(self.system.spawns), 1)

    def test_reject_symlink_state(self):
        (self.project / '.state').symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(HostError, 'unsafe_state_directory'):
            with self.host():
                pass

    def test_reject_nonprivate_state(self):
        (self.project / '.state').mkdir(mode=0o755)
        # mkdir's mode is filtered by the caller's umask (daily launcher: 077).
        (self.project / '.state').chmod(0o755)
        with self.assertRaisesRegex(HostError, 'state_directory_not_private'):
            with self.host():
                pass

    def test_reject_profile_symlink(self):
        with self.host() as host:
            host.profile_root.symlink_to(self.project, target_is_directory=True)
            with self.assertRaisesRegex(HostError, 'unsafe_state_directory'):
                host.launch()
        self.assertEqual(self.system.spawns, [])

    def test_reject_state_tamper_daily_profile(self):
        with self.host() as host:
            host.launch()
            state = json.loads(host.state_file.read_text())
            state['profileRoot'] = '/Users/example/Library/Application Support/Codex'
            host.state_file.write_text(json.dumps(state))
            with self.assertRaisesRegex(HostError, 'state_profile_mismatch'):
                host.attach()
        self.assertEqual(self.system.signals, [])

    def test_reject_preexisting_pid_in_state(self):
        with self.host() as host:
            host.launch()
            state = json.loads(host.state_file.read_text())
            state['known']['20'] = ['daily-start', str(EXECUTABLE)]
            host.state_file.write_text(json.dumps(state))
            with self.assertRaisesRegex(HostError, 'preexisting_pid_in_owned_state'):
                host.stop()
        self.assertIn(20, self.system.rows)

    def test_pid_reuse_is_never_signalled(self):
        with self.host() as host:
            host.launch()
            self.system.rows.pop(101)
            self.system.rows[100] = Process(100, 1, 999, 'different-start', str(EXECUTABLE))
            host.stop()
        self.assertEqual(self.system.signals, [])
        self.assertIn(100, self.system.rows)

    def test_reject_daily_app_command_even_matching_fingerprint(self):
        with self.host() as host:
            host.launch()
            self.system.commands[100] = str(EXECUTABLE)
            with self.assertRaisesRegex(HostError, 'owned_command_mismatch'):
                host.stop()
        self.assertEqual(self.system.signals, [])

    def test_untracked_group_member_is_never_signalled_but_known_debugger_is_closed(self):
        with self.host() as host:
            host.launch()
            self.system.rows[102] = Process(102, 1, 100, 'unknown', '/usr/bin/other')
            with self.assertRaisesRegex(HostError, 'unverified_processes_remain_after_owned_cleanup'):
                host.stop()
        self.assertEqual(self.system.signals, [(100, signal.SIGTERM, False),
                                               (101, signal.SIGTERM, False)])
        self.assertIn(102, self.system.rows)
        self.assertIn(20, self.system.rows)
        self.assertNotIn(100, self.system.rows)
        self.assertNotIn(101, self.system.rows)

    def test_listener_child_born_between_ps_and_lsof_is_proven_on_bounded_refresh(self):
        with self.host() as host:
            host.launch()
            observations = 0
            def new_listener(port=None, pids=None):
                nonlocal observations
                observations += 1
                self.system.rows[102] = Process(102, 100, 100, 'new-child', '/Applications/Codex.app/helper')
                return [(102, '127.0.0.1:54321')]
            self.system.listeners = new_listener
            host.validate()
            self.assertEqual(observations, 2)
            self.assertIn('102', json.loads(host.state_file.read_text())['known'])

    def test_foreign_new_listener_is_not_accepted_by_refresh(self):
        with self.host() as host:
            host.launch()
            observations = 0
            def foreign_listener(port=None, pids=None):
                nonlocal observations
                observations += 1
                self.system.rows[404] = Process(404, 1, 404, 'foreign', '/usr/bin/other')
                return [(404, '127.0.0.1:54321')]
            self.system.listeners = foreign_listener
            with self.assertRaisesRegex(HostError, 'debug_listener_not_owned_loopback'):
                host.validate()
            self.assertEqual(observations, 3)
            self.assertNotIn('404', json.loads(host.state_file.read_text())['known'])

    def test_reparented_unobserved_listener_is_not_adopted_by_group(self):
        with self.host() as host:
            host.launch()
            def orphan_listener(port=None, pids=None):
                self.system.rows[102] = Process(102, 1, 100, 'orphan', '/Applications/Codex.app/helper')
                return [(102, '127.0.0.1:54321')]
            self.system.listeners = orphan_listener
            with self.assertRaisesRegex(HostError, 'unverified_process_in_owned_group'):
                host.validate()
            self.assertNotIn('102', json.loads(host.state_file.read_text())['known'])

    def test_child_born_between_cleanup_snapshots_is_proven_before_group_signal(self):
        with self.host() as host:
            host.launch()
            original = self.system.snapshot
            observations = 0
            def growing_tree():
                nonlocal observations
                observations += 1
                if observations == 2:
                    self.system.rows[102] = Process(102, 100, 100, 'new-child', '/Applications/Codex.app/helper')
                return original()
            self.system.snapshot = growing_tree
            self.assertEqual(host.stop()['status'], 'stopped')
        self.assertEqual(self.system.signals, [(100, signal.SIGTERM, True)])
        self.assertIn(20, self.system.rows)
        self.assertNotIn(102, self.system.rows)

    def test_snapshot_parser_records_macos_zombie_state_without_executing_ps(self):
        output = ('100 1 100 Mon Oct 5 00:13:34 2026 S /Applications/Codex.app/Contents/MacOS/ChatGPT\n'
                  '101 100 100 Mon Oct 5 00:13:35 2026 Z (codex)\n')
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr='')
        with patch('codex_bar.host.subprocess.run', return_value=result) as run:
            rows = System().snapshot()
        self.assertTrue(rows[100].is_alive)
        self.assertFalse(rows[101].is_alive)
        self.assertEqual(rows[101].state, 'Z')
        self.assertIn('state=', run.call_args.args[0][-1])
        self.assertTrue(Process(2, 1, 2, 'start', 'exe', 'T').is_alive)
        self.assertTrue(Process(3, 1, 3, 'start', 'exe', 'SE').is_alive)

    def test_tracked_child_becoming_zombie_is_not_signalled_or_reported_unknown(self):
        with self.host() as host:
            host.launch()
            self.system.rows[101] = Process(101, 100, 100, 'child-start', '(helper)', 'Z')
            self.system.listener_enabled = False
            self.assertEqual(host.stop()['status'], 'stopped')
        self.assertEqual(self.system.signals, [(100, signal.SIGTERM, False)])
        self.assertIn(101, self.system.rows)  # Kernel/parent reap is outside our authority.
        self.assertIn(20, self.system.rows)

    def test_unknown_zombie_does_not_block_guard_or_receive_cleanup_signal(self):
        with self.host() as host:
            host.launch()
            self.system.rows[102] = Process(102, 1, 100, 'unknown', '(ditto)', 'Z')
            host.validate()
            self.assertEqual(host.stop()['status'], 'stopped')
        self.assertEqual(self.system.signals, [(100, signal.SIGTERM, False),
                                               (101, signal.SIGTERM, False)])
        self.assertIn(102, self.system.rows)

    def test_unknown_live_member_may_exit_during_bounded_settle_without_being_signalled(self):
        with self.host() as host:
            host.launch()
            self.system.rows[102] = Process(102, 1, 100, 'unknown', '/usr/bin/other', 'SE')
            original_sleep = self.system.sleep
            def normal_exit(seconds):
                original_sleep(seconds)
                if self.system.now >= 0.2:
                    self.system.rows.pop(102, None)
            self.system.sleep = normal_exit
            self.assertEqual(host.stop()['status'], 'stopped')
        self.assertGreaterEqual(self.system.now, 0.2)
        self.assertLessEqual(self.system.now, 3.0)
        self.assertTrue(all(pid != 102 and not group for pid, _, group in self.system.signals))

    def test_unknown_live_member_outlasting_settle_is_still_refused(self):
        with self.host() as host:
            host.launch()
            self.system.rows[102] = Process(102, 1, 100, 'unknown', '/usr/bin/other', 'S')
            with self.assertRaisesRegex(HostError, 'unverified_processes_remain_after_owned_cleanup'):
                host.stop()
        self.assertGreaterEqual(self.system.now, 3.0)
        self.assertTrue(all(pid != 102 and not group for pid, _, group in self.system.signals))

    def test_tracked_orphan_can_be_cleaned_but_not_attached(self):
        with self.host() as host:
            host.launch()
            self.system.rows.pop(100)
            self.system.rows[101] = Process(101, 1, 888, 'child-start', '/Applications/Codex.app/helper')
            self.assertEqual(host.status()['status'], 'orphaned')
            with self.assertRaisesRegex(HostError, 'owned_main_process_not_running'):
                host.attach()
            with self.assertRaisesRegex(HostError, 'orphan_already_running'):
                host.launch()
            host.stop()
        self.assertEqual(self.system.signals, [(101, signal.SIGTERM, False)])

    def test_unrecorded_private_profile_orphan_refuses_new_launch(self):
        self.system.profile_owners = {555}
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'private_profile_already_in_use'):
                host.launch()
        self.assertEqual(self.system.spawns, [])

    def test_non_loopback_or_foreign_listener_refused(self):
        for endpoints in ([(101, '*:54321')], [(20, '127.0.0.1:54321')]):
            with self.subTest(endpoints=endpoints):
                with self.host() as host:
                    if not self.system.spawns:
                        host.launch()
                    self.system.endpoint_override = endpoints
                    with self.assertRaisesRegex(HostError, 'debug_listener_not_owned_loopback'):
                        host.validate()
                    self.system.endpoint_override = None

    def test_daily_profile_open_path_refused(self):
        with self.host() as host:
            host.launch()
            self.system.opened = [str(Path.home() / '.codex/auth.json')]
            with self.assertRaisesRegex(HostError, 'daily_profile_opened'):
                host.validate()

    def test_signature_failure_blocks_spawn(self):
        def invalid():
            raise HostError('signature_verification_failed')
        self.system.verify_signature = invalid
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'signature_verification_failed'):
                host.launch()
        self.assertEqual(self.system.spawns, [])

    def test_signature_failure_does_not_leave_owned_debugger_running(self):
        with self.host() as host:
            host.launch()
            def invalid():
                raise HostError('signature_verification_failed')
            self.system.verify_signature = invalid
            with self.assertRaisesRegex(HostError, 'owned_instance_stopped'):
                host.stop()
            self.assertEqual(host.status()['status'], 'stopped')
        self.assertNotIn(100, self.system.rows)
        self.assertIn(20, self.system.rows)

    def test_symlink_state_file_refused(self):
        with self.host() as host:
            host.state_file.symlink_to(self.project / 'outside.json')
            with self.assertRaisesRegex(HostError, 'unsafe_state_file'):
                host.status()

    def test_replaced_lock_fails_closed(self):
        with self.host() as host:
            lock_path = host.state_root / 'manager.lock'
            lock_path.rename(host.state_root / 'old.lock')
            lock_path.touch(mode=0o600)
            with self.assertRaisesRegex(HostError, 'manager_lock_identity_changed'):
                host.launch()
        self.assertEqual(self.system.spawns, [])

    def test_stale_state_recovery_does_not_signal_old_pids(self):
        with self.host() as host:
            host.launch()
            self.system.rows.pop(100)
            self.system.rows.pop(101)
            host.launch()
        self.assertEqual(len(self.system.spawns), 2)
        self.assertEqual(self.system.signals, [])

    def test_crashed_atomic_write_temp_does_not_block(self):
        with self.host() as host:
            (host.state_root / '.host-old.tmp').write_text('unused')
            host.launch()
            host.stop()
            self.assertTrue((host.state_root / '.host-old.tmp').exists())

    def test_listener_timeout_cleans_verified_owned_processes(self):
        self.system.listener_enabled = False
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'owned_listener_start_timeout'):
                host.launch(timeout=0.2)
            self.assertEqual(host.status()['status'], 'stopped')
        self.assertIn(20, self.system.rows)
        self.assertNotIn(100, self.system.rows)

    def test_postspawn_snapshot_failure_cleans_direct_popen_child(self):
        original = self.system.snapshot
        failed = False
        def fail_first_postspawn():
            nonlocal failed
            if self.system.spawns and not failed:
                failed = True
                raise HostError('process_inspection_failed')
            return original()
        self.system.snapshot = fail_first_postspawn
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'process_inspection_failed'):
                host.launch()
            self.assertEqual(host.last_launch_failure['status'], 'startup_failed_cleanup_verified')
            self.assertFalse(host.state_file.exists())
        self.assertEqual(self.system.direct_stops, [100])
        self.assertEqual(self.system.signals, [])
        self.assertNotIn(100, self.system.rows)
        self.assertIn(20, self.system.rows)

    def test_direct_popen_fallback_does_not_signal_already_reaped_child(self):
        proc = Mock()
        proc.poll.return_value = 0
        proc.wait.return_value = 0
        System().stop_created_child(proc)
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
        proc.wait.assert_called_once_with(timeout=3)

    def test_direct_popen_fallback_escalates_only_that_live_child(self):
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired('owned-child', 3), 0]
        System().stop_created_child(proc)
        proc.terminate.assert_called_once_with()
        proc.kill.assert_called_once_with()
        self.assertEqual(proc.wait.call_count, 2)

    def test_direct_popen_fallback_reports_failed_reap(self):
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired('owned-child', 1)
        with self.assertRaisesRegex(HostError, 'created_child_cleanup_failed'):
            System().stop_created_child(proc)

    def test_first_state_save_failure_cleans_using_in_memory_identity(self):
        with self.host() as host:
            original = host._save
            def first_write_fails(state):
                host._save = original
                raise OSError('synthetic write failure')
            host._save = first_write_fails
            with self.assertRaisesRegex(OSError, 'synthetic write failure'):
                host.launch()
            self.assertEqual(host.status()['status'], 'stopped')
            self.assertEqual(host.last_launch_failure['status'], 'startup_failed_cleanup_verified')
        self.assertEqual(self.system.signals, [(100, signal.SIGTERM, True)])
        self.assertNotIn(100, self.system.rows)
        self.assertIn(20, self.system.rows)

    def test_all_state_writes_fail_but_do_not_strand_child(self):
        with self.host() as host:
            def every_write_fails(state):
                raise OSError('synthetic disk full')
            host._save = every_write_fails
            with self.assertRaisesRegex(OSError, 'synthetic disk full'):
                host.launch()
            self.assertEqual(host.last_launch_failure['status'], 'startup_failed_cleanup_verified')
            self.assertFalse(host.state_file.exists())
        self.assertNotIn(100, self.system.rows)
        self.assertEqual(self.system.signals, [(100, signal.SIGTERM, True)])

    def test_unverified_postspawn_orphan_is_retained_and_reported_without_group_signal(self):
        original = self.system.snapshot
        failed = False
        def fail_first_postspawn():
            nonlocal failed
            if self.system.spawns and not failed:
                failed = True
                raise HostError('process_inspection_failed')
            return original()
        self.system.snapshot = fail_first_postspawn
        self.system.direct_exit_leaves_child = True
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'launch_failed_cleanup_unverified_recovery_saved'):
                host.launch()
            recovery = host.state_root / 'launch-failure.json'
            self.assertEqual(recovery.stat().st_mode & 0o777, 0o600)
            evidence = json.loads(recovery.read_text())
            self.assertEqual(evidence['status'], 'cleanup_unverified')
            self.assertFalse(evidence['unverifiedProcessGroupsSignalled'])
            self.assertTrue(host.profile_root.is_dir())
            self.assertEqual(host.status()['status'], 'cleanup_unverified')
            self.assertTrue(host.status()['profileRetained'])
            with self.assertRaisesRegex(HostError, 'missing_host_state_cleanup_unverified'):
                host.stop()
            with self.assertRaisesRegex(HostError, 'missing_host_state_cleanup_unverified'):
                host.launch()
        self.assertEqual(self.system.direct_stops, [100])
        self.assertEqual(self.system.signals, [])
        self.assertIn(101, self.system.rows)
        self.assertIn(20, self.system.rows)

    def test_missing_state_with_unrecorded_profile_user_refuses_cleanup_success(self):
        with self.host() as host:
            host._prepare_profile()
            self.system.profile_owners = {777}
            status = host.status()
            self.assertEqual(status['status'], 'cleanup_unverified')
            self.assertTrue(status['profileRetained'])
            with self.assertRaisesRegex(HostError, 'missing_host_state_cleanup_unverified'):
                host.stop()
        self.assertEqual(self.system.signals, [])

    def test_missing_state_retained_idle_profile_reported_accurately(self):
        with self.host() as host:
            host._prepare_profile()
            self.assertTrue(host.status()['profileRetained'])
            self.assertTrue(host.stop()['profileRetained'])

    def test_operations_require_lock(self):
        with self.assertRaisesRegex(HostError, 'manager_lock_required'):
            self.host().launch()

    def test_environment_missing_home_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(HostError, 'home_environment_missing'):
                minimal_environment(self.project)


class ApprovedProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.project = root / 'codex-usage-bar-macos'
        self.project.mkdir()
        self.previous = root / 'codex-usage-pet'
        self.previous.mkdir()
        (self.previous / '.build').mkdir()
        self.base = self.previous / '.build/private-login'
        self.base.mkdir(mode=0o700)
        self.profile = self.base / 'session-test1234'
        self.profile.mkdir(mode=0o700)
        for name in ('codex-home', 'electron-profile', 'working-directory'):
            (self.profile / name).mkdir(mode=0o700)
        self.manifest = self.base / 'active-session.json'
        self.manifest.touch(mode=0o600)
        self.record = {'status': 'debug_test_closed', 'debugCleanupVerified': True,
                       'profileRoot': str(self.profile), 'manifestPath': str(self.manifest)}
        self.save()
        self.system = FakeSystem()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        self.manifest.write_text(json.dumps(self.record))

    def host(self):
        return ManagedHost(approved_previous_profile=True,
                           _project_root=self.project, _system=self.system)

    def test_explicit_binding_uses_previous_profile_without_copying_or_editing_manifest(self):
        original = self.manifest.read_bytes()
        with self.host() as host:
            self.assertEqual(host.profile_root, self.profile)
            self.assertEqual(host.state_root, self.project / '.state')
            endpoint = host.launch()
            self.assertEqual(endpoint.profile_root, self.profile)
            self.assertEqual(self.system.spawns[0][1]['CODEX_HOME'], str(self.profile / 'codex-home'))
            self.assertFalse((host.state_root / 'private-session').exists())
            host.stop()
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertTrue(self.profile.is_dir())

    def test_convenience_factory_has_same_fixed_binding(self):
        host = ManagedHost.reuse_approved_profile(_project_root=self.project, _system=self.system)
        self.assertEqual(host.profile_root, self.profile)

    def test_default_mode_cannot_attach_previously_bound_profile(self):
        with self.host() as host:
            host.launch()
        with ManagedHost(_project_root=self.project, _system=self.system) as default:
            with self.assertRaisesRegex(HostError, 'state_profile_mismatch'):
                default.attach()

    def test_arbitrary_profile_path_is_refused(self):
        self.record['profileRoot'] = str(self.project)
        self.save()
        with self.assertRaisesRegex(HostError, 'approved_profile_path_invalid'):
            self.host()

    def test_traversal_and_unexpected_session_name_are_refused(self):
        for value in (str(self.base) + '/../session-test1234', str(self.base / 'daily-profile')):
            self.record['profileRoot'] = value
            self.save()
            with self.assertRaisesRegex(HostError, 'approved_profile_path_invalid'):
                self.host()

    def test_open_or_unverified_debugger_manifest_is_refused(self):
        for status, verified in (('ready_for_user_login', True), ('debug_test_closed', False),
                                 ('debug_test_closed', 1)):
            self.record.update(status=status, debugCleanupVerified=verified)
            self.save()
            with self.assertRaisesRegex(HostError, 'approved_profile_previous_debugger_not_closed'):
                self.host()

    def test_manifest_symlink_is_refused(self):
        original = self.base / 'original.json'
        self.manifest.rename(original)
        self.manifest.symlink_to(original)
        with self.assertRaisesRegex(HostError, 'approved_profile_manifest_unavailable'):
            self.host()

    def test_profile_symlink_is_refused(self):
        original = self.base / 'session-else1234'
        self.profile.rename(original)
        self.profile.symlink_to(original, target_is_directory=True)
        with self.assertRaisesRegex(HostError, 'unsafe_approved_profile_directory'):
            self.host()

    def test_ancestor_symlink_is_refused(self):
        original = self.previous.parent / 'other-project'
        self.previous.rename(original)
        self.previous.symlink_to(original, target_is_directory=True)
        with self.assertRaisesRegex(HostError, 'unsafe_approved_profile_directory'):
            self.host()

    def test_nonprivate_profile_or_manifest_is_refused(self):
        self.profile.chmod(0o755)
        with self.assertRaisesRegex(HostError, 'unsafe_approved_profile_directory'):
            self.host()
        self.profile.chmod(0o700)
        self.manifest.chmod(0o644)
        with self.assertRaisesRegex(HostError, 'unsafe_state_file'):
            self.host()

    def test_missing_subdirectory_is_not_created(self):
        (self.profile / 'codex-home').rmdir()
        with self.assertRaisesRegex(HostError, 'approved_profile_directory_missing'):
            self.host()
        self.assertFalse((self.profile / 'codex-home').exists())

    def test_live_existing_profile_is_not_started_again(self):
        self.system.profile_owners = {999}
        with self.host() as host:
            with self.assertRaisesRegex(HostError, 'missing_host_state_cleanup_unverified'):
                host.launch()
        self.assertEqual(self.system.spawns, [])


class InstalledHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve() / 'account'
        self.home.mkdir(mode=0o700)
        self.source = self.home / 'Documents/codex-usage-bar'
        self.source.mkdir(parents=True)
        self.app = self.home / 'Applications/codex-usage-bar.app'
        self.resources = self.app / 'Contents/Resources/codex-usage-bar'
        self.resources.mkdir(parents=True)
        self.support = self.home / 'Library/Application Support/codex-usage-bar'
        self.support.mkdir(parents=True, mode=0o700)
        self.receipt_path = self.support / 'install.json'
        self.receipt_path.touch(mode=0o600)
        self.profile_base = self.source.parent / 'codex-usage-pet/.build/private-login'
        self.profile_base.mkdir(parents=True, mode=0o700)
        self.previous_profile = self.profile_base / 'session-test1234'
        self.previous_profile.mkdir(mode=0o700)
        for name in ('codex-home', 'electron-profile', 'working-directory'):
            (self.previous_profile / name).mkdir(mode=0o700)
        self.previous_manifest = self.profile_base / 'active-session.json'
        self.previous_manifest.touch(mode=0o600)
        self.previous_manifest.write_text(json.dumps({
            'status': 'debug_test_closed', 'debugCleanupVerified': True,
            'profileRoot': str(self.previous_profile), 'manifestPath': str(self.previous_manifest)}))
        self.pwd_patch = patch('codex_bar.host.pwd.getpwuid', return_value=SimpleNamespace(pw_dir=str(self.home)))
        self.pwd_patch.start()
        self.system = FakeSystem()
        self.receipt = {'version': 1, 'appPath': str(self.app), 'resourceRoot': str(self.resources),
                        'sourceRoot': str(self.source), 'profileBinding': 'fresh',
                        'profileRoot': str(self.support / 'private-session'), 'approvedManifestPath': None}
        self.save()

    def tearDown(self):
        self.pwd_patch.stop()
        self.tmp.cleanup()

    def save(self):
        self.receipt_path.write_text(json.dumps(self.receipt))

    def use_approved(self):
        self.receipt.update(profileBinding='approved_previous', profileRoot=str(self.previous_profile),
                            approvedManifestPath=str(self.previous_manifest))
        self.save()

    def host(self):
        return ManagedHost(_project_root=self.resources, _system=self.system)

    def test_installed_fresh_mode_uses_fixed_support_and_no_resource_state(self):
        with self.host() as host:
            self.assertTrue(host.installed)
            self.assertEqual(host.state_root, self.support)
            self.assertEqual(host.profile_root, self.support / 'private-session')
            host.launch()
            host.stop()
        self.assertFalse((self.resources / '.state').exists())

    def test_installed_receipt_auto_selects_approved_binding_without_cli_path(self):
        self.use_approved()
        original = self.previous_manifest.read_bytes()
        with self.host() as host:
            self.assertTrue(host.approved_previous_profile)
            self.assertEqual(host.profile_root, self.previous_profile)
            host.launch()
            host.stop()
        self.assertEqual(self.previous_manifest.read_bytes(), original)
        self.assertFalse((self.support / 'private-session').exists())

    def test_account_home_comes_from_pwd_not_environment(self):
        with patch.dict(os.environ, {'HOME': '/tmp/ignored-home-override'}):
            self.assertEqual(installation_paths()['home'], self.home)
            self.assertEqual(self.host().state_root, self.support)

    def test_fresh_installed_app_remains_usable_after_source_is_removed(self):
        self.source.rmdir()
        with self.host() as host:
            self.assertEqual(host.profile_root, self.support / 'private-session')

    def test_uninstalled_source_ignores_receipt_and_keeps_local_state(self):
        self.receipt_path.write_text('invalid installed receipt')
        with ManagedHost(_project_root=self.source, _system=self.system) as host:
            self.assertFalse(host.installed)
            self.assertEqual(host.state_root, self.source / '.state')

    def test_app_bundle_in_wrong_location_does_not_silently_create_source_state(self):
        misplaced = self.home / 'Downloads/codex-usage-bar.app/Contents/Resources/codex-usage-bar'
        misplaced.mkdir(parents=True)
        with self.assertRaisesRegex(HostError, 'app_not_installed_at_expected_path'):
            ManagedHost(_project_root=misplaced, _system=self.system)
        self.assertFalse((misplaced / '.state').exists())

    def test_mismatched_app_and_resource_receipts_are_refused(self):
        for field in ('appPath', 'resourceRoot'):
            original = self.receipt[field]
            self.receipt[field] = str(self.source)
            self.save()
            with self.assertRaisesRegex(HostError, 'installation_receipt_app_mismatch'):
                self.host()
            self.receipt[field] = original

    def test_daily_cli_or_gui_profile_binding_is_refused(self):
        self.use_approved()
        for path in (self.home / '.codex', self.home / '.codex/subdir',
                     self.home / 'Library/Application Support/Codex'):
            self.receipt['profileRoot'] = str(path)
            self.save()
            with self.assertRaisesRegex(HostError, 'daily_profile_binding_forbidden'):
                self.host()

    def test_approved_receipt_cannot_swap_manifest_or_profile(self):
        self.use_approved()
        self.receipt['approvedManifestPath'] = str(self.source / 'active-session.json')
        self.save()
        with self.assertRaisesRegex(HostError, 'installation_approved_manifest_mismatch'):
            self.host()
        self.receipt['approvedManifestPath'] = str(self.previous_manifest)
        self.receipt['profileRoot'] = str(self.profile_base / 'session-other123')
        self.save()
        with self.assertRaisesRegex(HostError, 'installation_approved_profile_mismatch'):
            self.host()

    def test_approved_receipt_revalidates_original_closed_status(self):
        self.use_approved()
        record = json.loads(self.previous_manifest.read_text())
        record['status'] = 'running'
        self.previous_manifest.write_text(json.dumps(record))
        with self.assertRaisesRegex(HostError, 'approved_profile_previous_debugger_not_closed'):
            self.host()

    def test_forged_fresh_path_and_binding_are_refused(self):
        self.receipt['profileRoot'] = str(self.previous_profile)
        self.save()
        with self.assertRaisesRegex(HostError, 'installation_fresh_profile_mismatch'):
            self.host()
        self.receipt['profileBinding'] = 'arbitrary'
        self.save()
        with self.assertRaisesRegex(HostError, 'installation_profile_binding_invalid'):
            self.host()

    def test_receipt_wrong_version_mode_and_symlink_are_refused(self):
        self.receipt['version'] = True
        self.save()
        with self.assertRaisesRegex(HostError, 'invalid_installation_receipt'):
            self.host()
        self.receipt['version'] = 1
        self.save()
        self.receipt_path.chmod(0o644)
        with self.assertRaisesRegex(HostError, 'unsafe_state_file'):
            self.host()
        self.receipt_path.chmod(0o600)
        target = self.support / 'copied-receipt.json'
        self.receipt_path.rename(target)
        self.receipt_path.symlink_to(target)
        with self.assertRaisesRegex(HostError, 'installation_receipt_unavailable'):
            self.host()

    def test_symlink_installed_resource_path_is_refused(self):
        target = self.resources.parent / 'other-resources'
        self.resources.rename(target)
        self.resources.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(HostError, 'installed_resource_path_mismatch'):
            self.host()

    def test_symlink_approved_ancestor_is_refused(self):
        self.use_approved()
        target = self.profile_base.parent / 'other-login'
        self.profile_base.rename(target)
        self.profile_base.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(HostError, 'unsafe_approved_profile_directory'):
            self.host()

    def test_support_permissions_and_foreign_source_are_refused(self):
        self.support.chmod(0o755)
        with self.assertRaisesRegex(HostError, 'unsafe_approved_profile_directory'):
            self.host()
        self.support.chmod(0o700)
        self.receipt['sourceRoot'] = '/tmp/foreign-source'
        self.save()
        with self.assertRaisesRegex(HostError, 'installation_source_outside_account_home'):
            self.host()

    def test_read_only_receipt_builder_validates_reuse_and_never_creates_state(self):
        original = self.previous_manifest.read_bytes()
        with patch('codex_bar.host.PROJECT_ROOT', self.source), \
             patch('codex_bar.host.System.profile_users', return_value=set()) as users:
            receipt = build_install_receipt(reuse_approved_profile=True)
        users.assert_called_once_with(self.previous_profile)
        self.assertEqual(receipt['profileRoot'], str(self.previous_profile))
        self.assertEqual(receipt['approvedManifestPath'], str(self.previous_manifest))
        self.assertFalse((self.source / '.state').exists())
        self.assertEqual(self.previous_manifest.read_bytes(), original)

    def test_receipt_builder_refuses_live_reused_profile(self):
        with patch('codex_bar.host.PROJECT_ROOT', self.source), \
             patch('codex_bar.host.System.profile_users', return_value={999}):
            with self.assertRaisesRegex(HostError, 'approved_install_profile_in_use'):
                build_install_receipt(reuse_approved_profile=True)

    def test_fresh_receipt_builder_has_no_manifest_dependency(self):
        self.previous_manifest.unlink()
        with patch('codex_bar.host.PROJECT_ROOT', self.source), \
             patch('codex_bar.host.System.profile_users') as users:
            receipt = build_install_receipt()
        users.assert_not_called()
        self.assertEqual(receipt['profileRoot'], str(self.support / 'private-session'))
        self.assertIsNone(receipt['approvedManifestPath'])


if __name__ == '__main__':
    unittest.main()
