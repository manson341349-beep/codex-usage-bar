"""Foreground manager. Importing/building bundles never executes web assets.

All live actions require the CLI acknowledgement. That flag is an operator guard,
not a replacement for the operator's consent to the documented runtime access.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import threading
import time

from .cdp import BrowserSession, CDPError
from .host import ManagedHost, PROJECT_ROOT, HostError
from .daily_host import DailyHost
from .quota import CodexQuotaClient, QuotaBridge

BRIDGE_KEY = 'codex-usage-bar.bridge.v1'
ASSET_NAMES = ('sprig.js', 'bar.js', 'adaptive.js', 'bar.css')
ASSET_LIMITS = {name: 2 * 1024 * 1024 if name == 'sprig.js' else 262144
                for name in ASSET_NAMES}
SUPPORTED_LOCALES = ('zh-CN', 'en')


class ManagerError(RuntimeError):
    pass


def recovery_token(seed: str, target_id: str) -> str:
    """Derive a per-page owner from a private, verified host generation."""
    if (type(seed) is not str or re.fullmatch(r'[0-9a-f]{64}', seed) is None or
            type(target_id) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,256}', target_id) is None):
        raise ManagerError('invalid_renderer_recovery_identity')
    return hmac.new(bytes.fromhex(seed), b'codex-usage-bar.page.v1\0' + target_id.encode('ascii'),
                    hashlib.sha256).hexdigest()


@contextmanager
def supervisor_lifetime(stop, supervisor_pid):
    """An explicitly supervised manager cannot outlive its direct parent.

    A dead parent is reparented before its PID can be reused. We require the
    direct parent relationship at entry and latch its loss; an unrelated process
    reusing the numeric PID can never regain this relationship or clear Stop.
    No signal is sent to either the supervisor or Codex.
    """
    if supervisor_pid is None:
        yield
        return
    if (type(supervisor_pid) is not int or not 2 <= supervisor_pid <= 2147483647 or
            os.getppid() != supervisor_pid):
        raise ManagerError('invalid_supervisor_identity')
    cancelled = threading.Event()
    def watch():
        while not cancelled.wait(.2):
            if os.getppid() != supervisor_pid:
                stop[0] = True
                return
    watcher = threading.Thread(target=watch, name='supervisor-lifetime', daemon=True)
    watcher.start()
    try:
        yield
    finally:
        cancelled.set()
        watcher.join(timeout=1)


def read_assets(root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Only a fixed allowlist, with checked-in fingerprints; never fetch scripts."""
    directory = root / 'web'
    try:
        manifest_path = directory / 'asset-manifest.json'
        if manifest_path.is_symlink():
            raise ManagerError('unsafe_asset_manifest')
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict) or set(manifest) != set(ASSET_NAMES):
            raise ManagerError('asset_manifest_mismatch')
        output = {}
        for name in ASSET_NAMES:
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ManagerError('unsafe_asset_path')
            data = path.read_bytes()
            if len(data) > ASSET_LIMITS[name] or hashlib.sha256(data).hexdigest() != manifest[name]:
                raise ManagerError('asset_fingerprint_mismatch')
            output[name] = data.decode('utf-8')
        return output
    except (OSError, ValueError, UnicodeError) as exc:
        raise ManagerError('assets_unavailable') from exc


