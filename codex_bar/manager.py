"""Foreground manager. Importing/building bundles never executes web assets.

All live actions require the CLI acknowledgement. That flag is an operator guard,
not a replacement for the operator's consent to the documented runtime access.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import signal
import sys
import time

from .cdp import BrowserSession, CDPError
from .host import ManagedHost, PROJECT_ROOT, HostError
from .daily_host import DailyHost
from .quota import CodexQuotaClient, QuotaBridge

BRIDGE_KEY = 'codex-usage-bar.bridge.v1'
ASSET_NAMES = ('bar.js', 'adaptive.js', 'bar.css')


class ManagerError(RuntimeError):
    pass


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
            if len(data) > 262144 or hashlib.sha256(data).hexdigest() != manifest[name]:
                raise ManagerError('asset_fingerprint_mismatch')
            output[name] = data.decode('utf-8')
        return output
    except (OSError, ValueError, UnicodeError) as exc:
        raise ManagerError('assets_unavailable') from exc


def install_expression(assets: dict[str, str], token: str) -> str:
    # JSON quoting is for JavaScript literals here, never shell interpolation.
    return """(() => {
      const key=Symbol.for(%s), token=%s;
      if(window[key] || window.CodexUsageBar || window.CodexUsageBarAdapter)
        return {installed:false, reason:'existing-information-bar'};
      const owned={token, api:null, barExport:null, adaptiveExport:null}; window[key]=owned;
      try {
        %s
        %s
        owned.barExport=window.CodexUsageBar;
        owned.adaptiveExport=window.CodexUsageBarAdapter;
        owned.api=window.CodexUsageBarAdapter.install({css:%s, homeOnly:true});
        return {installed:true};
      } catch (_) {
        if (owned.api) { try { owned.api.dispose(); } catch (_) {} }
        if(window[key]===owned) delete window[key];
        delete window.CodexUsageBar; delete window.CodexUsageBarAdapter;
        return {installed:false, reason:'mount-failed'};
      }
    })()""" % (json.dumps(BRIDGE_KEY), json.dumps(token), assets['bar.js'],
                assets['adaptive.js'], json.dumps(assets['bar.css']))


def owned_expression(token: str, body: str) -> str:
    return """(() => { const h=window[Symbol.for(%s)];
      if(!h || h.token!==%s || !h.api) return {owned:false};
      %s
    })()""" % (json.dumps(BRIDGE_KEY), json.dumps(token), body)


class Renderer:
    """Only own bridge results are returned; no DOM text, screenshots or titles."""
    def __init__(self, session, page, *, token=None):
        self.session = session
        self.page = page
        self.token = token or secrets.token_hex(16)
        self.installed = False

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
        result = self.evaluate(install_expression(assets, self.token))
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
        return result

    def dispose(self):
        if not self.installed:
            return False
        result = self.evaluate(owned_expression(self.token, """
          h.api.dispose();
          const absent=!document.querySelector('[data-codex-usage-bar]');
          if(window[Symbol.for(%s)]===h) delete window[Symbol.for(%s)];
          if(window.CodexUsageBar===h.barExport) delete window.CodexUsageBar;
          if(window.CodexUsageBarAdapter===h.adaptiveExport) delete window.CodexUsageBarAdapter;
          return {owned:true,removed:absent};
        """ % (json.dumps(BRIDGE_KEY), json.dumps(BRIDGE_KEY))))
        self.installed = False
        if result.get('removed') is not True:
            raise ManagerError('renderer_cleanup_unverified')
        return True


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


def run_daily(*, duration=0, wait_for_exit=False):
    """Attach the bar to the original daily profile; never stop the user's app."""
    assets = read_assets()
    with DailyHost() as host, stop_signals() as stop:
        endpoint = None
        announced_wait = False
        while not stop[0]:
            try:
                endpoint = host.launch_or_attach()
                break
            except HostError as error:
                if str(error) != 'daily_instance_running' or not wait_for_exit:
                    raise
                if not announced_wait:
                    print('日常 Codex 正在运行。请先保存工作并用 Cmd-Q 正常退出；本启动器会等待，然后使用原账号、历史和项目重新打开。不会强制结束任务。', flush=True)
                    announced_wait = True
                time.sleep(1)
        if endpoint is None:
            print('已取消等待；日常 Codex 未改变。', flush=True)
            return

        session = renderer = executor = future = None
        cleanup_failed = False
        try:
            session = BrowserSession.connect(endpoint.port, host.validate)
            # Daily Codex may restore a conversation. Attach only to its one
            # canonical app origin; the adapter still mounts ONLY at empty home.
            page = session.attach_codex_page(allow_application_routes=True)
            renderer = Renderer(session, page)
            renderer.install(assets)
            bridge = QuotaBridge(CodexQuotaClient(codex_home=host.quota_home))
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='quota')
            print('已接入日常 Codex，沿用原资料。空白首页显示信息栏；输入/对话页卸载。Ctrl-C 仅关闭信息栏，Cmd-Q 退出 Codex。', flush=True)
            deadline = time.monotonic() + duration if duration else None
            next_refresh = next_heartbeat = 0.0
            previous_layout = None
            while not stop[0] and (deadline is None or time.monotonic() < deadline):
                if not host.is_running():
                    break
                now = time.monotonic()
                if future is not None and future.done():
                    future.result()
                    future = None
                    next_refresh = now + 60
                    next_heartbeat = 0.0
                if future is None and now >= next_refresh:
                    future = executor.submit(bridge.refresh)
                if now >= next_heartbeat:
                    layout = renderer.update(bridge.snapshot())
                    state = (layout.get('mounted'), layout.get('visible'))
                    if state != previous_layout:
                        print('日常首页横条已挂载。' if all(state) else '等待空白首页；不读取对话或输入正文。', flush=True)
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
            if renderer is not None and still_running is True:
                try:
                    renderer.dispose()
                except Exception:
                    renderer_failed = True
            if session is not None:
                try:
                    session.close()
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
            elif result and result.get('status') == 'stopped':
                print('日常 Codex 已退出，信息栏管理器结束。', flush=True)
            if cleanup_failed:
                print('信息栏清理未完全确认；未强制结束日常 Codex。请正常退出 Codex 后重新启动信息栏。', file=sys.stderr)
                if not original_failure:
                    raise ManagerError('daily_renderer_cleanup_unverified')


