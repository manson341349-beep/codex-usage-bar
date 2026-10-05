"""Attach the bar to the account's existing daily Codex profile.

The wrapper owns its manager and debugger connection, never the user's app or
work.  No method sends signals, copies login data, or edits a Codex profile.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets

from .host import (EXECUTABLE, HostError, LEXICAL_PROJECT_ROOT, ManagedHost,
                   OwnedEndpoint, System, installation_paths)


def daily_environment(home: Path, quota_home: Path, profile_root: Path) -> dict[str, str]:
    """Bind the existing default profile without inheriting secret/launch overrides."""
    env = {key: os.environ[key] for key in (
        'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'LANG', 'LC_CTYPE',
        '__CF_USER_TEXT_ENCODING') if key in os.environ}
    env.update({'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
                'CODEX_HOME': str(quota_home),
                'CODEX_ELECTRON_USER_DATA_PATH': str(profile_root)})
    return env


class DailyHost(ManagedHost):
    """A separate lifecycle using shared metadata, locking and process helpers.

    In particular, this does not call ManagedHost.__init__, launch, stop or its
    failure cleanup: those methods own and terminate an isolated test app.
    """

    def __init__(self, *, _system: System | None = None,
                 _project_root: Path | None = None):
        self.system = _system or System()
        candidate = _project_root or LEXICAL_PROJECT_ROOT
        self.project = candidate.resolve(strict=True)
        self.installation = installation_paths()
        resources = self.installation['resources']
        self.installed = candidate.absolute() == resources or self.project == resources
        suffix = ('codex-usage-bar.app', 'Contents', 'Resources', 'codex-usage-bar')
        if tuple(candidate.parts[-4:]) == suffix and not self.installed:
            raise HostError('app_not_installed_at_expected_path')
        if self.installed and self.project != resources:
            raise HostError('installed_resource_path_mismatch')
        self.state_root = self.installation['support'] if self.installed else self.project / '.state'
        self.profile_root = self.installation['home'] / 'Library/Application Support/Codex'
        self.quota_home = self.installation['home'] / '.codex'
        self.state_file = self.state_root / 'daily-host.json'
        self._lock_fd = None
        self._proc = None
        self.last_launch_failure = None
        if self.installed:
            self._validate_installation_identity()

    def _validate_installation_identity(self):
        # Legacy isolated bindings are intentionally not resolved. Their login
        # directories need not exist for this independently selected daily mode.
        self._owned_directory_chain(self.project)
        self._owned_directory_chain(self.state_root)
        record = self._read_private_record(self.installation['receipt'])
        if (not isinstance(record, dict) or type(record.get('version')) is not int
                or record['version'] not in (1, 2)
                or record.get('appPath') != str(self.installation['app'])
                or record.get('resourceRoot') != str(self.project)):
            raise HostError('installation_receipt_app_mismatch')

    def _read_private_record(self, path: Path, *, optional=False):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if optional:
                return None
            raise HostError('installation_receipt_unavailable') from None
        except OSError:
            raise HostError('unsafe_state_file') from None
        try:
            self._check_private_file(fd)
            raw = os.read(fd, 262145)
            if len(raw) > 262144:
                raise HostError('state_size_limit')
            return json.loads(raw)
        except (ValueError, TypeError):
            raise HostError('invalid_host_state') from None
        finally:
            os.close(fd)

    def _prepare_profile(self):
        # Existing user directories may be 0755. Never mkdir/chmod/repair them.
        for path in (self.quota_home, self.profile_root):
            try:
                self._owned_directory_chain(path)
            except HostError:
                raise HostError('daily_profile_missing_or_unsafe') from None

    def _args(self, port: int | None) -> list[str]:
        if type(port) is not int or not 1024 <= port <= 65535:
            raise HostError('invalid_daily_debug_port')
        return [str(EXECUTABLE), '--user-data-dir=' + str(self.profile_root),
                '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}']

    def _validate_state(self, state: dict) -> None:
        if (not isinstance(state, dict) or state.get('mode') != 'daily'
                or state.get('quotaHome') != str(self.quota_home)
                or state.get('status') not in ('starting', 'running', 'detached', 'stopped')):
            raise HostError('invalid_daily_host_state')
        if 'recoverySeed' in state and (type(state['recoverySeed']) is not str or
                re.fullmatch(r'[0-9a-f]{64}', state['recoverySeed']) is None):
            raise HostError('invalid_daily_recovery_seed')
        # Reuse schema/fingerprint checks without broadening isolated semantics.
        compatible = dict(state)
        if compatible['status'] == 'detached':
            compatible['status'] = 'running'
        super()._validate_state(compatible)
        self._args(state['port'])

    @staticmethod
    def _under(name: str, root: Path) -> bool:
        return name == str(root) or name.startswith(str(root) + '/')

    def _verified_isolated(self, rows) -> set[int]:
        """Ignore only a fingerprinted earlier wrapper with a proven private profile.

        Merely passing --user-data-dir does not prove isolation: Codex sets its
        own userData from the environment. Inspect only file path metadata.
        """
        verified = set()
        candidates = {self.project / '.state/host.json', self.installation['support'] / 'host.json'}
        for path in candidates:
            try:
                if not path.exists():
                    continue
                self._owned_directory_chain(path.parent)
                state = self._read_private_record(path)
                if not isinstance(state, dict) or state.get('version') != 1:
                    continue
                main = state.get('pid')
                known = state.get('known')
                initial = state.get('initialPids')
                if (type(main) is not int or not isinstance(known, dict)
                        or not isinstance(initial, list) or main not in rows
                        or not rows[main].is_alive or rows[main].executable != str(EXECUTABLE)
                        or rows[main].pgid != main
                        or known.get(str(main)) != list(rows[main].fingerprint)):
                    continue
                profile = self._canonical_path(state.get('profileRoot'), 'invalid_isolated_profile')
                self._forbid_daily_profile(profile)
                self._owned_directory_chain(profile / 'electron-profile')
                port = state.get('port')
                if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
                    continue
                args = [str(EXECUTABLE), '--user-data-dir=' + str(profile / 'electron-profile')]
                if port is not None:
                    args += ['--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}']
                if self.system.command(main) != ' '.join(args):
                    continue
                owned = {main}
                while True:
                    extra = {pid for pid, row in rows.items()
                             if row.is_alive and row.ppid in owned}
                    if extra.issubset(owned):
                        break
                    owned |= extra
                if owned.intersection(initial):
                    continue
                opened = self.system.open_files(owned)
                if (not any(self._under(name, profile / 'electron-profile') for name in opened)
                        or any(self._under(name, root) for name in opened
                               for root in (self.profile_root, self.quota_home))):
                    continue
                verified |= owned
            except (HostError, OSError, TypeError, ValueError):
                # Unverifiable isolation grants no exemption from the refusal.
                continue
        return verified

    def _external_users(self, rows, *, owned=None, scan_profile=True) -> set[int]:
        owned = owned or set()
        isolated = self._verified_isolated(rows)
        mains = {pid for pid, row in rows.items() if row.is_alive
                 and row.executable.endswith('/Codex.app/Contents/MacOS/ChatGPT')}
        # Only the Electron profile is exclusive. Existing official CLI workers
        # legitimately share ~/.codex; do not inspect its files or histories.
        profile_users = self.system.profile_users(self.profile_root) if scan_profile else set()
        return (mains - owned - isolated) | (profile_users - owned)

    def preflight(self) -> None:
        self._require_lock()
        self._prepare_profile()
        if self._external_users(self.system.snapshot()):
            raise HostError('daily_instance_running')

    def _guard(self, state: dict, *, require_listener=True) -> set[int]:
        self._prepare_profile()
        for attempt in range(3):
            rows = self.system.snapshot()
            owned = self._owned(state, rows, discover=True)
            if state['pid'] not in owned:
                raise HostError('daily_instance_not_running')
            # Recursive open-file enumeration belongs at launch/preflight only.
            # Runtime authority is the saved main identity, exact command, fresh
            # descendant tree and owned loopback listener, with no other main.
            if self._external_users(rows, owned=owned, scan_profile=False):
                raise HostError('daily_instance_running')
            endpoints = self.system.listeners(state['port'])
            if any(address != f"127.0.0.1:{state['port']}" for _, address in endpoints):
                raise HostError('debug_listener_not_owned_loopback')
            if any(pid not in owned for pid, _ in endpoints):
                if attempt < 2:
                    continue
                raise HostError('debug_listener_not_owned_loopback')
            if require_listener and not endpoints:
                raise HostError('owned_debug_listener_missing')
            self._save(state)
            return owned
        raise HostError('debug_listener_not_owned_loopback')

    def validate(self) -> OwnedEndpoint:
        state = self._load()
        if state is None or state['status'] == 'stopped':
            raise HostError('no_running_daily_instance')
        self._guard(state)
        return OwnedEndpoint(state['pid'], state['port'], self.profile_root)

    def recovery_seed(self) -> str:
        """Return authority only for a freshly verified host under our exclusive lock.

        Older records may lack a seed. Migrate only after the process, exact
        command and listener pass the same checks as a debugger operation.
        The private host record is the only persistent storage of this secret.
        """
        state = self._load()
        if state is None or state['status'] == 'stopped':
            raise HostError('no_running_daily_instance')
        self._guard(state)
        if 'recoverySeed' not in state:
            state['recoverySeed'] = secrets.token_hex(32)
            self._save(state)
        return state['recoverySeed']

    def is_running(self) -> bool:
        state = self._load()
        if state is None or state['status'] == 'stopped':
            return False
        return state['pid'] in self._owned(state, self.system.snapshot(), discover=False,
                                           require_known_group=False)

    def any_alive(self) -> bool:
        state = self._load()
        return bool(state and self._owned(state, self.system.snapshot(), discover=True,
                                          require_known_group=False))

    def status(self) -> dict:
        state = self._load()
        if state is None:
            self._prepare_profile()
            active = bool(self._external_users(self.system.snapshot()))
            return {'status': 'daily_instance_running' if active else 'not_created',
                    'ownedProcessCount': 0, 'debugPortOpen': False,
                    'appLeftRunning': active, 'profileRetained': True}
        owned = self._owned(state, self.system.snapshot(), discover=True,
                            require_known_group=False)
        running = state['pid'] in owned
        port_open = bool(self.system.listeners(state['port']))
        return {'status': ('detached' if state['status'] == 'detached' else 'running')
                if running else ('orphaned' if owned else 'stopped'),
                'ownedProcessCount': len(owned), 'debugPortOpen': port_open,
                'appLeftRunning': bool(owned), 'profileRetained': True}

    def launch_or_attach(self, *, timeout=35, allow_launch=True, cancelled=None) -> OwnedEndpoint | None:
        """Attach a verified instance; spawning requires an explicit caller permit."""
        self._require_lock()
        if (type(allow_launch) is not bool or (cancelled is not None and not callable(cancelled))
                or isinstance(timeout, bool) or
                not isinstance(timeout, (int, float)) or not 0 < timeout <= 60):
            raise HostError('invalid_launch_options')
        if cancelled is not None and cancelled():
            return None
        self._prepare_profile()
        previous = self._load()
        if previous:
            owned = self._owned(previous, self.system.snapshot(), discover=True,
                                require_known_group=False)
            if previous['pid'] in owned:
                self.system.verify_signature()
                self._guard(previous)
                previous['status'] = 'running'
                self._save(previous)
                return OwnedEndpoint(previous['pid'], previous['port'], self.profile_root)
            if owned:
                raise HostError('daily_instance_running')
            if allow_launch and self.system.listeners(previous['port']):
                raise HostError('stale_state_port_in_use')
        if not allow_launch:
            # Observation grants no authority to spawn, so it needs only main
            # process metadata. Avoid recursively scanning the user's profile
            # every idle heartbeat; actual launches retain the full preflight.
            if self._external_users(self.system.snapshot(), scan_profile=False):
                raise HostError('daily_instance_running')
            return None
        self.preflight()
        self.system.verify_signature()
        with self.system.reserve_port() as port:
            if self.system.listeners(port):
                raise HostError('selected_port_not_free')
        # Recheck immediately before spawn; no app is terminated to make room.
        self.preflight()
        initial = self.system.snapshot()
        if self._external_users(initial):
            raise HostError('daily_instance_running')
        # Stop or loss of the supervisor can arrive during the blocking checks
        # above. Recheck at the process-creation boundary, before opening Codex.
        if cancelled is not None and cancelled():
            return None
        self._proc = self.system.spawn(self._args(port),
                                       daily_environment(self.installation['home'], self.quota_home,
                                                         self.profile_root),
                                       self.installation['home'])
        state = None
        try:
            rows = self.system.snapshot()
            main = rows.get(self._proc.pid)
            if (main is None or not main.is_alive or main.pid in initial or main.pgid != main.pid
                    or main.executable != str(EXECUTABLE)):
                raise HostError('new_main_identity_not_verified')
            state = {'version': 1, 'mode': 'daily', 'status': 'starting', 'pid': main.pid,
                     'port': port, 'profileRoot': str(self.profile_root),
                     'quotaHome': str(self.quota_home),
                     'recoverySeed': secrets.token_hex(32),
                     'known': {str(main.pid): list(main.fingerprint)},
                     'initialPids': sorted(initial)}
            self._save(state)
            until = self.system.monotonic() + timeout
            while True:
                self._guard(state, require_listener=False)
                if self.system.listeners(port):
                    break
                if self._proc.poll() is not None:
                    raise HostError('daily_instance_exited_before_ready')
                if self.system.monotonic() >= until:
                    raise HostError('owned_listener_start_timeout')
                self.system.sleep(.15)
            self._guard(state)
            self.system.verify_signature()
            state['status'] = 'running'
            self._save(state)
            return OwnedEndpoint(main.pid, port, self.profile_root)
        except BaseException as error:
            # The user may already be resuming work. Even a failed launch never
            # authorizes SIGTERM/SIGKILL of this profile or its process tree.
            self.last_launch_failure = {'status': 'daily_launch_failed_app_not_stopped',
                                        'appMayBeRunning': True, 'debugPortMayRemain': True,
                                        'errorType': type(error).__name__}
            if state is not None:
                try:
                    self._save(state)
                except (HostError, OSError):
                    pass
            raise

    def detach(self) -> dict:
        """Release manager ownership only; renderer/transport cleanup is caller-owned."""
        state = self._load()
        if state is not None:
            alive = self._owned(state, self.system.snapshot(), discover=True,
                                require_known_group=False)
            state['status'] = 'detached' if alive else 'stopped'
            self._save(state)
        return self.status()

    def stop(self):
        raise HostError('daily_app_exit_requires_user_quit')

    def _stop_state(self, _state):
        raise HostError('daily_app_exit_requires_user_quit')

    def _cleanup_failed_launch(self, _state, _port, _error):
        raise HostError('daily_app_exit_requires_user_quit')

    def launch(self, **_):
        raise HostError('daily_use_launch_or_attach')
