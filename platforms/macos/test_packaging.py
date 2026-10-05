"""Offline packaging fixtures. Never install into the current account or launch Codex."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

MACOS = Path(__file__).resolve().parent
sys.path.insert(0, str(MACOS))
import build
import install

REAL_COMPILER = build.compile_native_launcher
FIXTURE_EXECUTABLE = b'\xcf\xfa\xed\xfeOFFLINE PACKAGING FIXTURE; DO NOT EXECUTE\n'


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='codex-usage-bar-fixture-')
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.home = self.root / 'account'
        self.source.mkdir()
        self.home.mkdir()
        compiler_patch = patch.object(build, 'compile_native_launcher',
                                      side_effect=self.compile_fixture)
        self.compiler = compiler_patch.start()
        self.addCleanup(compiler_patch.stop)
        for relative in (*build.PAYLOAD_FILES, *build.SOURCE_EXTRA_FILES):
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture: ' + relative + '\n')
        (self.source / 'codex_bar/__init__.py').write_text('__version__ = "0.3.0"\n')
        self.installer = install.Installer(_home=self.home, _source_root=self.source,
                                           _receipt_factory=self.make_receipt,
                                           _idle_check=lambda profile: None)

    def tearDown(self):
        self.temporary.cleanup()

    def compile_fixture(self, payload, destination, cache):
        self.assertEqual((payload / build.NATIVE_SOURCE).read_bytes(),
                         (self.source / build.NATIVE_SOURCE).read_bytes())
        self.assertTrue(cache.parent.is_dir())
        destination.write_bytes(FIXTURE_EXECUTABLE)

    def make_receipt(self, reuse=False):
        app = self.home / 'Applications/codex-usage-bar.app'
        support = self.home / 'Library/Application Support/codex-usage-bar'
        if reuse:
            base = self.source.parent / 'codex-usage-pet/.build/private-login'
            profile, manifest = base / 'session-fixture01', base / 'active-session.json'
        else:
            profile, manifest = support / 'private-session', None
        return {'version': 1, 'appPath': str(app),
                'resourceRoot': str(app / 'Contents/Resources/codex-usage-bar'),
                'sourceRoot': str(self.source), 'profileBinding': 'approved_previous' if reuse else 'fresh',
                'profileRoot': str(profile), 'approvedManifestPath': str(manifest) if manifest else None}

    def test_allowlist_excludes_private_material_and_tracks_hashes(self):
        for relative in ('.state/auth.json', 'evidence/screenshot.png', '.git/config',
                         'web/secret.json', 'codex_bar/unapproved.py'):
            extra = self.source / relative
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text('DO NOT PACKAGE')
        app = self.root / 'dist/codex-usage-bar.app'
        manifest = build.build_app(app, _source_root=self.source)
        payload = app / 'Contents/Resources/codex-usage-bar'
        actual = {str(p.relative_to(payload)) for p in payload.rglob('*') if p.is_file()}
        self.assertEqual(actual, set(build.PAYLOAD_FILES))
        self.assertEqual({f['path'] for f in manifest['files']}, actual)
        for entry in manifest['files']:
            self.assertEqual(entry['sha256'], hashlib.sha256(
                (payload / entry['path']).read_bytes()).hexdigest())
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertEqual(stat.S_IMODE((app / 'Contents/MacOS/codex-usage-bar').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((payload / 'platforms/macos/Start.command').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((payload / 'platforms/macos/Test.command').stat().st_mode), 0o755)
        install.verify_owned_app(app)

    def test_app_launcher_is_native_and_tracks_its_source_and_output(self):
        app = self.root / 'app.app'
        manifest = build.build_app(app, _source_root=self.source)
        binary = app / 'Contents/MacOS/codex-usage-bar'
        self.assertEqual(binary.read_bytes(), FIXTURE_EXECUTABLE)
        self.compiler.assert_called_once()
        native = manifest['nativeLauncher']
        self.assertEqual(native['path'], 'Contents/MacOS/codex-usage-bar')
        self.assertEqual(native['format'], 'Mach-O')
        self.assertEqual(native['source'], 'platforms/macos/Launcher.swift')
        self.assertEqual(native['sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())
        self.assertEqual(native['sourceSha256'], hashlib.sha256(
            (self.source / build.NATIVE_SOURCE).read_bytes()).hexdigest())
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        self.assertTrue(info['LSUIElement'])
        self.assertEqual(info['CFBundleExecutable'], build.PRODUCT)
        self.assertEqual(info['LSMinimumSystemVersion'], native['minimumMacOSVersion'])
        self.assertIn('codex_bar/daily_host.py', build.PAYLOAD_FILES)
        self.assertIn('tests/test_daily_host.py', build.SOURCE_EXTRA_FILES)

    def test_sprig_bundle_and_license_ship_but_build_inputs_are_source_only(self):
        runtime = {'web/sprig.js', 'web/THREE-LICENSE.txt'}
        build_inputs = {'web/sprig-source/runtime.js', 'web/sprig-source/character.js',
                        'web/sprig-source/motion.js', 'web/vendor/three.module.js',
                        'web/vendor/three.core.min.js', 'scripts/build-sprig.mjs',
                        'package.json', 'package-lock.json'}
        self.assertTrue(runtime.issubset(build.PAYLOAD_FILES))
        self.assertTrue(build_inputs.issubset(build.SOURCE_EXTRA_FILES))
        self.assertFalse(build_inputs.intersection(build.PAYLOAD_FILES))
        app = self.root / 'sprig.app'
        build.build_app(app, _source_root=self.source)
        payload = app / 'Contents/Resources/codex-usage-bar'
        for name in runtime:
            self.assertEqual((payload / name).read_bytes(), (self.source / name).read_bytes())
        for name in build_inputs:
            self.assertFalse((payload / name).exists(), name)
        install.verify_owned_app(app)

    def test_swift_compiler_uses_copied_source_and_no_shell(self):
        payload = self.root / 'copy with spaces'
        destination = self.root / 'output with spaces/launcher'
        cache = self.root / 'cache'
        with patch.object(build.subprocess, 'run') as run, \
                patch.object(build.platform, 'machine', return_value='arm64'):
            run.return_value.returncode = 0
            REAL_COMPILER(payload, destination, cache)
        arguments, options = run.call_args
        command = arguments[0]
        self.assertEqual(command[:5], ['/usr/bin/xcrun', 'swiftc', '-O', '-framework', 'AppKit'])
        self.assertIn('arm64-apple-macosx12.0', command)
        self.assertIn('platforms/macos/Launcher.swift', command)
        self.assertEqual(command[command.index('-o') + 1], os.path.relpath(destination, payload))
        self.assertEqual(command[command.index('-file-prefix-map') + 1], str(payload) + '=.')
        self.assertEqual(options['cwd'], payload)
        self.assertNotIn('shell', options)
        self.assertTrue(options['capture_output'])

    def test_native_compile_failure_leaves_no_partial_app(self):
        self.compiler.side_effect = build.PackageError('native_launcher_compile_failed')
        with self.assertRaisesRegex(build.PackageError, 'native_launcher_compile_failed'):
            build.build_app(self.root / 'app.app', _source_root=self.source)
        self.assertFalse((self.root / 'app.app').exists())
        self.assertFalse(list(self.root.glob('.codex-usage-bar-build-*')))

    def test_compiler_errors_are_sanitized(self):
        for failure, code in ((FileNotFoundError('private path'), 'apple_swift_compiler_required'),
                              (subprocess.TimeoutExpired('private path', 120),
                               'native_launcher_compile_timeout')):
            with self.subTest(error=code), patch.object(build.subprocess, 'run', side_effect=failure):
                with self.assertRaisesRegex(build.PackageError, '^' + code + '$'):
                    REAL_COMPILER(self.source, self.root / 'output', self.root / 'cache')
        with patch.object(build.subprocess, 'run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b'compiler diagnostic with private paths'
            with self.assertRaisesRegex(build.PackageError, '^native_launcher_compile_failed$'):
                REAL_COMPILER(self.source, self.root / 'output', self.root / 'cache')

    def test_native_output_rejects_shell_missing_or_symlink(self):
        def shell_output(payload, destination, cache):
            destination.write_text('#!/bin/zsh\nexit 0\n')
        def symlink_output(payload, destination, cache):
            destination.symlink_to(self.source / 'README.md')
        for action, code in ((shell_output, 'native_launcher_not_macho'),
                             (lambda *args: None, 'native_launcher_output_missing'),
                             (symlink_output, 'symlink_path_refused')):
            with self.subTest(error=code):
                self.compiler.side_effect = action
                with self.assertRaisesRegex(build.PackageError, code):
                    build.build_app(self.root / 'app.app', _source_root=self.source)
                self.assertFalse((self.root / 'app.app').exists())

    @unittest.skipUnless(shutil.which('zsh'), 'zsh is required to exercise macOS launchers')
    def test_launchers_dispatch_to_daily_or_explicit_isolated_mode(self):
        # Execute the exact shipped shell scripts against a harmless fixture module.
        # No real manager is imported and no Codex app or profile is opened.
        module = self.source / 'codex_bar/__main__.py'
        module.write_text('import json, sys\nfrom pathlib import Path\n'
                          'print(json.dumps({"arguments": sys.argv[1:], '
                          '"environmentIgnored": sys.flags.ignore_environment, '
                          '"userSiteDisabled": sys.flags.no_user_site, '
                          '"bytecodeDisabled": sys.dont_write_bytecode, '
                          '"correctWorkingDirectory": Path.cwd() == Path(__file__).resolve().parent.parent}))\n')
        commands = [
            ('Start.command', [], ['daily', '--acknowledge-runtime', '--wait-for-exit']),
            ('Start.command', ['--resident'],
             ['daily', '--acknowledge-runtime', '--wait-for-exit', '--resident']),
            ('Start.command', ['--resident', '--supervisor-pid', '12345'],
             ['daily', '--acknowledge-runtime', '--wait-for-exit', '--resident',
              '--supervisor-pid', '12345']),
            ('Start.command', ['--resident', '--launch-once'],
             ['daily', '--acknowledge-runtime', '--wait-for-exit', '--resident', '--launch-once']),
            ('Start.command', ['--resident', '--launch-once', '--supervisor-pid', '12345'],
             ['daily', '--acknowledge-runtime', '--wait-for-exit', '--resident', '--launch-once',
              '--supervisor-pid', '12345']),
            ('Test.command', [], ['run', '--acknowledge-runtime', '--duration', '0']),
        ]
        for name, arguments, expected in commands:
            with self.subTest(command=name, arguments=arguments):
                script = self.source / 'platforms/macos' / name
                script.write_bytes((MACOS / name).read_bytes())
                result = subprocess.run([shutil.which('zsh'), str(script), *arguments],
                                        text=True, capture_output=True, timeout=15)
                if result.returncode and 'Python 3.12 or later is required.' in result.stderr:
                    self.skipTest('No launcher-supported Python 3.12+ location on this test host')
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout.splitlines()[-1])
                self.assertEqual(output['arguments'], expected)
                for flag in ('environmentIgnored', 'userSiteDisabled', 'bytecodeDisabled',
                             'correctWorkingDirectory'):
                    self.assertTrue(output[flag], flag)
        for arguments in (['--unknown'], ['--resident', '--unknown'], ['--resident', '--resident'],
                          ['--resident', '--supervisor-pid'],
                          ['--resident', '--supervisor-pid', '1'],
                          ['--resident', '--supervisor-pid', '2147483648'],
                          ['--resident', '--supervisor-pid', 'not-a-pid'],
                          ['--resident', '--supervisor-pid', '12345', '--unknown'],
                          ['--launch-once'], ['--resident', '--launch-once', '--launch-once'],
                          ['--resident', '--supervisor-pid', '12345', '--launch-once']):
            with self.subTest(rejected=arguments):
                result = subprocess.run([shutil.which('zsh'),
                                         str(self.source / 'platforms/macos/Start.command'), *arguments],
                                        text=True, capture_output=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('correctWorkingDirectory', result.stdout)
        self.assertFalse(list(self.source.rglob('*.pyc')))

    def test_package_version_is_shared_by_bundle_and_manifest(self):
        (self.source / 'codex_bar/__init__.py').write_text('__version__ = "1.2.3"\n')
        app = self.root / 'app.app'
        manifest = build.build_app(app, _source_root=self.source)
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleVersion'], '1.2.3')
        self.assertEqual(info['CFBundleShortVersionString'], manifest['version'])
        self.assertEqual(manifest['version'], '1.2.3')

    def test_version_must_be_literal_and_is_never_executed(self):
        path = self.source / 'codex_bar/__init__.py'
        path.write_text('raise RuntimeError("must not execute")\n__version__ = "0.3.0"\n')
        self.assertEqual(build.package_version(self.source), '0.3.0')
        path.write_text('__version__ = str(3)\n')
        with self.assertRaisesRegex(build.PackageError, 'invalid_package_version'):
            build.build_app(self.root / 'app.app', _source_root=self.source)

    def test_source_archive_is_reproducible_allowlisted_and_private_path_free(self):
        for relative in ('.state/auth.json', '.verification/private.json', '.git/config',
                         'evidence/screenshot.png', 'web/unapproved.png'):
            extra = self.source / relative
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text('PRIVATE SENTINEL')
        archive_path = self.root / 'first.zip'
        manifest = build.build_source_archive(archive_path, _source_root=self.source)
        second = self.root / 'second.zip'
        build.build_source_archive(second, _source_root=self.source)
        self.assertEqual(archive_path.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(archive_path) as archive:
            prefix = 'codex-usage-bar-0.3.0/'
            expected = {prefix + name for name in (*build.PAYLOAD_FILES, *build.SOURCE_EXTRA_FILES)}
            expected.add(prefix + 'source-manifest.json')
            self.assertEqual(set(archive.namelist()), expected)
            self.assertIn(prefix + 'tests/test_sprig_runtime.js', archive.namelist())
            for name in archive.namelist():
                self.assertNotIn(b'PRIVATE SENTINEL', archive.read(name))
                self.assertNotIn(str(self.root).encode(), archive.read(name))
            self.assertEqual(json.loads(archive.read(prefix + 'source-manifest.json')), manifest)
            launcher = archive.getinfo(prefix + 'platforms/macos/Start.command')
            self.assertEqual((launcher.external_attr >> 16) & 0o777, 0o755)
        with self.assertRaisesRegex(build.PackageError, 'build_destination_already_exists'):
            build.build_source_archive(archive_path, _source_root=self.source)

    def test_source_archive_refuses_symlink_and_missing_required_files(self):
        source = self.source / 'scripts/validate.py'
        data = source.read_bytes()
        source.unlink()
        with self.assertRaisesRegex(build.PackageError, 'required_payload_file_missing'):
            build.build_source_archive(self.root / 'release.zip', _source_root=self.source)
        target = self.source / 'unapproved.py'
        target.write_bytes(data)
        source.symlink_to(target)
        with self.assertRaisesRegex(build.PackageError, 'symlink_path_refused'):
            build.build_source_archive(self.root / 'release.zip', _source_root=self.source)
        self.assertFalse((self.root / 'release.zip').exists())

    def test_builder_refuses_symlink_source(self):
        original = self.source / build.PAYLOAD_FILES[0]
        target = self.source / 'different.py'
        original.rename(target)
        original.symlink_to(target)
        with self.assertRaisesRegex(build.PackageError, 'symlink_path_refused'):
            build.build_app(self.root / 'app.app', _source_root=self.source)

    def test_builder_refuses_symlink_destination_ancestor(self):
        real = self.root / 'real'
        real.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(build.PackageError, 'symlink_path_refused'):
            build.build_app(alias / 'app.app', _source_root=self.source)

    def test_builder_refuses_hardlinked_payload(self):
        source = self.source / build.PAYLOAD_FILES[0]
        os.link(source, self.source / 'linked.py')
        with self.assertRaisesRegex(build.PackageError, 'unsafe_payload_file'):
            build.build_app(self.root / 'app.app', _source_root=self.source)

    def test_license_and_provenance_are_required(self):
        for relative in ('README.md', 'LICENSE', 'NOTICE.md', 'docs/PROVENANCE.md',
                         'web/asset-manifest.json', 'web/THREE-LICENSE.txt',
                         'platforms/macos/Launcher.swift'):
            path = self.source / relative
            data = path.read_bytes()
            path.unlink()
            with self.assertRaisesRegex(build.PackageError, 'required_payload_file_missing'):
                build.build_app(self.root / 'app.app', _source_root=self.source)
            path.write_bytes(data)

    def test_install_has_private_receipt_and_no_private_payload(self):
        result = self.installer.install()
        self.assertTrue(self.installer.app.is_dir())
        self.assertIsNone(result['backupPath'])
        self.assertEqual(stat.S_IMODE(self.installer.support.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.installer.receipt_path.stat().st_mode), 0o600)
        self.assertEqual(self.installer.existing_receipt(),
                         {**self.make_receipt(), 'buildSourceRoot': str(self.source)})
        self.assertFalse((self.installer.support / 'private-session').exists())
        self.assertFalse(list(self.installer.app.rglob('install.json')))

    def test_upgrade_keeps_old_app_backup_and_approved_binding(self):
        self.installer.install(reuse_approved_profile=True)
        old_asset = self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js'
        old_content = old_asset.read_bytes()
        (self.source / 'web/bar.js').write_text('new release')
        result = self.installer.install()
        self.assertEqual(result['profileBinding'], 'approved_previous')
        backup = Path(result['backupPath'])
        self.assertEqual((backup / 'Contents/Resources/codex-usage-bar/web/bar.js').read_bytes(), old_content)
        self.assertEqual(old_asset.read_text(), 'new release')

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin atomic directory exchange')
    def test_atomic_upgrade_replaces_payload_without_retained_backup(self):
        self.installer.install()
        (self.source / 'web/bar.js').write_text('new resident fixture\n')
        result = self.installer.install(replace_existing=True)
        self.assertIsNone(result['backupPath'])
        self.assertFalse(self.installer.backups.exists())
        self.assertEqual((self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js')
                         .read_text(), 'new resident fixture\n')
        self.assertFalse(list(self.installer.applications.glob('.codex-usage-bar-install-*')))
        install.verify_owned_app(self.installer.app)

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin atomic directory exchange')
    def test_atomic_upgrade_receipt_failure_restores_old_app(self):
        self.installer.install()
        payload = self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js'
        original = payload.read_bytes()
        receipt = self.installer.receipt_path.read_bytes()
        (self.source / 'web/bar.js').write_text('new resident fixture\n')
        with patch.object(install, 'write_private_json', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                self.installer.install(replace_existing=True)
        self.assertEqual(payload.read_bytes(), original)
        self.assertEqual(self.installer.receipt_path.read_bytes(), receipt)
        self.assertFalse(self.installer.backups.exists())
        self.assertFalse(list(self.installer.applications.glob('.codex-usage-bar-install-*')))

    def test_atomic_upgrade_exchange_failure_preserves_installation(self):
        self.installer.install()
        payload = self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js'
        original = payload.read_bytes()
        with patch.object(install, 'exchange_apps',
                          side_effect=install.InstallError('atomic_app_exchange_failed')):
            with self.assertRaisesRegex(install.InstallError, 'atomic_app_exchange_failed'):
                self.installer.install(replace_existing=True)
        self.assertEqual(payload.read_bytes(), original)
        self.assertFalse(self.installer.backups.exists())
        self.assertFalse(list(self.installer.applications.glob('.codex-usage-bar-install-*')))

    def test_upgrade_rechecks_app_after_compilation(self):
        self.installer.install()
        original_build = install.build_app

        def changed_target(*args, **kwargs):
            result = original_build(*args, **kwargs)
            marker = self.installer.app / 'Contents/Resources' / build.MARKER_NAME
            marker.write_text('{"foreign":true}')
            return result

        with patch.object(install, 'build_app', side_effect=changed_target):
            with self.assertRaisesRegex(install.InstallError, 'existing_app_identity_mismatch'):
                self.installer.install(replace_existing=True)
        self.assertTrue(self.installer.app.exists())
        self.assertEqual((self.installer.app / 'Contents/Resources' / build.MARKER_NAME)
                         .read_text(), '{"foreign":true}')

    def test_upgrade_from_moved_checkout_retains_approved_profile_binding(self):
        self.installer.install(reuse_approved_profile=True)
        previous = self.installer.existing_receipt()
        moved = self.root / 'moved-source'
        self.source.rename(moved)
        self.source = moved
        self.installer.source = moved
        self.installer.receipt_factory = lambda reuse: self.fail('must retain approved binding')
        self.installer.install()
        current = self.installer.existing_receipt()
        for key in ('sourceRoot', 'profileRoot', 'approvedManifestPath', 'profileBinding'):
            self.assertEqual(current[key], previous[key])
        self.assertEqual(current['buildSourceRoot'], str(moved))

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin atomic directory exchange')
    def test_committed_upgrade_reports_cleanup_failure_separately(self):
        self.installer.install()
        (self.source / 'web/bar.js').write_text('new resident fixture\n')
        original_cleanup = shutil.rmtree

        def fail_final_cleanup(path, *args, **kwargs):
            if Path(path).name.startswith('.codex-usage-bar-install-'):
                raise PermissionError('fixture')
            return original_cleanup(path, *args, **kwargs)

        with patch.object(install.shutil, 'rmtree', side_effect=fail_final_cleanup):
            result = self.installer.install(replace_existing=True)
        self.assertEqual(result['cleanupWarning']['code'], 'installed_cleanup_incomplete')
        self.assertTrue(Path(result['cleanupWarning']['retainedStagingPath']).is_dir())
        self.assertEqual((self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js')
                         .read_text(), 'new resident fixture\n')
        self.installer.existing_receipt()

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin atomic directory exchange')
    def test_rollback_exchange_failure_retains_original_app(self):
        self.installer.install()
        original_exchange = install.exchange_apps
        calls = []

        def fail_rollback(first, second):
            calls.append((first, second))
            if len(calls) == 2:
                raise install.InstallError('atomic_app_exchange_failed')
            original_exchange(first, second)

        with patch.object(install, 'write_private_json', side_effect=OSError('fixture')), \
                patch.object(install, 'exchange_apps', side_effect=fail_rollback):
            with self.assertRaisesRegex(install.InstallError, 'atomic_app_rollback_failed'):
                self.installer.install(replace_existing=True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0].is_dir())
        self.assertTrue(self.installer.app.is_dir())

    def test_uninstall_retains_profile_state_and_allows_reinstall(self):
        self.installer.install()
        profile = self.installer.support / 'private-session'
        profile.mkdir(mode=0o700)
        sentinel = profile / 'untouched-login-sentinel'
        sentinel.write_text('fixture only')
        result = self.installer.uninstall()
        self.assertFalse(self.installer.app.exists())
        self.assertTrue(Path(result['backupPath']).exists())
        self.assertTrue(Path(result['archivedReceiptPath']).exists())
        self.assertEqual(sentinel.read_text(), 'fixture only')
        self.installer.install()
        self.assertEqual(sentinel.read_text(), 'fixture only')

    def test_unrelated_existing_app_is_not_replaced(self):
        self.installer.app.mkdir(parents=True)
        sentinel = self.installer.app / 'unrelated-file'
        sentinel.write_text('keep')
        with self.assertRaises((OSError, install.InstallError)):
            self.installer.install()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_symlink_app_and_receipt_are_refused(self):
        self.installer.applications.mkdir()
        self.installer.app.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(build.PackageError, 'symlink_path_refused'):
            self.installer.install()
        self.installer.app.unlink()
        self.installer.install()
        self.installer.receipt_path.unlink()
        self.installer.receipt_path.symlink_to(self.source / 'LICENSE')
        with self.assertRaisesRegex(build.PackageError, 'symlink_path_refused'):
            self.installer.uninstall()
        self.assertTrue(self.installer.app.is_dir())

    def test_wrong_mode_receipt_is_refused(self):
        self.installer.install()
        self.installer.receipt_path.chmod(0o644)
        with self.assertRaisesRegex(install.InstallError, 'unsafe_install_metadata'):
            self.installer.uninstall()

    def test_live_manager_lock_is_refused(self):
        self.installer.install()
        with (self.installer.support / 'manager.lock').open('r+') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(install.InstallError, 'manager_is_running'):
                self.installer.uninstall()
        self.assertTrue(self.installer.app.is_dir())

    def test_upgrade_refuses_live_shared_manager_without_changing_app_or_receipt(self):
        self.installer.install(reuse_approved_profile=True)
        receipt = self.installer.receipt_path.read_bytes()
        old_asset = self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js'
        old_content = old_asset.read_bytes()
        (self.source / 'web/bar.js').write_text('new release')
        with (self.installer.support / 'manager.lock').open('r+') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(install.InstallError, 'manager_is_running'):
                self.installer.install()
        self.assertEqual(old_asset.read_bytes(), old_content)
        self.assertEqual(self.installer.receipt_path.read_bytes(), receipt)
        self.assertFalse(self.installer.backups.exists())

    def test_active_profile_is_refused_without_app_mutation(self):
        self.installer.install()
        def busy(profile):
            raise install.InstallError('dedicated_profile_is_in_use')
        self.installer.idle_check = busy
        with self.assertRaisesRegex(install.InstallError, 'dedicated_profile_is_in_use'):
            self.installer.uninstall()
        self.assertTrue(self.installer.app.is_dir())

    def test_first_install_failure_can_retry(self):
        with patch.object(install, 'write_private_json', side_effect=OSError('fixture error')):
            with self.assertRaises(OSError):
                self.installer.install()
        self.assertFalse(self.installer.app.exists())
        self.assertFalse(self.installer.receipt_path.exists())
        self.installer.install()
        self.assertTrue(self.installer.app.exists())

    def test_failed_upgrade_restores_app_and_receipt(self):
        self.installer.install()
        old_receipt = self.installer.receipt_path.read_bytes()
        old_asset = (self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js').read_bytes()
        (self.source / 'web/bar.js').write_text('new release')
        with patch.object(install, 'write_private_json', side_effect=OSError('fixture error')):
            with self.assertRaises(OSError):
                self.installer.install()
        self.assertEqual(self.installer.receipt_path.read_bytes(), old_receipt)
        self.assertEqual((self.installer.app / 'Contents/Resources/codex-usage-bar/web/bar.js').read_bytes(), old_asset)

    def test_failed_compilation_preserves_installed_app_and_receipt(self):
        self.installer.install()
        old_receipt = self.installer.receipt_path.read_bytes()
        launcher = self.installer.app / 'Contents/MacOS/codex-usage-bar'
        old_launcher = launcher.read_bytes()
        self.compiler.side_effect = build.PackageError('native_launcher_compile_failed')
        with self.assertRaisesRegex(build.PackageError, 'native_launcher_compile_failed'):
            self.installer.install()
        self.assertEqual(launcher.read_bytes(), old_launcher)
        self.assertEqual(self.installer.receipt_path.read_bytes(), old_receipt)
        self.assertFalse(self.installer.backups.exists())

    def test_forged_receipt_target_is_refused(self):
        self.installer.install()
        receipt = self.make_receipt()
        receipt['appPath'] = str(self.source)
        install.write_private_json(self.installer.receipt_path, receipt)
        with self.assertRaisesRegex(install.InstallError, 'install_receipt_identity_mismatch'):
            self.installer.uninstall()
        self.assertTrue(self.installer.app.is_dir())

    def test_fifo_receipt_is_rejected_without_blocking(self):
        self.installer.prepare_directories()
        os.mkfifo(self.installer.receipt_path, 0o600)
        with self.assertRaisesRegex(install.InstallError, 'unsafe_install_metadata'):
            self.installer.existing_receipt()

    def test_bundle_metadata_modes_ignore_permissive_umask(self):
        previous_umask = os.umask(0o002)
        try:
            self.installer.install()
        finally:
            os.umask(previous_umask)
        for path in ('Contents/Info.plist', 'Contents/Resources/codex-usage-bar-app.json',
                     'Contents/Resources/payload-manifest.json'):
            self.assertEqual(stat.S_IMODE((self.installer.app / path).stat().st_mode), 0o644)


if __name__ == '__main__':
    unittest.main()