def install_expression(assets: dict[str, str], token: str, *, home_only=True) -> str:
    # JSON quoting is for JavaScript literals here, never shell interpolation.
    return """(() => {
      const key=Symbol.for(%s), token=%s;
      if(location.protocol!=='app:' || location.host!=='-')
        return {installed:false, reason:'application-origin-required'};
      const previous=window[key];
      if(previous && previous.token===token && previous.api &&
         previous.sprigExport && window.CodexUsageBarSprig===previous.sprigExport &&
         window.CodexUsageBar===previous.barExport &&
         window.CodexUsageBarAdapter===previous.adaptiveExport)
        return {installed:true, reused:true};
      if(window[key] || 'CodexUsageBarSprig' in window ||
         'CodexUsageBar' in window || 'CodexUsageBarAdapter' in window)
        return {installed:false, reason:'existing-information-bar'};
      const owned={token, api:null, sprigExport:null, barExport:null, adaptiveExport:null}; window[key]=owned;
      try {
        %s
        owned.sprigExport=window.CodexUsageBarSprig;
        %s
        owned.barExport=window.CodexUsageBar;
        %s
        owned.adaptiveExport=window.CodexUsageBarAdapter;
        if(!owned.sprigExport) throw new Error('sprig-export-missing');
        owned.api=window.CodexUsageBarAdapter.install({css:%s, homeOnly:%s});
        return {installed:true};
      } catch (_) {
        if (owned.api) { try { owned.api.dispose(); } catch (_) {} }
        if(window[key]===owned) delete window[key];
        if(owned.sprigExport && window.CodexUsageBarSprig===owned.sprigExport) delete window.CodexUsageBarSprig;
        if(owned.barExport && window.CodexUsageBar===owned.barExport) delete window.CodexUsageBar;
        if(owned.adaptiveExport && window.CodexUsageBarAdapter===owned.adaptiveExport) delete window.CodexUsageBarAdapter;
        return {installed:false, reason:'mount-failed'};
      }
    })()""" % (json.dumps(BRIDGE_KEY), json.dumps(token), assets['sprig.js'],
                assets['bar.js'], assets['adaptive.js'], json.dumps(assets['bar.css']),
                json.dumps(home_only))


def owned_expression(token: str, body: str) -> str:
    return """(() => { const h=window[Symbol.for(%s)];
      if(location.protocol!=='app:' || location.host!=='-') return {owned:false};
      if(!h || h.token!==%s || !h.api) return {owned:false};
      %s
    })()""" % (json.dumps(BRIDGE_KEY), json.dumps(token), body)


class Renderer:
    """Only own bridge results are returned; no DOM text, screenshots or titles."""
    def __init__(self, session, page, *, token=None, home_only=True):
        self.session = session
        self.page = page
        self.token = token or secrets.token_hex(16)
        self.installed = False
        self.home_only = home_only

    def evaluate(self, expression: str):
        reply = self.session.call('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True,
            'awaitPromise': False, 'silent': True,
        }, session_id=self.page.session_id)
        if 'exceptionDetails' in reply:
            raise ManagerError('renderer_evaluation_failed')
        value = reply.get('result', {}).get('value')
        if not isinstance(value, dict):
            raise ManagerError('unexpected_renderer_result')
        return value

    def install(self, assets):
        result = self.evaluate(install_expression(assets, self.token, home_only=self.home_only))
        if result.get('installed') is not True:
            raise ManagerError('renderer_install_refused')
        self.installed = True

    def update(self, snapshot):
        # Do not let an upstream field silently become thread content.
        allowed = ('limits', 'status', 'statusLabel', 'stale', 'sourceLabel', 'updatedAt')
        payload = {k: snapshot[k] for k in allowed if k in snapshot}
        payload.update(accountOnly=True, working=None)
        result = self.evaluate(owned_expression(self.token, """
          const accepted=h.api.setAccountSnapshot(%s);
          const m=h.api.inspect(), l=m.layout;
          return {owned:true,accepted:accepted===true,
            mounted:!!l,visible:!!(l&&l.visible),
            locale:m.locale==='zh-CN'||m.locale==='en'?m.locale:null,
            focused:m.focused===true,pageVisible:m.visible===true,
            overlaps:!!(l&&l.overlapsNativeContent),
            clipped:!!(l&&l.clippedByAncestor),
            inViewport:!!(l&&l.barWithinViewport)};
        """ % json.dumps(payload, ensure_ascii=True, allow_nan=False)))
        if result.get('owned') is not True:
            raise ManagerError('renderer_bridge_lost')
        if result.get('accepted') is not True:
            # An adapter can dispose itself after a compatibility failure while
            # its bridge still exists. Never report that stale bridge as healthy.
            raise ManagerError('renderer_update_refused')
        if result.get('mounted') and (result.get('overlaps') or result.get('clipped')):
            raise ManagerError('renderer_layout_unsafe')
        # Locale is the only textual renderer metadata eligible for the native
        # menu protocol. Never forward arbitrary DOM strings or truthy flags.
        locale = result.get('locale')
        result['locale'] = locale if type(locale) is str and locale in SUPPORTED_LOCALES else None
        result['focused'] = result.get('focused') is True
        result['pageVisible'] = result.get('pageVisible') is True
        return result

    def dispose(self):
        if not self.installed:
            return False
        result = self.evaluate(owned_expression(self.token, """
          h.api.dispose();
          const absent=!document.querySelector('[data-codex-usage-bar]');
          if(window[Symbol.for(%s)]===h) delete window[Symbol.for(%s)];
          if(window.CodexUsageBarSprig===h.sprigExport) delete window.CodexUsageBarSprig;
          if(window.CodexUsageBar===h.barExport) delete window.CodexUsageBar;
          if(window.CodexUsageBarAdapter===h.adaptiveExport) delete window.CodexUsageBarAdapter;
          return {owned:true,removed:absent};
        """ % (json.dumps(BRIDGE_KEY), json.dumps(BRIDGE_KEY))))
        self.installed = False
        if result.get('owned') is False:
            # Reload or a replacement bridge removed our token. Never remove
            # anything belonging to a different manager in order to clean up.
            return False
        if result.get('removed') is not True:
            raise ManagerError('renderer_cleanup_unverified')
        return True


