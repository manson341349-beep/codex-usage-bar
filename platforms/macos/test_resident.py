"""Offline resident fixtures: no real launchctl call, app launch or login changes."""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MACOS = Path(__file__).resolve().parent
sys.path.insert(0, str(MACOS))
import build
import install
import resident


class FakeLaunchctl:
    def __init__(self):
        self.calls = []
        self.job = None
        self.failures = {}
        self.others = {'com.example.keep': {'running': True}}
        self.agent = None

    def __call__(self, command, **options):
        self.calls.append((command, options))
        operation = command[1]
        code = self.failures.get(operation, 0)
        if isinstance(code, Exception):
            raise code
        if code:
            return subprocess.CompletedProcess(command, code, '', 'PRIVATE DIAGNOSTIC')
        if operation == 'print':
            if self.job is None:
                return subprocess.CompletedProcess(command, resident.SERVICE_NOT_FOUND, '', '')
            return subprocess.CompletedProcess(command, 0, self.job, '')
        if operation in ('bootstrap', 'kickstart'):
            self.job = self.owned_job()
        elif operation == 'bootout':
            self.job = None
        return subprocess.CompletedProcess(command, 0, '', '')

    def owned_job(self, *, running=True):
        agent = self.agent
        return (agent.target + ' = {\n'
                '\tpath = ' + str(agent.plist) + '\n'
                '\tstate = ' + ('running' if running else 'not running') + '\n'
                '\tprogram = ' + str(agent.executable) + '\n'
                '\targuments = {\n\t\t' + str(agent.executable) + '\n'
                '\t\t--resident\n\t}\n'
                '\tenvironment = {\n\t\tPRIVATE_TOKEN => DO NOT PRINT\n\t}\n}\n')


class ResidentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='resident-offline-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve() / 'account with spaces'
        self.home.mkdir(mode=0o700)
        self.runner = FakeLaunchctl()
        self.agent = resident.ResidentAgent(_home=self.home, _runner=self.runner)
        self.runner.agent = self.agent
        self.make_app()

    def make_app(self):
        app = self.agent.app
        (app / 'Contents/MacOS').mkdir(parents=True)
        (app / 'Contents/Resources').mkdir()
        self.agent.executable.write_bytes(b'\xcf\xfa\xed\xfeOFFLINE FIXTURE NOT EXECUTABLE')
        self.agent.executable.chmod(0o755)
        (app / 'Contents/Resources' / build.MARKER_NAME).write_text(json.dumps(build.MARKER))
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': build.BUNDLE_ID, 'CFBundleExecutable': build.PRODUCT}))

    def write_registration(self):
        self.agent.directories(create=True)
        self.agent.plist.write_bytes(plistlib.dumps(self.agent.definition()))
        self.agent.plist.chmod(0o600)

    def operations(self):
        return [command[1] for command, _ in self.runner.calls]

    def test_enable_serializes_fixed_safe_login_agent_and_observes_loaded_job(self):
        result = self.agent.enable()
        self.assertEqual(result, {'label': resident.LABEL, 'registered': True,
                                  'loaded': True, 'running': True})
        record = plistlib.loads(self.agent.plist.read_bytes())
        self.assertEqual(record, self.agent.definition())
        self.assertEqual(record['ProgramArguments'], [str(self.agent.executable), '--resident'])
        self.assertEqual(record['KeepAlive'], {'SuccessfulExit': False})
        self.assertIs(record['RunAtLoad'], True)
        self.assertEqual(record['ThrottleInterval'], 10)
        self.assertEqual(stat.S_IMODE(self.agent.plist.stat().st_mode), 0o600)
        self.assertEqual(self.operations(), ['print', 'enable', 'bootstrap', 'print'])
        self.assertEqual(self.runner.calls[2][0], [resident.LAUNCHCTL, 'bootstrap',
                                                self.agent.domain, str(self.agent.plist)])
        for command, options in self.runner.calls:
            self.assertEqual(command[0], '/bin/launchctl')
            self.assertNotIn('shell', options)
            self.assertEqual(options['timeout'], 15)
            self.assertEqual(options['env'], {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'})
        self.assertNotIn('Codex.app', self.agent.plist.read_text())
        self.assertNotIn('EnvironmentVariables', record)

    def test_existing_owned_registration_is_not_overwritten_or_force_restarted(self):
        self.write_registration()
        old_inode = self.agent.plist.stat().st_ino
        self.runner.job = self.runner.owned_job()
        self.agent.enable()
        self.assertEqual(old_inode, self.agent.plist.stat().st_ino)
        self.assertEqual(self.operations(), ['print', 'enable', 'kickstart', 'print'])
        self.assertEqual(self.runner.calls[2][0], [resident.LAUNCHCTL, 'kickstart', self.agent.target])
        self.assertNotIn('bootout', self.operations())

    def test_clean_exit_stays_stopped_until_explicit_enable(self):
        self.write_registration()
        self.runner.job = self.runner.owned_job(running=False)
        result = self.agent.status()
        self.assertTrue(result['loaded'])
        self.assertFalse(result['running'])
        self.assertEqual(self.operations(), ['print'])
        self.assertTrue(self.agent.enable()['running'])

    def test_status_absent_is_read_only(self):
        result = self.agent.status()
        self.assertFalse(result['registered'])
        self.assertFalse(result['loaded'])
        self.assertFalse((self.home / 'Library').exists())
        self.assertEqual(self.operations(), ['print'])

    def test_existing_unloaded_registration_is_bootstrapped(self):
        self.write_registration()
        self.agent.enable()
        self.assertIn('bootstrap', self.operations())
        self.assertNotIn('kickstart', self.operations())

    def test_bootstrap_failure_is_visible_and_can_be_retried(self):
        self.runner.failures['bootstrap'] = 5
        with self.assertRaisesRegex(resident.ResidentError, '^launchctl_bootstrap_failed$'):
            self.agent.enable()
        self.assertTrue(self.agent.plist.exists())
        self.assertFalse(self.agent.status()['loaded'])
        del self.runner.failures['bootstrap']
        self.assertTrue(self.agent.enable()['loaded'])

    def test_success_return_without_observed_load_is_not_success(self):
        def no_load(command, **options):
            if command[1] == 'bootstrap':
                return subprocess.CompletedProcess(command, 0, '', '')
            return self.runner(command, **options)
        self.agent.runner = no_load
        with self.assertRaisesRegex(resident.ResidentError, '^resident_load_not_observed$'):
            self.agent.enable()

    def test_unknown_print_failure_does_not_claim_absent_or_modify_job(self):
        self.runner.failures['print'] = 5
        with self.assertRaisesRegex(resident.ResidentError, '^launchctl_status_failed$'):
            self.agent.enable()
        self.assertFalse(self.agent.plist.exists())
        self.assertEqual(self.operations(), ['print'])

    def test_timeout_and_os_error_are_sanitized(self):
        for failure, expected in (
                (subprocess.TimeoutExpired('PRIVATE COMMAND', 15), 'launchctl_print_timeout'),
                (FileNotFoundError('PRIVATE PATH'), 'launchctl_unavailable')):
            with self.subTest(failure=type(failure).__name__):
                self.runner.failures['print'] = failure
                with self.assertRaisesRegex(resident.ResidentError, '^' + expected + '$'):
                    self.agent.status()

    def test_disable_only_boots_out_owned_target_and_removes_owned_plist(self):
        self.write_registration()
        self.runner.job = self.runner.owned_job()
        unrelated = self.agent.agents / 'com.example.keep.plist'
        unrelated.write_bytes(b'UNRELATED SENTINEL')
        result = self.agent.disable()
        self.assertFalse(result['registered'])
        self.assertFalse(self.agent.plist.exists())
        self.assertEqual(unrelated.read_bytes(), b'UNRELATED SENTINEL')
        self.assertEqual(self.runner.others, {'com.example.keep': {'running': True}})
        self.assertEqual(self.operations(), ['print', 'bootout', 'print'])
        self.assertEqual(self.runner.calls[1][0], [resident.LAUNCHCTL, 'bootout', self.agent.target])

    def test_disable_absent_is_idempotent_and_does_not_create_directories(self):
        self.assertFalse(self.agent.disable()['registered'])
        self.assertFalse(self.agent.disable()['loaded'])
        self.assertFalse((self.home / 'Library').exists())
        self.assertEqual(self.operations(), ['print', 'print'])

    def test_disable_unloaded_own_plist_needs_no_mutating_launchctl_call(self):
        self.write_registration()
        self.assertFalse(self.agent.disable()['registered'])
        self.assertEqual(self.operations(), ['print'])

    def test_failed_bootout_keeps_registration_for_recovery(self):
        self.write_registration()
        self.runner.job = self.runner.owned_job()
        self.runner.failures['bootout'] = 5
        with self.assertRaisesRegex(resident.ResidentError, '^launchctl_bootout_failed$'):
            self.agent.disable()
        self.assertTrue(self.agent.plist.exists())

    def test_registration_is_removed_before_bootout_can_terminate_menu_child(self):
        self.write_registration()
        self.runner.job = self.runner.owned_job()
        def terminated_child(command, **options):
            if command[1] == 'bootout':
                self.assertFalse(self.agent.plist.exists())
                raise SystemExit(0)
            return self.runner(command, **options)
        self.agent.runner = terminated_child
        with self.assertRaises(SystemExit):
            self.agent.disable()
        self.assertFalse(self.agent.plist.exists())

    def test_bootout_success_without_observed_unload_is_not_success(self):
        self.write_registration()
        self.runner.job = self.runner.owned_job()
        def still_loaded(command, **options):
            if command[1] == 'bootout':
                return subprocess.CompletedProcess(command, 0, '', '')
            return self.runner(command, **options)
        self.agent.runner = still_loaded
        with self.assertRaisesRegex(resident.ResidentError, '^resident_unload_not_observed$'):
            self.agent.disable()
        self.assertTrue(self.agent.plist.exists())

    def test_foreign_same_name_plist_is_never_replaced_or_removed(self):
        self.agent.directories(create=True)
        foreign = plistlib.dumps({'Label': resident.LABEL, 'ProgramArguments': ['/other/program']})
        self.agent.plist.write_bytes(foreign)
        for action in (self.agent.enable, self.agent.disable, self.agent.status):
            with self.subTest(action=action.__name__):
                with self.assertRaisesRegex(resident.ResidentError, '^unrecognized_resident_plist$'):
                    action()
                self.assertEqual(self.agent.plist.read_bytes(), foreign)
        self.assertEqual(self.runner.calls, [])

    def test_concurrent_plist_creation_cannot_be_overwritten(self):
        self.agent.directories(create=True)
        self.agent.plist.write_bytes(b'CONCURRENT FOREIGN FILE')
        with self.assertRaises(FileExistsError):
            self.agent.write_new()
        self.assertEqual(self.agent.plist.read_bytes(), b'CONCURRENT FOREIGN FILE')
        self.assertEqual(list(self.agent.agents.iterdir()), [self.agent.plist])

    def test_concurrent_resident_operation_is_refused(self):
        self.agent.directories(create=True)
        path = self.agent.agents / ('.' + resident.LABEL + '.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(resident.ResidentError, '^resident_operation_in_progress$'):
                self.agent.enable()
        finally:
            os.close(fd)
        self.assertEqual(self.runner.calls, [])

    def test_unrecognized_loaded_job_with_missing_plist_is_never_touched(self):
        self.runner.job = self.runner.owned_job()
        for action in (self.agent.enable, self.agent.disable, self.agent.status):
            with self.subTest(action=action.__name__):
                with self.assertRaisesRegex(resident.ResidentError, '^unrecognized_resident_job$'):
                    action()
        self.assertFalse(self.agent.plist.exists())
        self.assertTrue(all(op == 'print' for op in self.operations()))

    def test_loaded_path_program_and_arguments_must_all_match(self):
        self.write_registration()
        original = self.runner.owned_job()
        for bad in (original.replace(str(self.agent.plist), '/other/job.plist'),
                    original.replace('program = ' + str(self.agent.executable), 'program = /other/app'),
                    original.replace('--resident', '--other'), 'unknown diagnostic format'):
            self.runner.job = bad
            for action in (self.agent.enable, self.agent.disable):
                with self.subTest(action=action.__name__, job=bad[:30]):
                    with self.assertRaisesRegex(resident.ResidentError, '^unrecognized_resident_job$'):
                        action()
        self.assertTrue(all(op == 'print' for op in self.operations()))

    def test_symlink_directory_is_refused(self):
        (self.home / 'Library').mkdir()
        real = self.home / 'real'
        real.mkdir()
        self.agent.agents.symlink_to(real, target_is_directory=True)
        for action in (self.agent.enable, self.agent.disable, self.agent.status):
            with self.assertRaisesRegex(build.PackageError, 'symlink_path_refused'):
                action()
        self.assertFalse(list(real.iterdir()))
        self.assertEqual(self.runner.calls, [])

    def test_symlink_plist_is_refused_without_touching_target(self):
        self.agent.directories(create=True)
        sentinel = self.home / 'sentinel'
        sentinel.write_text('keep')
        self.agent.plist.symlink_to(sentinel)
        for action in (self.agent.enable, self.agent.disable, self.agent.status):
            with self.assertRaisesRegex(build.PackageError, 'symlink_path_refused'):
                action()
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertEqual(self.runner.calls, [])

    def test_hardlinked_or_writable_plist_is_refused(self):
        self.write_registration()
        self.agent.plist.chmod(0o666)
        with self.assertRaisesRegex(resident.ResidentError, '^unsafe_resident_plist$'):
            self.agent.disable()
        self.agent.plist.chmod(0o600)
        os.link(self.agent.plist, self.home / 'alias')
        with self.assertRaisesRegex(resident.ResidentError, '^unsafe_resident_plist$'):
            self.agent.enable()
        self.assertEqual(self.runner.calls, [])

    def test_foreign_owner_plist_is_refused(self):
        self.write_registration()
        real_fstat = os.fstat
        def foreign_owner(fd):
            values = list(real_fstat(fd))
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        with patch.object(resident.os, 'fstat', side_effect=foreign_owner):
            with self.assertRaisesRegex(resident.ResidentError, '^unsafe_resident_plist$'):
                self.agent.status()
        self.assertEqual(self.runner.calls, [])

    def test_unsafe_directory_owner_and_permissions_are_refused(self):
        self.agent.directories(create=True)
        self.agent.agents.chmod(0o777)
        with self.assertRaisesRegex(install.InstallError, '^unsafe_install_directory$'):
            self.agent.enable()
        self.agent.agents.chmod(0o755)
        real_lstat = Path.lstat
        def foreign_owner(path, *args, **kwargs):
            result = real_lstat(path, *args, **kwargs)
            if path == self.agent.agents:
                values = list(result)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return result
        with patch.object(Path, 'lstat', foreign_owner):
            with self.assertRaisesRegex(install.InstallError, '^unsafe_install_directory$'):
                self.agent.disable()
        self.assertEqual(self.runner.calls, [])

    def test_unknown_app_marker_and_shell_executable_are_refused(self):
        marker = self.agent.app / 'Contents/Resources' / build.MARKER_NAME
        marker.write_text('{}')
        with self.assertRaisesRegex(install.InstallError, '^existing_app_identity_mismatch$'):
            self.agent.enable()
        marker.write_text(json.dumps(build.MARKER))
        self.agent.executable.write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(resident.ResidentError, '^unsafe_resident_executable$'):
            self.agent.enable()
        self.assertEqual(self.runner.calls, [])

    def test_app_symlink_and_group_writable_binary_are_refused(self):
        self.agent.executable.chmod(0o775)
        with self.assertRaisesRegex(install.InstallError, '^unsafe_existing_app_owner$'):
            self.agent.enable()
        self.agent.executable.unlink()
        self.agent.executable.symlink_to(self.home / 'not-executed')
        with self.assertRaisesRegex(install.InstallError, '^unsafe_existing_app_entry$'):
            self.agent.enable()
        self.assertEqual(self.runner.calls, [])

    def test_disable_still_works_after_owned_app_has_been_removed(self):
        self.write_registration()
        self.runner.job = self.runner.owned_job()
        self.agent.app.rename(self.home / 'app-backup')
        self.assertFalse(self.agent.disable()['registered'])

    def test_privileged_registration_is_refused(self):
        with patch.object(resident.os, 'getuid', return_value=0):
            with self.assertRaisesRegex(resident.ResidentError, 'without_sudo'):
                resident.ResidentAgent(_home=self.home, _runner=self.runner)

    def test_cli_reports_json_without_launchctl_private_output(self):
        with patch.object(resident, 'ResidentAgent', return_value=self.agent), \
                patch.object(resident.sys, 'platform', 'darwin'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(resident.main(['enable']), 0)
            self.assertTrue(json.loads(output.getvalue())['ok'])
            self.assertNotIn('PRIVATE', output.getvalue())
            self.runner.failures['print'] = 5
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(resident.main(['status']), 1)
            self.assertEqual(json.loads(error.getvalue()),
                             {'ok': False, 'error': 'launchctl_status_failed'})
            self.assertNotIn('PRIVATE', error.getvalue())


if __name__ == '__main__':
    unittest.main()
