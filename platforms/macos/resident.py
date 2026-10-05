#!/usr/bin/env python3
"""Manage only this account's fixed codex-usage-bar login agent."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile

from build import MACHO_MAGICS, PRODUCT, reject_symlink_components
from install import checked_directory, user_home, verify_owned_app

LABEL = 'io.github.codex-usage-bar.resident'
LAUNCHCTL = '/bin/launchctl'
# launchctl's bootstrap error for a service absent from an existing domain.
SERVICE_NOT_FOUND = 113


class ResidentError(RuntimeError):
    pass


class ResidentAgent:
    """No public path/label overrides. Underscored inputs are offline test seams."""

    def __init__(self, *, _home=None, _runner=subprocess.run):
        if os.getuid() == 0 or os.geteuid() != os.getuid():
            raise ResidentError('run_as_the_logged_in_user_without_sudo')
        self.home = Path(_home) if _home is not None else user_home()
        self.app = self.home / 'Applications' / (PRODUCT + '.app')
        self.executable = self.app / 'Contents' / 'MacOS' / PRODUCT
        self.agents = self.home / 'Library' / 'LaunchAgents'
        self.plist = self.agents / (LABEL + '.plist')
        self.domain = 'gui/' + str(os.getuid())
        self.target = self.domain + '/' + LABEL
        self.runner = _runner

    def definition(self) -> dict:
        return {'Label': LABEL, 'ProgramArguments': [str(self.executable), '--resident'],
                'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
                'ThrottleInterval': 10, 'LimitLoadToSessionType': 'Aqua',
                'ProcessType': 'Interactive'}

    def directories(self, *, create=False) -> bool:
        checked_directory(self.home)
        for path in (self.home / 'Library', self.agents):
            reject_symlink_components(path)
            if not path.exists() and not create:
                return False
            checked_directory(path, create=create)
        return True

    def verify_app(self) -> None:
        checked_directory(self.home / 'Applications')
        verify_owned_app(self.app)
        reject_symlink_components(self.executable)
        fd = os.open(self.executable, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022
                    or not info.st_mode & stat.S_IXUSR or os.read(fd, 4) not in MACHO_MAGICS):
                raise ResidentError('unsafe_resident_executable')
        finally:
            os.close(fd)

    def registered(self) -> bool:
        reject_symlink_components(self.plist)
        try:
            fd = os.open(self.plist, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return False
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022):
                raise ResidentError('unsafe_resident_plist')
            raw = os.read(fd, 65537)
            if len(raw) > 65536:
                raise ResidentError('resident_plist_too_large')
            try:
                definition = plistlib.loads(raw)
            except Exception as exc:
                raise ResidentError('unrecognized_resident_plist') from exc
            # Exact equality also rejects extra Program, environment, and shell keys.
            if definition != self.definition():
                raise ResidentError('unrecognized_resident_plist')
            return True
        finally:
            os.close(fd)

    @contextmanager
    def lock(self):
        path = self.agents / ('.' + LABEL + '.lock')
        reject_symlink_components(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ResidentError('unsafe_resident_lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ResidentError('resident_operation_in_progress') from exc
            yield
        finally:
            os.close(fd)

    def run(self, operation: str, *arguments: str):
        try:
            # Never interpolate a shell command or forward caller credentials.
            return self.runner([LAUNCHCTL, operation, *arguments],
                               capture_output=True, text=True, timeout=15, check=False,
                               env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'})
        except subprocess.TimeoutExpired as exc:
            raise ResidentError('launchctl_' + operation + '_timeout') from exc
        except OSError as exc:
            raise ResidentError('launchctl_unavailable') from exc

    def require(self, operation: str, *arguments: str) -> None:
        if self.run(operation, *arguments).returncode != 0:
            # launchctl output may include inherited environment or private paths.
            raise ResidentError('launchctl_' + operation + '_failed')

    def loaded(self, registered: bool) -> dict:
        result = self.run('print', self.target)
        if result.returncode == SERVICE_NOT_FOUND:
            return {'loaded': False, 'running': False}
        if result.returncode != 0:
            raise ResidentError('launchctl_status_failed')
        if not registered:
            raise ResidentError('unrecognized_resident_job')
        # `print` is diagnostic text, not a stable API. Fail closed on a changed
        # format; never bootout/enable/kickstart a job whose identity is unclear.
        output = result.stdout
        path = re.search(r'(?m)^([ \t]+)path = (.+)$', output)
        if not path or path.group(2).strip() != str(self.plist):
            raise ResidentError('unrecognized_resident_job')
        indent = re.escape(path.group(1))
        program = re.search(r'(?m)^' + indent + r'program = (.+)$', output)
        arguments = re.search(r'(?m)^' + indent + r'arguments = \{\n(.*?)^' + indent + r'\}',
                              output, re.DOTALL)
        if (not program or program.group(1).strip() != str(self.executable)
                or not arguments
                or [line.strip() for line in arguments.group(1).splitlines() if line.strip()]
                != [str(self.executable), '--resident']):
            raise ResidentError('unrecognized_resident_job')
        state = re.search(r'(?m)^' + indent + r'state = (.+)$', output)
        return {'loaded': True, 'running': bool(state and state.group(1).strip() == 'running')}

    def status(self) -> dict:
        present = self.directories() and self.registered()
        return {'label': LABEL, 'registered': present, **self.loaded(present)}

    def write_new(self) -> None:
        fd, name = tempfile.mkstemp(prefix='.' + LABEL + '-', dir=self.agents)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                plistlib.dump(self.definition(), stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            # Exclusive creation prevents replacing an unknown existing file.
            os.link(temporary, self.plist)
        finally:
            temporary.unlink(missing_ok=True)

    def enable(self) -> dict:
        self.verify_app()
        self.directories(create=True)
        with self.lock():
            present = self.registered()
            job = self.loaded(present)
            if not present:
                self.write_new()
            self.require('enable', self.target)
            if job['loaded']:
                # No -k: a running instance is never killed by registration.
                self.require('kickstart', self.target)
            else:
                self.require('bootstrap', self.domain, str(self.plist))
            result = self.status()
            if not result['loaded']:
                raise ResidentError('resident_load_not_observed')
            return result

    def disable(self) -> dict:
        if not self.directories():
            return self.status()
        with self.lock():
            present = self.registered()
            job = self.loaded(present)
            if present:
                # Remove login registration before bootout: a menu invocation may
                # be a child of the resident job and get terminated with it.
                if not self.registered():
                    raise ResidentError('resident_registration_changed')
                self.plist.unlink()
            try:
                if job['loaded']:
                    self.require('bootout', self.target)
                    if self.loaded(present)['loaded']:
                        raise ResidentError('resident_unload_not_observed')
            except Exception:
                # An observable failure restores only our removed definition;
                # exclusive creation still refuses a newly appeared foreign file.
                if present:
                    self.write_new()
                raise
            # No domain-wide disable, remove, or persistent override is used.
            return {'label': LABEL, 'registered': False, 'loaded': False, 'running': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('enable', 'disable', 'status'))
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('macOS is required')
    try:
        result = getattr(ResidentAgent(), args.action)()
    except Exception as exc:
        # Only our closed error vocabulary is public; never print raw stderr.
        code = str(exc) if isinstance(exc, ResidentError) else type(exc).__name__
        print(json.dumps({'ok': False, 'error': code}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