class RendererCollection:
    """Manage daily windows independently, using only opaque target IDs.

    Target discovery and quota heartbeats are bounded by the manager's timer.
    A target failure backs off independently; transport failures are reconnected
    on the next heartbeat. Ownership tokens survive transport reconnects so a
    bridge already installed by this manager is reused instead of replaced.
    """
    def __init__(self, port, validate, assets, *, recovery_seed=None):
        self.port, self.validate, self.assets = port, validate, assets
        if recovery_seed is not None:
            recovery_token(recovery_seed, 'validation')
        self.recovery_seed = recovery_seed
        self.session = None
        self.targets = {}
        self._locale = None

    def _publish_locale(self, candidates):
        if not candidates:
            return  # Missing/hidden pages do not reset the last known language.
        priority = max(rank for rank, _ in candidates)
        languages = [locale for rank, locale in candidates if rank == priority]
        # Keep a stable choice when equally relevant windows disagree.
        selected = self._locale if self._locale in languages else languages[0]
        if selected != self._locale:
            self._locale = selected
            print('codex-usage-bar-locale:' + selected, flush=True)

    def _connect(self):
        if self.session is None or self.session.closed:
            if self.session is not None:
                self.session.close()
            self.session = BrowserSession.connect(self.port, self.validate)
            for state in self.targets.values():
                state['renderer'] = None

    def _renderer(self, target_id, state):
        if state['renderer'] is None:
            page = self.session.attach_codex_target(target_id)
            state['renderer'] = Renderer(self.session, page, token=state['token'], home_only=False)
        return state['renderer']

    def _detach(self, state):
        renderer = state['renderer']
        if renderer is not None:
            try:
                self.session.detach_page(renderer.page)
            except CDPError:
                pass  # Closing targets have already discarded their sessions.
            state['renderer'] = None

    def sync(self, snapshot, *, now=None):
        now = time.monotonic() if now is None else now
        self._connect()
        ids = self.session.list_codex_pages()
        for absent in set(self.targets).difference(ids):
            state = self.targets.pop(absent)
            self._detach(state)
        result = {'pages': len(ids), 'mounted': 0, 'visible': 0, 'unavailable': 0}
        locale_candidates = []
        for index, target_id in enumerate(ids):
            token = (recovery_token(self.recovery_seed, target_id) if self.recovery_seed is not None
                     else secrets.token_hex(16))
            state = self.targets.setdefault(target_id, {'token': token,
                         'renderer': None, 'failures': 0, 'next_attempt': 0.0})
            if now < state['next_attempt']:
                result['unavailable'] += 1
                continue
            try:
                renderer = self._renderer(target_id, state)
                if not renderer.installed:
                    renderer.install(self.assets)
                try:
                    layout = renderer.update(snapshot)
                except ManagerError as error:
                    if str(error) not in ('renderer_bridge_lost', 'renderer_update_refused'):
                        raise
                    # Reload clears the bridge. A disposed owned adapter can
                    # also be rebuilt, but foreign bridges are never adopted.
                    if str(error) == 'renderer_update_refused':
                        renderer.dispose()
                    renderer.installed = False
                    renderer.install(self.assets)
                    layout = renderer.update(snapshot)
                state.update(failures=0, next_attempt=0.0)
                result['mounted'] += int(layout.get('mounted') is True)
                result['visible'] += int(layout.get('visible') is True)
                locale = layout.get('locale')
                if type(locale) is str and locale in SUPPORTED_LOCALES:
                    rank = 2 if layout.get('focused') is True else 1 if layout.get('pageVisible') is True else 0
                    if rank:
                        locale_candidates.append((rank, locale))
            except (CDPError, ManagerError):
                state['failures'] = min(state['failures'] + 1, 5)
                state['next_attempt'] = now + min(60, 5 * 2 ** (state['failures'] - 1))
                result['unavailable'] += 1
                # Remove our own unsafe/disposed UI when the window is still
                # reachable. The next attempt will get a fresh target session.
                renderer = state['renderer']
                if renderer is not None and renderer.installed and not self.session.closed:
                    try:
                        renderer.dispose()
                    except (CDPError, ManagerError):
                        pass
                self._detach(state)
                if self.session.closed:
                    # Do not issue more requests on a failed transport. Other
                    # pages are preserved and reattached at the next heartbeat.
                    result['unavailable'] += len(ids) - index - 1
                    break
        self._publish_locale(locale_candidates)
        return result

    def dispose(self):
        if not self.targets:
            return
        self._connect()
        ids = self.session.list_codex_pages()
        failed = False
        reconnected = False
        pending = list(self.targets)
        while pending:
            target_id = pending.pop(0)
            if target_id not in ids:
                continue
            state = self.targets[target_id]
            try:
                renderer = self._renderer(target_id, state)
                # Reconnected renderers may still have an owned bridge.
                # Only check the original token; never reinstall here.
                renderer.installed = True
                renderer.dispose()
            except (CDPError, ManagerError):
                recovered = False
                try:
                    if self.session.closed:
                        if reconnected:
                            raise ManagerError('daily_renderer_cleanup_unverified')
                        reconnected = True
                        self._connect()
                        recovered = True
                    ids = self.session.list_codex_pages()
                except (CDPError, ManagerError):
                    failed = True
                    continue
                if target_id not in ids:
                    continue  # The user closed this window during cleanup.
                if recovered:
                    # Give surviving windows the recovered transport first.
                    # A persistently unresponsive target must not consume that
                    # only recovery before the healthy windows can be cleaned.
                    pending.append(target_id)
                else:
                    failed = True
        if failed:
            raise ManagerError('daily_renderer_cleanup_unverified')
        self.targets.clear()

    def close(self):
        if self.session is not None:
            self.session.close()