def main(argv=None):
    parser = argparse.ArgumentParser(description='codex-usage-bar：日常 Codex 首页信息栏；另保留独立测试模式。')
    parser.add_argument('command', choices=('daily', 'daily-status', 'status', 'login', 'run', 'cleanup'))
    parser.add_argument('--acknowledge-runtime', action='store_true',
                        help='仅在审阅并批准本次实际运行后使用；不是自动授权。')
    parser.add_argument('--duration', type=int, default=0, metavar='SECONDS')
    parser.add_argument('--reuse-approved-profile', action='store_true',
                        help='仅复用已批准的前次专用测试资料；不接受任意目录或复制凭据。')
    parser.add_argument('--wait-for-exit', action='store_true',
                        help='仅日常模式：等待已运行的 Codex 被本人正常退出，再启动信息栏版本。')
    args = parser.parse_args(argv)
    if args.duration < 0 or args.duration > 86400:
        parser.error('duration must be 0..86400')
    if args.command not in ('status', 'daily-status') and not args.acknowledge_runtime:
        parser.error('实际运行需先获批准，再显式传 --acknowledge-runtime')
    if args.wait_for_exit and args.command != 'daily':
        parser.error('--wait-for-exit is only available for daily')
    if args.reuse_approved_profile and args.command in ('daily', 'daily-status'):
        parser.error('daily mode always uses the existing everyday profile')
    try:
        if args.command == 'daily':
            run_daily(duration=args.duration, wait_for_exit=args.wait_for_exit)
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
