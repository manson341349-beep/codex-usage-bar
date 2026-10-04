"""Offline packaging fixtures. Never install into the current account or launch Codex."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

MACOS = Path(__file__).resolve().parent
sys.path.insert(0, str(MACOS))
import build
import install


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='codex-usage-bar-fixture-')
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.home = self.root / 'account'
        self.source.mkdir()
        self.home.mkdir()
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
            self.assertEqual(len(entry['sha256']), 64)
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertEqual(stat.S_IMODE((app / 'Contents/MacOS/codex-usage-bar').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((payload / 'platforms/macos/Start.command').stat().st_mode), 0o755)
        install.verify_owned_app(app)

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
        for relative in ('README.md', 'LICENSE', 'NOTICE.md', 'docs/PROVENANCE.md', 'web/asset-manifest.json'):
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
        self.assertEqual(self.installer.existing_receipt(), self.make_receipt())
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