@contextmanager
def stop_signals():
    requested = [False]
    def request_stop(_signum, _frame):
        requested[0] = True
    original = {s: signal.signal(s, request_stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield requested
    finally:
        for sig, handler in original.items():
            signal.signal(sig, handler)


def run_foreground(*, duration=0, login_only=False, reuse_approved_profile=False):
    assets = None if login_only else read_assets()
    with ManagedHost(approved_previous_profile=reuse_approved_profile) as host, stop_signals() as stop:
        session = renderer = executor = future = None
        cleanup_failed = False
        launched = False
        try:
            endpoint = host.launch(debug=not login_only)
            launched = True
            if login_only:
                print('专用隔离 Codex 已打开。请本人登录；完成后按 Ctrl-C 关闭，资料保留。', flush=True)
            else:
                session = BrowserSession.connect(endpoint.port, host.validate)
                page = session.attach_codex_page()
                renderer = Renderer(session, page)
                renderer.install(assets)
                # Same CODEX_HOME as our isolated GUI, never daily ~/.codex.
                bridge = QuotaBridge(CodexQuotaClient(codex_home=endpoint.profile_root / 'codex-home'))
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='quota')
                print('信息栏仅在专用实例的空白首页挂载。真实工作状态未接入。Ctrl-C 退出并清理。', flush=True)
            deadline = time.monotonic() + duration if duration else None
            next_refresh = 0.0
            next_heartbeat = 0.0
            next_login_check = 0.0
            previous_layout = None
            while not stop[0] and (deadline is None or time.monotonic() < deadline):
                now = time.monotonic()
                if login_only:
                    if now >= next_login_check:
                        host.validate()
                        next_login_check = time.monotonic() + 5
                else:
                    if future is not None and future.done():
                        future.result()  # The bridge sanitizes its own error state.
                        future = None
                        next_refresh = now + 60
                        next_heartbeat = 0.0
                    if future is None and now >= next_refresh:
                        future = executor.submit(bridge.refresh)
                    if now >= next_heartbeat:
                        # Every CDP request retains its full ownership audit.
                        # Local timers animate/count down between heartbeats;
                        # no extra host audit is needed before this audited call.
                        layout = renderer.update(bridge.snapshot())
                        next_heartbeat = time.monotonic() + 5
                        state = (layout.get('mounted'), layout.get('visible'))
                        if state != previous_layout:
                            print('横条已挂载。' if all(state) else '等待唯一空白首页输入框；登录页/输入中/对话页不会挂载。', flush=True)
                            previous_layout = state
                time.sleep(.2)
        finally:
            if renderer is not None:
                try:
                    renderer.dispose()
                except Exception:
                    cleanup_failed = True
            if session is not None:
                session.close()
            # Finite quota RPC deadline; no new work or retained live subprocess.
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            if launched:
                try:
                    host.stop()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                raise ManagerError('cleanup_needs_review')
            if launched:
                print('专用实例和调试端口已关闭；登录资料保留。', flush=True)


def run_daily(*, duration=0, wait_for_exit=False, resident=False, supervisor_pid=None,
              launch_once=False):
    """Observe Codex in resident mode; only a one-shot user request may open it."""
    if supervisor_pid is not None and not resident:
        raise ManagerError('supervisor_requires_resident')
    if type(launch_once) is not bool or (launch_once and not resident):
        raise ManagerError('launch_once_requires_resident')
    assets = read_assets()
    with stop_signals() as stop, supervisor_lifetime(stop, supervisor_pid), DailyHost() as host:
        deadline = time.monotonic() + duration if resident and duration else None
        may_launch = not resident or launch_once
        announced = None
        while not stop[0] and (deadline is None or time.monotonic() < deadline):
            try:
                endpoint = host.launch_or_attach(allow_launch=may_launch,
                                                cancelled=lambda: stop[0])
            except HostError as error:
                # A failed post-spawn validation may leave the user's app alive.
                # That attempted launch still consumes the one-shot request.
                if resident and isinstance(host.last_launch_failure, dict):
                    may_launch = False
                if str(error) != 'daily_instance_running' or not (wait_for_exit or resident):
                    raise
                state = 'waiting-for-quit' if may_launch else 'needs-launcher'
                if state != announced:
                    if resident:
                        print('codex-usage-bar-resident:' + state, flush=True)
                    if may_launch:
                        print('日常 Codex 正在运行。请先保存工作并用 Cmd-Q 正常退出；本启动器会等待，然后使用原账号、历史和项目重新打开。不会强制结束任务。', flush=True)
                    announced = state
                time.sleep(1)
                continue
            if endpoint is None:
                if stop[0]:
                    break
                # No endpoint means observer mode found no running managed app.
                # Even a previous managed exit or an ordinary app disappearing
                # cannot turn observation into permission to spawn.
                if announced != 'waiting':
                    print('codex-usage-bar-resident:waiting', flush=True)
                    announced = 'waiting'
                time.sleep(1)
                continue
            # A request is consumed when it attaches OR launches once. It never
            # survives a managed app exit or grants authority to later retries.
            may_launch = False
            announced = None
            remaining = max(.001, deadline - time.monotonic()) if deadline is not None else duration
            _run_daily_session(host, endpoint, assets, stop, duration=remaining, resident=resident)
            if not resident or stop[0] or (deadline is not None and time.monotonic() >= deadline):
                return
            time.sleep(.2)
        if not resident:
            print('已取消等待；日常 Codex 未改变。', flush=True)


def _run_daily_session(host, endpoint, assets, stop, *, duration=0, resident=False):
    """One verified host generation, including its exact cleanup boundary."""
    renderers = executor = future = None
    cleanup_failed = False
    try:
        renderers = RendererCollection(endpoint.port, host.validate, assets,
                                       recovery_seed=host.recovery_seed())
        bridge = QuotaBridge(CodexQuotaClient(codex_home=host.quota_home))
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='quota')
        if resident:
            print('codex-usage-bar-resident:attached', flush=True)
        print('日常额度条管理器已启动。会在兼容的首页和会话输入框上方显示；支持多窗口与页面重载。Ctrl-C 仅关闭信息栏，Cmd-Q 退出 Codex。', flush=True)
        deadline = time.monotonic() + duration if duration else None
        next_refresh = next_heartbeat = 0.0
        quota_failures = 0
        previous_layout = None
        while not stop[0] and (deadline is None or time.monotonic() < deadline):
            if not host.is_running():
                break
            now = time.monotonic()
            if future is not None and future.done():
                try:
                    future.result()
                except (HostError, CDPError, ManagerError):
                    # Ownership/transport safety failures are never quota retries.
                    raise
                except Exception:
                    if not resident:
                        raise
                    quota_failures = min(quota_failures + 1, 5)
                    print('codex-usage-bar-resident:quota-retrying', flush=True)
                    next_refresh = now + min(60, 5 * 2 ** (quota_failures - 1))
                else:
                    if quota_failures:
                        print('codex-usage-bar-resident:attached', flush=True)
                        previous_layout = None
                    quota_failures = 0
                    next_refresh = now + 60
                future = None
                next_heartbeat = 0.0
            if future is None and now >= next_refresh:
                future = executor.submit(bridge.refresh)
            if now >= next_heartbeat:
                try:
                    # An unexpected refresh exception cannot leave old account
                    # data displayed as fresh. Hide it until a complete read succeeds.
                    snapshot = ({'status': 'read_failed', 'limits': {}} if quota_failures
                                else bridge.snapshot())
                    layout = renderers.sync(snapshot, now=now)
                except CDPError:
                    # Closed transports and changing window inventories are
                    # retried on the next heartbeat, never in a tight loop.
                    layout = {'mounted': 0, 'visible': 0, 'unavailable': 1}
                state = (layout.get('mounted'), layout.get('visible'), layout.get('unavailable'))
                if state != previous_layout:
                    print('额度条已在 %d 个窗口显示；%d 个窗口等待恢复。' % (state[1], state[2])
                          if state[1] else '等待兼容的输入框或窗口恢复；不读取对话或输入正文。', flush=True)
                    previous_layout = state
                next_heartbeat = time.monotonic() + 5
            time.sleep(.2)
    except (CDPError, HostError, ManagerError) as original_error:
        # Closing the app is a normal end to daily mode, not a reason to
        # signal any process or classify a closed renderer as a leak.
        try:
            still_running = host.is_running()
        except Exception:
            raise original_error from None
        if still_running:
            raise
    finally:
        original_failure = sys.exc_info()[0] is not None
        renderer_failed = False
        result = None
        try:
            still_running = host.is_running()
        except Exception:
            still_running = None
            cleanup_failed = True
        if renderers is not None and still_running is True:
            try:
                renderers.dispose()
            except Exception:
                renderer_failed = True
        if renderers is not None:
            try:
                renderers.close()
            except Exception:
                cleanup_failed = True
        if executor is not None:
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                cleanup_failed = True
        try:
            result = host.detach()
        except Exception:
            cleanup_failed = True
        # Cmd-Q can race with dispose; a confirmed stopped app has no live
        # renderer to remove. Unknown/orphaned state remains a failure.
        if renderer_failed and not (result and result.get('status') == 'stopped'
                                   and result.get('ownedProcessCount') == 0
                                   and result.get('debugPortOpen') is False):
            cleanup_failed = True
        if result and result.get('appLeftRunning'):
            print('信息栏管理器已断开；日常 Codex 继续运行。要关闭本地调试端口，请正常退出 Codex（Cmd-Q）。', flush=True)
        elif not resident and result and result.get('status') == 'stopped':
            print('日常 Codex 已退出，信息栏管理器结束。', flush=True)
        if cleanup_failed:
            print('信息栏清理未完全确认；未强制结束日常 Codex。请正常退出 Codex 后重新启动信息栏。', file=sys.stderr)
            if not original_failure:
                raise ManagerError('daily_renderer_cleanup_unverified')


