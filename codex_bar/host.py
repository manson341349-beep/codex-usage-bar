"""Owned, isolated macOS Codex lifecycle. Importing this module launches nothing.

The profile stays in this project's private .state directory. There is deliberately
no API accepting an app path, existing PID, profile path, port, or WebSocket URL.
A context acquires only the manager lock; call stop() explicitly to close an app.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import stat
import subprocess
import tempfile
import time
from typing import Iterator

APP = Path('/Applications/Codex.app')
EXECUTABLE = APP / 'Contents/MacOS/ChatGPT'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEXICAL_PROJECT_ROOT = Path(__file__).absolute().parent.parent
STATE_VERSION = 1
INSTALL_RECEIPT_VERSION = 1


def installation_paths() -> dict[str, Path]:
    """Fixed per-account locations, independent of HOME or CLI path overrides."""
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if not account_home.is_absolute() or account_home.is_symlink():
        raise HostError('account_home_not_canonical')
    app = account_home / 'Applications/codex-usage-bar.app'
    support = account_home / 'Library/Application Support/codex-usage-bar'
    return {'home': account_home, 'app': app,
            'resources': app / 'Contents/Resources/codex-usage-bar',
            'support': support, 'receipt': support / 'install.json'}


class HostError(RuntimeError):
    """A short, non-sensitive failure code safe for the manager UI."""


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    started: str
    executable: str
    state: str = ''

    @property
    def fingerprint(self) -> tuple[str, str]:
        return (self.started, self.executable)

    @property
    def is_alive(self) -> bool:
        # macOS ps(1): Z is a dead process awaiting its parent's wait/reap.
        # A stopped (T) or exiting-but-not-dead (SE) process is still live.
        return not self.state.startswith('Z')


@dataclass(frozen=True)
class OwnedEndpoint:
    pid: int
    port: int | None
    profile_root: Path

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise HostError('debugging_not_enabled')
        return f'http://127.0.0.1:{self.port}'


class System:
    """Only metadata inspection and explicit lifecycle operations; no UI reads."""

    def snapshot(self) -> dict[int, Process]:
        try:
            result = subprocess.run(
                ['/bin/ps', '-axo', 'pid=,ppid=,pgid=,lstart=,state=,comm='],
                check=True, capture_output=True, text=True, timeout=5)
            rows = {}
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 9)
                if len(parts) != 10 or not parts[8]:
                    raise HostError('process_metadata_unparseable')
                pid, ppid, pgid = map(int, parts[:3])
                rows[pid] = Process(pid, ppid, pgid, ' '.join(parts[3:8]), parts[9], parts[8])
            return rows
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise HostError('process_inspection_failed') from exc

    def command(self, pid: int) -> str:
        try:
            r = subprocess.run(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command='],
                               capture_output=True, text=True, timeout=3, check=True)
            return r.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostError('command_inspection_failed') from exc

    def listeners(self, port: int | None = None,
                  pids: set[int] | None = None) -> list[tuple[int, str]]:
        args = ['/usr/sbin/lsof', '-nP']
        if pids is not None:
            if not pids:
                return []
            args += ['-a', '-p', ','.join(map(str, sorted(pids)))]
        args += ['-iTCP' + (f':{port}' if port is not None else ''),
                 '-sTCP:LISTEN', '-FpPn']
        r = self._lsof(args)
        pid, answer = None, []
        for line in r.splitlines():
            if line.startswith('p'):
                try:
                    pid = int(line[1:])
                except ValueError as exc:
                    raise HostError('listener_metadata_unparseable') from exc
            elif line.startswith('n'):
                if pid is None:
                    raise HostError('listener_metadata_unparseable')
                answer.append((pid, line[1:]))
        return answer

    def open_files(self, pids: set[int]) -> list[str]:
        if not pids:
            return []
        output = self._lsof(['/usr/sbin/lsof', '-nP', '-a', '-p',
                            ','.join(map(str, sorted(pids))), '-Fn'])
        # Inspect path names only, not file contents. Never log these paths.
        return [line[1:] for line in output.splitlines() if line.startswith('n')]

    def profile_users(self, profile_root: Path) -> set[int]:
        """Detect unrecorded orphans using this private tree; file names only."""
        output = self._lsof(['/usr/sbin/lsof', '-nP', '+D', str(profile_root), '-Fp'])
        try:
            return {int(line[1:]) for line in output.splitlines() if line.startswith('p')}
        except ValueError as exc:
            raise HostError('profile_owner_inspection_failed') from exc

    @staticmethod
    def _lsof(args: list[str]) -> str:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostError('open_file_inspection_failed') from exc
        if r.returncode not in (0, 1):
            raise HostError('open_file_inspection_failed')
        return r.stdout

    def verify_signature(self) -> None:
        try:
            r = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(APP)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostError('signature_verification_failed') from exc
        if r.returncode:
            raise HostError('signature_verification_failed')

    @contextmanager
    def reserve_port(self) -> Iterator[int]:
        # Electron cannot inherit this socket. Release immediately before spawn;
        # actual loopback listener ownership is checked before any CDP connection.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(('127.0.0.1', 0))
            yield reservation.getsockname()[1]

    def spawn(self, args: list[str], env: dict[str, str], cwd: Path):
        return subprocess.Popen(args, env=env, cwd=cwd, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

    def send_signal(self, pid: int, sig: int, *, group: bool = False) -> None:
        try:
            (os.killpg if group else os.kill)(pid, sig)
        except ProcessLookupError:
            pass

    def stop_created_child(self, proc) -> None:
        """Stop this Popen's direct child without relying on a saved PID record.

        Popen polls/waits its own unreaped child before sending a signal. A live
        unreaped child's PID cannot have been reused. Never use killpg here:
        descendants have not necessarily been fingerprinted yet.
        """
        try:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=1)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HostError('created_child_cleanup_failed') from exc

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


def minimal_environment(profile_root: Path) -> dict[str, str]:
    """Keep HOME unchanged; exclude API tokens and flavor/debug override variables."""
    env = {key: os.environ[key] for key in (
        'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'LANG', 'LC_CTYPE',
        '__CF_USER_TEXT_ENCODING') if key in os.environ}
    if not env.get('HOME'):
        raise HostError('home_environment_missing')
    env.update({'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                'CODEX_HOME': str(profile_root / 'codex-home'),
                'CODEX_ELECTRON_USER_DATA_PATH': str(profile_root / 'electron-profile')})
    return env


def build_install_receipt(*, reuse_approved_profile: bool = False) -> dict:
    """Read-only installer binding: no arbitrary profile path and no credential copy.

    Import this helper from the source package being installed. The installer owns
    writing this result atomically to install.json with mode 0600 in its fixed
    0700 support directory; it must also refuse an active installed manager lock.
    """
    if type(reuse_approved_profile) is not bool:
        raise HostError('invalid_profile_binding_option')
    host = ManagedHost(approved_previous_profile=reuse_approved_profile,
                       _project_root=PROJECT_ROOT)
    if host.installed:
        raise HostError('installation_receipt_requires_source_package')
    host._forbid_daily_profile(host.project)
    host._owned_directory_chain(host.project)
    layout = host.installation
    if reuse_approved_profile:
        host._owned_directory_chain(host.profile_root)
        if host.system.profile_users(host.profile_root):
            raise HostError('approved_install_profile_in_use')
        profile = host.profile_root
        manifest = host.project.parent / 'codex-usage-pet/.build/private-login/active-session.json'
    else:
        profile = layout['support'] / 'private-session'
        manifest = None
    return {'version': INSTALL_RECEIPT_VERSION, 'appPath': str(layout['app']),
            'resourceRoot': str(layout['resources']), 'sourceRoot': str(host.project),
            'profileBinding': 'approved_previous' if reuse_approved_profile else 'fresh',
            'profileRoot': str(profile),
            'approvedManifestPath': str(manifest) if manifest is not None else None}


class ManagedHost:
    """Launch/attach/stop only the isolated instance recorded by this project.

    _system and _project_root are test seams, not command-line configuration.
    No credential contents are read, copied, or removed by this class.
    """

    def __init__(self, *, approved_previous_profile: bool = False,
                 _system: System | None = None,
                 _project_root: Path | None = None):
        if type(approved_previous_profile) is not bool:
            raise HostError('invalid_profile_binding_option')
        self.system = _system or System()
        candidate = _project_root or LEXICAL_PROJECT_ROOT
        self.project = candidate.resolve(strict=True)
        self.installation = installation_paths()
        expected_resources = self.installation['resources']
        self.installed = candidate.absolute() == expected_resources or self.project == expected_resources
        bundle_suffix = ('codex-usage-bar.app', 'Contents', 'Resources', 'codex-usage-bar')
        if tuple(candidate.parts[-4:]) == bundle_suffix and not self.installed:
            raise HostError('app_not_installed_at_expected_path')
        if self.installed and self.project != expected_resources:
            raise HostError('installed_resource_path_mismatch')
        self.state_root = self.project / '.state'
        self.approved_previous_profile = approved_previous_profile
        self._approved_source_project = self.project
        if self.installed:
            self.state_root = self.installation['support']
            self._bind_installation_receipt(approved_previous_profile)
        else:
            self.profile_root = (self._resolve_approved_previous_profile() if approved_previous_profile
                                 else self.state_root / 'private-session')
        self.state_file = self.state_root / 'host.json'
        self._lock_fd: int | None = None
        self._proc = None
        self.last_launch_failure: dict | None = None

    @classmethod
    def reuse_approved_profile(cls, **test_seams):
        """Bind only the earlier approved dedicated profile, without copying it."""
        return cls(approved_previous_profile=True, **test_seams)

    @staticmethod
    def _existing_directory(path: Path, *, private: bool) -> None:
        try:
            info = path.lstat()
        except OSError as exc:
            raise HostError('approved_profile_directory_missing') from exc
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid() or (mode != 0o700 if private else bool(mode & 0o022))):
            raise HostError('unsafe_approved_profile_directory')

    def _resolve_approved_previous_profile(self) -> Path:
        """Read only the fixed predecessor's own lifecycle manifest, not auth data."""
        previous_project = self._approved_source_project.parent / 'codex-usage-pet'
        base = previous_project / '.build' / 'private-login'
        for ancestor in (previous_project, previous_project / '.build'):
            self._existing_directory(ancestor, private=False)
        self._existing_directory(base, private=True)
        manifest = base / 'active-session.json'
        try:
            fd = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise HostError('approved_profile_manifest_unavailable') from exc
        try:
            self._check_private_file(fd)
            raw = os.read(fd, 65537)
            if len(raw) > 65536:
                raise HostError('approved_profile_manifest_size_limit')
            record = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise HostError('invalid_approved_profile_manifest') from exc
        finally:
            os.close(fd)
        if (not isinstance(record, dict) or record.get('status') != 'debug_test_closed'
                or record.get('debugCleanupVerified') is not True):
            raise HostError('approved_profile_previous_debugger_not_closed')
        raw_path = record.get('profileRoot')
        if not isinstance(raw_path, str):
            raise HostError('approved_profile_path_invalid')
        profile = Path(raw_path)
        if (not profile.is_absolute() or str(profile) != raw_path or profile.parent != base
                or re.fullmatch(r'session-[a-z0-9_]{8,32}', profile.name) is None):
            raise HostError('approved_profile_path_invalid')
        if record.get('manifestPath', str(manifest)) != str(manifest):
            raise HostError('approved_profile_manifest_path_mismatch')
        self._existing_directory(profile, private=True)
        for name in ('codex-home', 'electron-profile', 'working-directory'):
            self._existing_directory(profile / name, private=True)
        return profile

    @staticmethod
    def _canonical_path(value, error: str) -> Path:
        if not isinstance(value, str):
            raise HostError(error)
        path = Path(value)
        if not path.is_absolute() or str(path) != value or '..' in path.parts:
            raise HostError(error)
        return path

    def _owned_directory_chain(self, path: Path) -> None:
        """Validate every component below the OS account home, without following links."""
        account_home = self.installation['home']
        try:
            parts = path.relative_to(account_home).parts
        except ValueError as exc:
            raise HostError('installation_path_outside_account_home') from exc
        current = account_home
        self._existing_directory(current, private=False)
        for part in parts:
            current /= part
            self._existing_directory(current, private=False)

    def _forbid_daily_profile(self, path: Path) -> None:
        candidate = str(path).casefold()
        for protected in (self.installation['home'] / '.codex',
                          self.installation['home'] / 'Library/Application Support/Codex'):
            value = str(protected).casefold()
            if candidate == value or candidate.startswith(value + '/'):
                raise HostError('daily_profile_binding_forbidden')

    def _bind_installation_receipt(self, requested_reuse: bool) -> None:
        self._owned_directory_chain(self.project)
        self._owned_directory_chain(self.state_root)
        self._existing_directory(self.state_root, private=True)
        try:
            fd = os.open(self.installation['receipt'], os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise HostError('installation_receipt_unavailable') from exc
        try:
            self._check_private_file(fd)
            raw = os.read(fd, 65537)
            if len(raw) > 65536:
                raise HostError('installation_receipt_size_limit')
            receipt = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise HostError('invalid_installation_receipt') from exc
        finally:
            os.close(fd)
        if (not isinstance(receipt, dict) or type(receipt.get('version')) is not int
                or receipt['version'] != INSTALL_RECEIPT_VERSION):
            raise HostError('invalid_installation_receipt')
        if (receipt.get('appPath') != str(self.installation['app'])
                or receipt.get('resourceRoot') != str(self.project)):
            raise HostError('installation_receipt_app_mismatch')
        source = self._canonical_path(receipt.get('sourceRoot'), 'installation_source_path_invalid')
        self._forbid_daily_profile(source)
        try:
            source.relative_to(self.installation['home'])
        except ValueError as exc:
            raise HostError('installation_source_outside_account_home') from exc
        profile = self._canonical_path(receipt.get('profileRoot'), 'installation_profile_path_invalid')
        self._forbid_daily_profile(profile)
        binding = receipt.get('profileBinding')
        if binding == 'approved_previous':
            self._approved_source_project = source
            expected_manifest = source.parent / 'codex-usage-pet/.build/private-login/active-session.json'
            if receipt.get('approvedManifestPath') != str(expected_manifest):
                raise HostError('installation_approved_manifest_mismatch')
            self._owned_directory_chain(expected_manifest.parent)
            approved = self._resolve_approved_previous_profile()
            if profile != approved:
                raise HostError('installation_approved_profile_mismatch')
            self.approved_previous_profile = True
            self.profile_root = approved
        elif binding == 'fresh':
            if requested_reuse or profile != self.state_root / 'private-session':
                raise HostError('installation_fresh_profile_mismatch')
            if receipt.get('approvedManifestPath') is not None:
                raise HostError('installation_fresh_manifest_forbidden')
            self.approved_previous_profile = False
            self.profile_root = profile
        else:
            raise HostError('installation_profile_binding_invalid')

    @staticmethod
    def _private_directory(path: Path) -> None:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        s = path.lstat()
        if not stat.S_ISDIR(s.st_mode) or stat.S_ISLNK(s.st_mode):
            raise HostError('unsafe_state_directory')
        if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
            raise HostError('state_directory_not_private')

    def __enter__(self):
        if self._lock_fd is not None:
            raise HostError('manager_lock_already_held')
        self._private_directory(self.state_root)
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        try:
            fd = os.open(self.state_root / 'manager.lock', flags, 0o600)
        except OSError as exc:
            raise HostError('unsafe_manager_lock') from exc
        try:
            self._check_private_file(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise HostError('another_manager_is_running') from exc
        except BaseException:
            os.close(fd)
            raise
        self._lock_fd = fd
        return self

    def __exit__(self, *_):
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    @staticmethod
    def _check_private_file(fd: int) -> None:
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1):
            raise HostError('unsafe_state_file')

    def _require_lock(self) -> None:
        if self._lock_fd is None:
            raise HostError('manager_lock_required')
        self._private_directory(self.state_root)
        current = (self.state_root / 'manager.lock').lstat()
        held = os.fstat(self._lock_fd)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise HostError('manager_lock_identity_changed')

    def _prepare_profile(self) -> None:
        if self.approved_previous_profile:
            # Never create, repair, copy, or relocate the existing login directories.
            if self.installed:
                self._owned_directory_chain(self.profile_root)
            self._existing_directory(self.profile_root, private=True)
            for name in ('codex-home', 'electron-profile', 'working-directory'):
                self._existing_directory(self.profile_root / name, private=True)
            return
        self._private_directory(self.profile_root)
        for name in ('codex-home', 'electron-profile', 'working-directory'):
            self._private_directory(self.profile_root / name)

    def _load(self) -> dict | None:
        self._require_lock()
        try:
            fd = os.open(self.state_file, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise HostError('unsafe_state_file') from exc
        try:
            self._check_private_file(fd)
            raw = os.read(fd, 262145)
            if len(raw) > 262144:
                raise HostError('state_size_limit')
            state = json.loads(raw)
            self._validate_state(state)
            return state
        except (ValueError, TypeError, KeyError) as exc:
            raise HostError('invalid_host_state') from exc
        finally:
            os.close(fd)

    def _validate_state(self, state: dict) -> None:
        if not isinstance(state, dict) or state.get('version') != STATE_VERSION:
            raise HostError('invalid_host_state')
        if state.get('profileRoot') != str(self.profile_root):
            raise HostError('state_profile_mismatch')
        if state.get('status') not in ('starting', 'running', 'stopped'):
            raise HostError('invalid_host_state')
        if type(state.get('pid')) is not int or state['pid'] <= 1:
            raise HostError('invalid_host_state')
        port = state.get('port')
        if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
            raise HostError('invalid_host_state')
        known = state.get('known')
        if not isinstance(known, dict) or str(state['pid']) not in known:
            raise HostError('invalid_host_state')
        for pid, fp in known.items():
            if not pid.isdecimal() or int(pid) <= 1 or not isinstance(fp, list) or len(fp) != 2:
                raise HostError('invalid_host_state')
            if not all(isinstance(v, str) and v for v in fp):
                raise HostError('invalid_host_state')
        if known[str(state['pid'])][1] != str(EXECUTABLE):
            raise HostError('state_executable_mismatch')
        initial = state.get('initialPids')
        if not isinstance(initial, list) or any(type(v) is not int for v in initial):
            raise HostError('invalid_host_state')
        if set(map(int, known)).intersection(initial):
            raise HostError('preexisting_pid_in_owned_state')

    def _save(self, state: dict) -> None:
        self._require_lock()
        self._validate_state(state)
        fd, name = tempfile.mkstemp(prefix='.host-', suffix='.tmp', dir=self.state_root)
        target = Path(name)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(state, stream, separators=(',', ':'))
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(target, self.state_file)
        finally:
            # This path was created by mkstemp for this call, never an old file.
            try:
                target.unlink()
            except FileNotFoundError:
                pass

    def _args(self, port: int | None) -> list[str]:
        args = [str(EXECUTABLE), '--user-data-dir=' + str(self.profile_root / 'electron-profile')]
        if port is not None:
            args += ['--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}']
        return args

    def _owned(self, state: dict, rows: dict[int, Process], *, discover: bool,
               require_known_group: bool = True) -> set[int]:
        # Dead children can change ps's command spelling to '(name)' before
        # reaping. They are neither live ownership failures nor signal targets.
        rows = {pid: row for pid, row in rows.items() if row.is_alive}
        known = state['known']
        owned = {int(pid) for pid, fp in known.items()
                 if int(pid) in rows and list(rows[int(pid)].fingerprint) == fp}
        main = state['pid']
        if main in owned:
            if rows[main].pgid != main or self.system.command(main) != ' '.join(self._args(state['port'])):
                raise HostError('owned_command_mismatch')
        # New descendants are accepted only from a live, fingerprinted ancestor.
        if discover:
            while True:
                extra = {pid for pid, row in rows.items() if row.ppid in owned}
                if extra.issubset(owned):
                    break
                if extra.intersection(state['initialPids']):
                    raise HostError('preexisting_process_in_owned_tree')
                owned |= extra
            for pid in owned:
                known[str(pid)] = list(rows[pid].fingerprint)
        group = {pid for pid, row in rows.items() if row.pgid == main}
        if require_known_group and not group.issubset(owned):
            raise HostError('unverified_process_in_owned_group')
        if owned.intersection(state['initialPids']):
            raise HostError('preexisting_process_in_owned_tree')
        return owned

    def _guard(self, state: dict, *, require_listener: bool = True) -> set[int]:
        self._prepare_profile()
        # ps and lsof are separate observations: Chromium can create the actual
        # listener process between them. Refresh twice, proving ancestry afresh;
        # a loopback address alone never grants ownership of a newly seen PID.
        for attempt in range(3):
            rows = self.system.snapshot()
            owned = self._owned(state, rows, discover=True)
            main = state['pid']
            if main not in owned or rows[main].pgid != main:
                raise HostError('owned_main_process_not_running')
            if self.system.command(main) != ' '.join(self._args(state['port'])):
                raise HostError('owned_command_mismatch')
            opened = self.system.open_files(owned)
            home = self.installation['home']
            forbidden = (home / '.codex', home / 'Library/Application Support/Codex')
            for name in opened:
                if any(name == str(p) or name.startswith(str(p) + '/') for p in forbidden):
                    raise HostError('daily_profile_opened')
            port = state['port']
            endpoints = self.system.listeners(port) if port is not None else self.system.listeners(pids=owned)
            if port is None and endpoints:
                raise HostError('unexpected_listener_during_login')
            if any(address != f'127.0.0.1:{port}' for _, address in endpoints):
                raise HostError('debug_listener_not_owned_loopback')
            if any(pid not in owned for pid, _ in endpoints):
                if attempt < 2:
                    continue
                raise HostError('debug_listener_not_owned_loopback')
            if port is not None and require_listener and not endpoints:
                raise HostError('owned_debug_listener_missing')
            self._save(state)
            return owned
        raise HostError('debug_listener_not_owned_loopback')  # Defensive unreachable guard.

    def attach(self) -> OwnedEndpoint:
        """Read-only app validation; writes only refreshed own PID metadata."""
        state = self._load()
        if state is None or state['status'] == 'stopped':
            raise HostError('no_running_owned_instance')
        self.system.verify_signature()
        self._guard(state)
        self.system.verify_signature()
        return OwnedEndpoint(state['pid'], state['port'], self.profile_root)

    def validate(self) -> OwnedEndpoint:
        """Call before each debugger operation; no arbitrary endpoints accepted."""
        state = self._load()
        if state is None or state['status'] == 'stopped':
            raise HostError('no_running_owned_instance')
        self._guard(state)
        return OwnedEndpoint(state['pid'], state['port'], self.profile_root)

    def status(self) -> dict:
        """Return only lifecycle metadata. A stale state does not authorize a PID."""
        state = self._load()
        if state is None:
            return self._missing_state_status()
        owned = self._owned(state, self.system.snapshot(), discover=True)
        running = state['pid'] in owned
        return {'status': ('running' if running else 'orphaned' if owned else 'stopped'),
                'pid': state['pid'] if running else None, 'port': state['port'] if owned else None,
                'profileRetained': True, 'ownedProcessCount': len(owned)}

    def _missing_state_status(self) -> dict:
        """Missing authority never means a previous failed launch was cleaned."""
        retained = self.profile_root.exists() or self.profile_root.is_symlink()
        users = set()
        if retained:
            self._prepare_profile()
            users = self.system.profile_users(self.profile_root)
        pending_recovery = False
        recovery_path = self.state_root / 'launch-failure.json'
        try:
            fd = os.open(recovery_path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            fd = None
        except OSError as exc:
            raise HostError('unsafe_recovery_metadata') from exc
        if fd is not None:
            try:
                self._check_private_file(fd)
                raw = os.read(fd, 16385)
                if len(raw) > 16384:
                    raise HostError('recovery_metadata_size_limit')
                evidence = json.loads(raw)
                if (not isinstance(evidence, dict)
                        or evidence.get('profileRoot') != str(self.profile_root)):
                    raise HostError('invalid_recovery_metadata')
                pending_recovery = evidence.get('status') == 'cleanup_unverified'
            except (ValueError, TypeError) as exc:
                raise HostError('invalid_recovery_metadata') from exc
            finally:
                os.close(fd)
        return {'status': 'cleanup_unverified' if users or pending_recovery else 'not_created',
                'profileRetained': retained, 'unrecordedProfileUserCount': len(users),
                'pendingRecoveryEvidence': pending_recovery}

    def launch(self, *, debug: bool = True, timeout: float = 35) -> OwnedEndpoint:
        """Explicit action: open signed Codex with only this project's private state."""
        self._require_lock()
        if type(debug) is not bool or not 0 < timeout <= 60:
            raise HostError('invalid_launch_options')
        previous = self._load()
        if previous is None and self._missing_state_status()['status'] == 'cleanup_unverified':
            raise HostError('missing_host_state_cleanup_unverified')
        if previous:
            rows = self.system.snapshot()
            if self._owned(previous, rows, discover=True):
                raise HostError('owned_instance_or_orphan_already_running')
            if previous['port'] is not None and self.system.listeners(previous['port']):
                raise HostError('stale_state_port_in_use')
        self._prepare_profile()
        if self.system.profile_users(self.profile_root):
            raise HostError('private_profile_already_in_use')
        self.system.verify_signature()
        initial = self.system.snapshot()
        port = None
        if debug:
            with self.system.reserve_port() as chosen:
                port = chosen
                if self.system.listeners(port):
                    raise HostError('selected_port_not_free')
        # Reservation is intentionally closed immediately before spawning.
        self._proc = self.system.spawn(self._args(port), minimal_environment(self.profile_root),
                                       self.profile_root / 'working-directory')
        state = None
        try:
            rows = self.system.snapshot()
            main = rows.get(self._proc.pid)
            if (main is None or not main.is_alive or main.pid in initial or main.pgid != main.pid
                    or main.executable != str(EXECUTABLE)):
                raise HostError('new_main_identity_not_verified')
            state = {'version': STATE_VERSION, 'status': 'starting', 'pid': main.pid,
                     'port': port, 'profileRoot': str(self.profile_root),
                     'known': {str(main.pid): list(main.fingerprint)},
                     'initialPids': sorted(initial)}
            self._save(state)
            until = self.system.monotonic() + timeout
            while True:
                self._guard(state, require_listener=False)
                if port is None or self.system.listeners(port):
                    break
                if self._proc.poll() is not None:
                    raise HostError('owned_process_exited_before_ready')
                if self.system.monotonic() >= until:
                    raise HostError('owned_listener_start_timeout')
                self.system.sleep(0.15)
            self._guard(state)
            self.system.verify_signature()
            state['status'] = 'running'
            self._save(state)
            return OwnedEndpoint(main.pid, port, self.profile_root)
        except BaseException as error:
            self._cleanup_failed_launch(state, port, error)
            raise

    def _cleanup_failed_launch(self, state: dict | None, port: int | None,
                               original_error: BaseException) -> None:
        """Close the spawn-to-state-write transaction gap without broad signals."""
        cleanup_errors = []
        if state is not None:
            try:
                # This verified in-memory state remains authoritative even if
                # its first atomic write failed. Do not reload an absent file.
                self._stop_state(state)
            except (HostError, OSError) as error:
                cleanup_errors.append(type(error).__name__)
        try:
            self.system.stop_created_child(self._proc)
        except (HostError, OSError) as error:
            cleanup_errors.append(type(error).__name__)
        verified = False
        try:
            rows = self.system.snapshot()
            group_remains = any(row.pgid == self._proc.pid and row.is_alive for row in rows.values())
            known_remains = bool(state and self._owned(state, rows, discover=True))
            profile_in_use = bool(self.system.profile_users(self.profile_root))
            listener_remains = bool(port is not None and self.system.listeners(port))
            verified = (self._proc.poll() is not None and not group_remains
                        and not known_remains and not profile_in_use and not listener_remains)
        except (HostError, OSError):
            cleanup_errors.append('cleanup_verification_failed')
        self.last_launch_failure = {
            'status': 'startup_failed_cleanup_verified' if verified else 'cleanup_unverified',
            'directChildPid': self._proc.pid, 'port': port,
            'profileRoot': str(self.profile_root), 'profileRetained': True,
            'errorType': type(original_error).__name__, 'cleanupErrors': cleanup_errors,
            'unverifiedProcessGroupsSignalled': False,
        }
        if not verified:
            saved = self._save_recovery(self.last_launch_failure)
            suffix = 'recovery_saved' if saved else 'recovery_not_saved'
            raise HostError('launch_failed_cleanup_unverified_' + suffix) from original_error

    def _save_recovery(self, evidence: dict) -> bool:
        """Best effort, non-authoritative metadata; never used as attach authority."""
        path = None
        try:
            self._require_lock()
            fd, name = tempfile.mkstemp(prefix='.recovery-', suffix='.tmp', dir=self.state_root)
            path = Path(name)
            with os.fdopen(fd, 'w') as stream:
                json.dump(evidence, stream, separators=(',', ':'))
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.state_root / 'launch-failure.json')
            return True
        except (HostError, OSError):
            return False
        finally:
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass

    def stop(self) -> dict:
        """Terminate only a freshly rechecked owned tree; never delete its profile."""
        state = self._load()
        if state is None:
            status = self._missing_state_status()
            if status['status'] == 'cleanup_unverified':
                raise HostError('missing_host_state_cleanup_unverified')
            self.system.verify_signature()
            self.system.verify_signature()
            return status
        return self._stop_state(state)

    def _stop_state(self, state: dict) -> dict:
        """Cleanup must not depend on a successful initial state-file write."""
        self._validate_state(state)
        signature_failure = False
        try:
            self.system.verify_signature()
        except HostError:
            # A failed integrity check must prevent launching, but must not leave
            # an already fingerprinted debugger running when cleanup is requested.
            signature_failure = True
        self._prepare_profile()
        for sig, duration in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 1.0)):
            rows = self.system.snapshot()
            owned = self._owned(state, rows, discover=True, require_known_group=False)
            try:
                self._save(state)
            except OSError:
                # Disk pressure must not strand a verified running debugger.
                pass
            # Discover children born after the preceding snapshot, but only from
            # a verified live ancestor. Unknown group members never become owned
            # merely because they share a PGID or have been reparented to PID 1.
            current = self.system.snapshot()
            current_owned = self._owned(state, current, discover=True, require_known_group=False)
            current_group = {pid for pid, row in current.items() if row.pgid == state['pid']}
            safe_group = current_group if current_group.issubset(current_owned) else set()
            if safe_group:
                self.system.send_signal(state['pid'], sig, group=True)
            # If a group contains an unverified member, close verified processes
            # individually instead of leaving their debugger open. Report any
            # unverified leftovers below; never signal those leftovers.
            for pid in sorted(current_owned - safe_group):
                current = self.system.snapshot()
                if pid in self._owned(state, current, discover=True, require_known_group=False):
                    self.system.send_signal(pid, sig)
            until = self.system.monotonic() + duration
            while self.system.monotonic() < until:
                if self._proc is not None:
                    self._proc.poll()
                settling = self.system.snapshot()
                known_remains = self._owned(state, settling, discover=True, require_known_group=False)
                live_group_remains = any(row.pgid == state['pid'] and row.is_alive
                                         for row in settling.values())
                # Unknown members receive no signal, but may be finishing their
                # normal exit after the verified parent closes. Allow the same
                # bounded grace interval before reporting unresolved leftovers.
                if not known_remains and not live_group_remains:
                    break
                self.system.sleep(0.1)
            if not self._owned(state, self.system.snapshot(), discover=True, require_known_group=False):
                break
        final_rows = self.system.snapshot()
        if self._owned(state, final_rows, discover=True, require_known_group=False):
            raise HostError('owned_process_cleanup_incomplete')
        if any(row.pgid == state['pid'] and row.is_alive for row in final_rows.values()):
            raise HostError('unverified_processes_remain_after_owned_cleanup')
        if state['port'] is not None and self.system.listeners(state['port']):
            raise HostError('debug_port_not_closed')
        if self.system.profile_users(self.profile_root):
            raise HostError('untracked_private_profile_process')
        try:
            self.system.verify_signature()
        except HostError:
            signature_failure = True
        state['status'] = 'stopped'
        try:
            self._save(state)
        except OSError as exc:
            raise HostError('owned_instance_stopped_state_update_failed') from exc
        if signature_failure:
            raise HostError('signature_verification_failed_owned_instance_stopped')
        if (self.state_root / 'launch-failure.json').exists():
            self._save_recovery({'status': 'resolved_after_verified_stop',
                                 'profileRoot': str(self.profile_root), 'profileRetained': True})
        return {'status': 'stopped', 'profileRetained': True, 'debugPortClosed': True,
                'signatureValidAfter': True}
