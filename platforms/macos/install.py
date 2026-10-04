#!/usr/bin/env python3
"""Install/uninstall the user-owned local wrapper without changing Codex.app."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shutil
import stat
import sys
import tempfile
import uuid

from build import (BUNDLE_ID, MARKER, MARKER_NAME, PRODUCT, SOURCE_ROOT,
                   PackageError, build_app, reject_symlink_components)


class InstallError(RuntimeError):
    pass


def user_home() -> Path:
    # HOME may refer to a different profile; use the current local account.
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def checked_directory(path: Path, *, private=False, create=False) -> None:
    reject_symlink_components(path)
    if create:
        path.mkdir(mode=0o700 if private else 0o755, exist_ok=True)
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or (mode != 0o700 if private else bool(mode & 0o022))):
        raise InstallError('unsafe_install_directory')


def read_owned_json(path: Path, *, private: bool) -> dict:
    reject_symlink_components(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or (mode != 0o600 if private else bool(mode & 0o022))):
            raise InstallError('unsafe_install_metadata')
        raw = os.read(fd, 65537)
        if len(raw) > 65536:
            raise InstallError('install_metadata_too_large')
        record = json.loads(raw)
        if not isinstance(record, dict):
            raise InstallError('invalid_install_metadata')
        return record
    finally:
        os.close(fd)


def write_private_json(path: Path, record: dict) -> None:
    reject_symlink_components(path)
    fd, name = tempfile.mkstemp(prefix='.install-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_owned_app(app: Path) -> None:
    checked_directory(app)
    for item in app.rglob('*'):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise InstallError('unsafe_existing_app_entry')
        if (info.st_uid != os.getuid() or info.st_mode & 0o022
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
            raise InstallError('unsafe_existing_app_owner')
    marker = read_owned_json(app / 'Contents' / 'Resources' / MARKER_NAME, private=False)
    if marker != MARKER:
        raise InstallError('existing_app_identity_mismatch')
    info_file = app / 'Contents' / 'Info.plist'
    reject_symlink_components(info_file)
    with info_file.open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleExecutable') != PRODUCT:
        raise InstallError('existing_app_identity_mismatch')


def receipt_for_source(reuse_approved_profile: bool) -> dict:
    sys.path.insert(0, str(SOURCE_ROOT))
    from codex_bar.host import build_install_receipt
    return build_install_receipt(reuse_approved_profile=reuse_approved_profile)


def verify_idle_profile(profile: Path) -> None:
    if not profile.exists():
        return
    checked_directory(profile, private=True)
    sys.path.insert(0, str(SOURCE_ROOT))
    from codex_bar.host import System
    if System().profile_users(profile):
        raise InstallError('dedicated_profile_is_in_use')


class Installer:
    """The CLI fixes all target paths. Underscored inputs are fixture-only seams."""

    def __init__(self, *, _home=None, _source_root=SOURCE_ROOT,
                 _receipt_factory=receipt_for_source, _idle_check=verify_idle_profile):
        self.home = Path(_home) if _home is not None else user_home()
        self.source = Path(_source_root)
        self.applications = self.home / 'Applications'
        self.app = self.applications / (PRODUCT + '.app')
        self.support = self.home / 'Library' / 'Application Support' / PRODUCT
        self.receipt_path = self.support / 'install.json'
        self.backups = self.applications / ('.' + PRODUCT + '-backups')
        self.receipt_factory = _receipt_factory
        self.idle_check = _idle_check

    def validate_receipt(self, receipt: dict) -> None:
        resource = self.app / 'Contents' / 'Resources' / PRODUCT
        if (receipt.get('version') != 1 or receipt.get('appPath') != str(self.app)
                or receipt.get('resourceRoot') != str(resource)
                or receipt.get('profileBinding') not in ('fresh', 'approved_previous')):
            raise InstallError('install_receipt_identity_mismatch')
        source = receipt.get('sourceRoot')
        profile = receipt.get('profileRoot')
        if not isinstance(source, str) or not Path(source).is_absolute():
            raise InstallError('invalid_receipt_source')
        if not isinstance(profile, str) or not Path(profile).is_absolute():
            raise InstallError('invalid_receipt_profile')
        if receipt['profileBinding'] == 'fresh':
            if profile != str(self.support / 'private-session') or receipt.get('approvedManifestPath') is not None:
                raise InstallError('invalid_fresh_profile_binding')
        else:
            # The host helper additionally verifies the original closed manifest.
            expected = Path(source).parent / 'codex-usage-pet' / '.build' / 'private-login'
            if Path(profile).parent != expected or receipt.get('approvedManifestPath') != str(expected / 'active-session.json'):
                raise InstallError('invalid_approved_profile_binding')
        reject_symlink_components(Path(profile))

    def existing_receipt(self) -> dict | None:
        reject_symlink_components(self.receipt_path)
        if not self.receipt_path.exists():
            return None
        checked_directory(self.support, private=True)
        record = read_owned_json(self.receipt_path, private=True)
        self.validate_receipt(record)
        return record

    def archived_receipt(self) -> dict | None:
        """Reuse retained configuration when reinstalling after our own uninstall."""
        if not self.support.exists():
            return None
        checked_directory(self.support, private=True)
        candidates = [item for item in self.support.iterdir()
                      if re.fullmatch(r'uninstalled-[a-f0-9]{32}\.json', item.name)]
        if not candidates:
            return None
        records = []
        for item in candidates:
            record = read_owned_json(item, private=True)
            self.validate_receipt(record)
            records.append((item.stat().st_mtime_ns, record))
        return max(records, key=lambda item: item[0])[1]

    def prepare_directories(self) -> None:
        checked_directory(self.home)
        for directory in (self.applications, self.home / 'Library', self.home / 'Library' / 'Application Support'):
            checked_directory(directory, create=True)
        checked_directory(self.support, private=True, create=True)

    @contextmanager
    def lifecycle_lock(self):
        path = self.support / 'manager.lock'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise InstallError('unsafe_manager_lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InstallError('manager_is_running_stop_it_first') from exc
            yield
        finally:
            os.close(fd)

    def backup_destination(self, purpose: str) -> Path:
        checked_directory(self.backups, private=True, create=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        return self.backups / f'{PRODUCT}-{purpose}-{stamp}-{uuid.uuid4().hex[:8]}.app'

    def install(self, *, reuse_approved_profile=False) -> dict:
        reject_symlink_components(self.app)
        current_receipt = self.existing_receipt()
        previous = current_receipt or self.archived_receipt()
        if self.app.exists():
            verify_owned_app(self.app)
            if current_receipt is None:
                raise InstallError('existing_app_has_no_owned_receipt')
        if self.support.exists() and previous is None:
            checked_directory(self.support, private=True)
            # A failed first transaction may leave only the lock we safely verify below.
            if {item.name for item in self.support.iterdir()} - {'manager.lock'}:
                raise InstallError('unrecognized_existing_support_directory')
        reuse = reuse_approved_profile or bool(previous and previous['profileBinding'] == 'approved_previous')
        receipt = self.receipt_factory(reuse)
        self.validate_receipt(receipt)
        self.prepare_directories()
        with self.lifecycle_lock():
            if self.existing_receipt() != current_receipt:
                raise InstallError('installation_changed_retry')
            self.idle_check(Path(receipt['profileRoot']))
            if previous and previous['profileRoot'] != receipt['profileRoot']:
                self.idle_check(Path(previous['profileRoot']))
            staging = Path(tempfile.mkdtemp(prefix='.codex-usage-bar-install-', dir=self.applications))
            staged_app = staging / (PRODUCT + '.app')
            backup = None
            promoted = False
            try:
                build_app(staged_app, _source_root=self.source)
                verify_owned_app(staged_app)
                if self.app.exists():
                    backup = self.backup_destination('backup')
                    self.app.rename(backup)
                staged_app.rename(self.app)
                promoted = True
                write_private_json(self.receipt_path, receipt)
            except BaseException:
                if promoted:
                    # Only this call's freshly generated app is removed on rollback.
                    shutil.rmtree(self.app)
                if backup is not None and backup.exists():
                    backup.rename(self.app)
                raise
            finally:
                shutil.rmtree(staging)
        return {'appPath': str(self.app), 'receiptPath': str(self.receipt_path),
                'backupPath': str(backup) if backup else None, 'profileBinding': receipt['profileBinding']}

    def uninstall(self) -> dict:
        receipt = self.existing_receipt()
        if receipt is None:
            raise InstallError('owned_install_receipt_required')
        verify_owned_app(self.app)
        with self.lifecycle_lock():
            self.idle_check(Path(receipt['profileRoot']))
            backup = self.backup_destination('uninstalled')
            # Preserve all login/profile files and unrelated state. Archive only our receipt.
            archived_receipt = self.support / ('uninstalled-' + uuid.uuid4().hex + '.json')
            self.app.rename(backup)
            try:
                self.receipt_path.rename(archived_receipt)
            except BaseException:
                backup.rename(self.app)
                raise
        return {'removedAppPath': str(self.app), 'backupPath': str(backup),
                'archivedReceiptPath': str(archived_receipt), 'loginProfilesRetained': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--use-approved-project-profile', action='store_true',
                        help='keep the validated predecessor profile for optional Test.command mode only')
    parser.add_argument('--uninstall', action='store_true',
                        help='remove only this receipted app; retain backup, state and login profiles')
    args = parser.parse_args(argv)
    if args.uninstall and args.use_approved_project_profile:
        parser.error('profile selection is only available during installation')
    if sys.platform != 'darwin' or sys.version_info < (3, 12):
        parser.error('macOS with an existing Python 3.12 or later is required')
    try:
        installer = Installer()
        result = installer.uninstall() if args.uninstall else installer.install(
            reuse_approved_profile=args.use_approved_project_profile)
    except Exception as exc:
        # Do not leak raw process output, credentials, or unexpected exception payloads.
        from_errors = (InstallError, PackageError)
        code = str(exc) if isinstance(exc, from_errors) else type(exc).__name__
        print('Installation not completed: ' + code, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if not args.uninstall:
        print('The app launches daily Codex with its existing login and history. Test.command is isolated opt-in.')
    print('No Codex.app files, autostart settings, or signing settings were changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