def main(argv=None):
    parser = argparse.ArgumentParser(description='codex-usage-bar：日常 Codex 输入框上方额度条；另保留独立测试模式。')
    parser.add_argument('command', choices=('daily', 'daily-status', 'status', 'login', 'run', 'cleanup'))
    parser.add_argument('--acknowledge-runtime', action='store_true',
                        help='仅在审阅并批准本次实际运行后使用；不是自动授权。')
    parser.add_argument('--duration', type=int, default=0, metavar='SECONDS')
    parser.add_argument('--reuse-approved-profile', action='store_true',
                        help='仅复用已批准的前次专用测试资料；不接受任意目录或复制凭据。')
    parser.add_argument('--wait-for-exit', action='store_true',
                        help='仅日常模式：等待已运行的 Codex 被本人正常退出，再启动信息栏版本。')
    parser.add_argument('--resident', action='store_true',
                        help='仅日常模式：后台观察并附加已启用实例；Codex 退出后保持关闭。')
    parser.add_argument('--launch-once', action='store_true',
                        help='仅与 --resident 共用：明确请求打开 Codex 一次，之后只观察。')
    parser.add_argument('--supervisor-pid', type=int,
                        help='仅由原生常驻伴侣传入其进程标识；伴侣终止时安全释放管理器。')
    args = parser.parse_args(argv)
    if args.duration < 0 or args.duration > 86400:
        parser.error('duration must be 0..86400')
    if args.command not in ('status', 'daily-status') and not args.acknowledge_runtime:
        parser.error('实际运行需先获批准，再显式传 --acknowledge-runtime')
    if args.wait_for_exit and args.command != 'daily':
        parser.error('--wait-for-exit is only available for daily')
    if args.resident and args.command != 'daily':
        parser.error('--resident is only available for daily')
    if args.launch_once and not args.resident:
        parser.error('--launch-once requires --resident')
    if args.supervisor_pid is not None and (not args.resident or
            not 2 <= args.supervisor_pid <= 2147483647):
        parser.error('--supervisor-pid requires --resident and a valid process ID')
    if args.reuse_approved_profile and args.command in ('daily', 'daily-status'):
        parser.error('daily mode always uses the existing everyday profile')
    try:
        if args.command == 'daily':
            run_daily(duration=args.duration, wait_for_exit=args.wait_for_exit, resident=args.resident,
                      supervisor_pid=args.supervisor_pid, launch_once=args.launch_once)
        elif args.command == 'daily-status':
            with DailyHost() as host:
                result = host.status()
                print(json.dumps({k: result.get(k) for k in
                      ('status', 'ownedProcessCount', 'debugPortOpen', 'appLeftRunning')}, ensure_ascii=False))
        elif args.command == 'status':
            with ManagedHost(approved_previous_profile=args.reuse_approved_profile) as host:
                result = host.status()
                # Only lifecycle fields, never raw manifests/profile identifiers.
                print(json.dumps({k: result.get(k) for k in
                      ('status', 'profileRetained', 'ownedProcessCount')}, ensure_ascii=False))
        elif args.command == 'cleanup':
            with ManagedHost(approved_previous_profile=args.reuse_approved_profile) as host:
                host.stop()
            print('本项目记录的专用实例清理完成；资料保留。')
        else:
            run_foreground(duration=args.duration, login_only=args.command == 'login',
                           reuse_approved_profile=args.reuse_approved_profile)
        return 0
    except Exception as error:
        # Never show RPC/CDP payloads, stderr, account information or profile data.
        code = str(error) if isinstance(error, (ManagerError, HostError, CDPError)) else 'unexpected_manager_failure'
        print('未完成：' + code + '。请按说明核验当前模式；不代表已成功挂载或清理。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
