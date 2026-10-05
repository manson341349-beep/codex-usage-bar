"""Offline orchestration and isolated JS ownership fixtures; never connects to Codex."""
import hashlib
from concurrent.futures import Future
import fcntl
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from codex_bar.manager import (ASSET_NAMES, ASSET_LIMITS, BRIDGE_KEY, ManagerError, Renderer, main,
                               read_assets, install_expression, recovery_token,
                               run_foreground, run_daily, supervisor_lifetime, RendererCollection)
from codex_bar.host import HostError
from codex_bar.cdp import CDPError


class FakeSession:
    def __init__(self):
        self.calls = []
        self.value = {'installed': True}

    def call(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        return {'result': {'value': self.value}}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.renderer = Renderer(self.session, SimpleNamespace(session_id='test'), token='owned-token')

    def test_building_bundle_only_concatenates_and_escapes_owned_test_strings(self):
        assets = {'sprig.js': '/* owned-test-sprig */',
                  'bar.js': '/* owned-test-one */', 'adaptive.js': '/* owned-test-two */',
                  'bar.css': '"\\\nCSS sentinel'}
        source = install_expression(assets, '"sentinel')
        self.assertIn(json.dumps(assets['bar.css']), source)
        self.assertIn('homeOnly:true', source)
        self.assertIn('existing-information-bar', source)
        self.assertNotIn('eval(', source)
        self.assertLess(source.index(assets['sprig.js']), source.index(assets['bar.js']))
        self.assertLess(source.index(assets['bar.js']), source.index(assets['adaptive.js']))

    def test_install_refusal_is_not_success(self):
        self.session.value = {'installed': False, 'reason': 'existing-information-bar'}
        with self.assertRaises(ManagerError):
            self.renderer.install(dict.fromkeys(ASSET_NAMES, '/* test */'))
        self.assertFalse(self.renderer.installed)

    def test_daily_install_is_origin_guarded_and_reuses_only_matching_owner(self):
        source = install_expression(dict.fromkeys(ASSET_NAMES, '/* fixture */'),
                                    'our-token', home_only=False)
        self.assertIn('homeOnly:false', source)
        self.assertIn("location.protocol!=='app:' || location.host!=='-'", source)
        self.assertIn('previous.token===token', source)
        self.assertIn('window.CodexUsageBar===previous.barExport', source)
        self.assertIn('window.CodexUsageBarSprig===previous.sprigExport', source)
        self.assertIn('installed:true, reused:true', source)

    def test_snapshot_never_forwards_thread_content_or_work_claim(self):
        self.session.value = {'owned': True, 'accepted': True, 'mounted': True, 'visible': True}
        self.renderer.update({'working': True, 'context': {'secret':'DO_NOT_FORWARD'},
                              'account_fingerprint':'PRIVATE_ID', 'status':'fresh',
                              'statusLabel':'已同步', 'limits':{}})
        method, params, session_id = self.session.calls[-1]
        self.assertEqual(method, 'Runtime.evaluate')
        self.assertEqual(session_id, 'test')
        self.assertIn('"working": null', params['expression'])
        self.assertIn('"accountOnly": true', params['expression'])
        self.assertNotIn('DO_NOT_FORWARD', params['expression'])
        self.assertNotIn('PRIVATE_ID', params['expression'])

    def test_unsafe_layout_is_failure(self):
        self.session.value = {'owned':True,'accepted':True,'mounted':True,'overlaps':True}
        with self.assertRaisesRegex(ManagerError, 'layout_unsafe'):
            self.renderer.update({})

    def test_renderer_locale_metadata_is_strict_and_separate_from_bar_visibility(self):
        for locale in ('zh-CN', 'en', 'zh', 'en-GB', 'EN', '', None, 1, True,
                       ['en'], {'locale': 'en'}, 'en\ncodex-usage-bar-locale:zh-CN'):
            with self.subTest(locale=locale):
                self.session.value = {'owned': True, 'accepted': True, 'mounted': False,
                                      'visible': False, 'locale': locale,
                                      'focused': 'true', 'pageVisible': 1}
                result = self.renderer.update({'locale': 'PRIVATE_UPSTREAM_LOCALE'})
                self.assertEqual(result['locale'], locale if type(locale) is str and locale in ('zh-CN', 'en') else None)
                self.assertIs(result['focused'], False)
                self.assertIs(result['pageVisible'], False)
                expression = self.session.calls[-1][1]['expression']
                self.assertIn('focused:m.focused===true,pageVisible:m.visible===true', expression)
                self.assertIn('visible:!!(l&&l.visible)', expression)
                self.assertNotIn('PRIVATE_UPSTREAM_LOCALE', expression)
        self.session.value = {'owned': True, 'accepted': True, 'mounted': False,
                              'visible': False, 'locale': 'en', 'focused': True, 'pageVisible': True}
        result = self.renderer.update({})
        self.assertIs(result['focused'], True)
        self.assertIs(result['pageVisible'], True)
        self.assertIs(result['visible'], False)

    def test_missing_owner_is_failure(self):
        self.session.value = {'owned':False}
        with self.assertRaisesRegex(ManagerError, 'bridge_lost'):
            self.renderer.update({})

    def test_frontend_rejected_or_missing_acceptance_is_failure(self):
        for acceptance in (False, None, 1, 'true'):
            with self.subTest(acceptance=acceptance):
                self.session.value = {'owned': True, 'accepted': acceptance,
                                      'mounted': False, 'visible': False}
                with self.assertRaisesRegex(ManagerError, '^renderer_update_refused$'):
                    self.renderer.update({})

    def test_accepted_unmounted_adapter_can_wait_for_home(self):
        self.session.value = {'owned': True, 'accepted': True,
                              'mounted': False, 'visible': False}
        self.assertFalse(self.renderer.update({})['mounted'])

    def test_cleanup_must_confirm_dom_absent(self):
        self.renderer.installed = True
        self.session.value = {'owned':True,'removed':False}
        with self.assertRaisesRegex(ManagerError, 'cleanup_unverified'):
            self.renderer.dispose()

    def test_cleanup_own_token_only(self):
        self.renderer.installed = True
        self.session.value = {'owned':True,'removed':True}
        self.assertTrue(self.renderer.dispose())
        self.assertIn('owned-token', self.session.calls[-1][1]['expression'])
        self.assertIn('h.api.dispose()', self.session.calls[-1][1]['expression'])

    def test_cleanup_after_reload_does_not_delete_foreign_bridge(self):
        self.renderer.installed = True
        self.session.value = {'owned': False}
        self.assertFalse(self.renderer.dispose())
        self.assertFalse(self.renderer.installed)

    def test_remote_error_details_are_not_output(self):
        self.session.call = lambda *args, **kwargs: {'exceptionDetails': {'text':'SECRET'}}
        with self.assertRaisesRegex(ManagerError,'^renderer_evaluation_failed$'):
            self.renderer.evaluate('/* own test, never executed */')

    def test_no_ack_no_live_action(self):
        with patch('codex_bar.manager.ManagedHost') as host:
            for command in ('run','login','cleanup'):
                with patch('sys.stderr'), self.assertRaises(SystemExit):
                    main([command])
            host.assert_not_called()

    def test_launch_refusal_never_stops_preexisting_owned_instance(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.side_effect = HostError('owned_instance_or_orphan_already_running')
        with patch('codex_bar.manager.ManagedHost', return_value=host), patch('builtins.print'):
            with self.assertRaises(HostError):
                run_foreground(login_only=True, duration=1)
        host.stop.assert_not_called()

    def test_failed_renderer_connect_stops_newly_launched_instance(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345)
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession.connect', side_effect=ManagerError('fixture_failure')), \
             patch('builtins.print'):
            with self.assertRaisesRegex(ManagerError, 'fixture_failure'):
                run_foreground(duration=1)
        host.stop.assert_called_once()

    def test_host_cleanup_failure_does_not_report_success(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345)
        host.stop.side_effect = HostError('cleanup_incomplete')
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession.connect', side_effect=ManagerError('fixture_failure')), \
             patch('builtins.print') as output:
            with self.assertRaisesRegex(ManagerError, 'cleanup_needs_review'):
                run_foreground(duration=1)
        self.assertNotIn('已关闭', str(output.call_args_list))

    def test_assets_tamper_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            web = root/'web'; web.mkdir()
            manifest = {}
            for name in ASSET_NAMES:
                data = b'/* only our test fixture */'
                (web/name).write_bytes(data)
                manifest[name]=hashlib.sha256(data).hexdigest()
            (web/'asset-manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(set(read_assets(root)),set(ASSET_NAMES))
            (web/'bar.js').write_text('tampered')
            with self.assertRaisesRegex(ManagerError,'fingerprint'):
                read_assets(root)
            (web/'bar.js').unlink()
            (web/'bar.js').symlink_to(web/'bar.css')
            with self.assertRaisesRegex(ManagerError,'unsafe_asset_path'):
                read_assets(root)

    def test_asset_manifest_requires_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'web').mkdir()
            for malformed in (list(ASSET_NAMES), None, True):
                (root / 'web' / 'asset-manifest.json').write_text(json.dumps(malformed))
                with self.subTest(manifest=malformed), self.assertRaisesRegex(ManagerError, 'asset_manifest_mismatch'):
                    read_assets(root)

    def test_asset_allowlist_requires_sprig_and_refuses_extra_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'web').mkdir()
            for names in (set(ASSET_NAMES) - {'sprig.js'},
                          set(ASSET_NAMES) | {'external.js'},
                          set(ASSET_NAMES) | {'../external.js'}):
                manifest = dict.fromkeys(names, '0' * 64)
                (root / 'web/asset-manifest.json').write_text(json.dumps(manifest))
                with self.subTest(names=names), self.assertRaisesRegex(ManagerError, 'asset_manifest_mismatch'):
                    read_assets(root)

    def test_only_sprig_has_a_two_mebibyte_limit_and_boundaries_are_enforced(self):
        self.assertEqual(ASSET_LIMITS['sprig.js'], 2 * 1024 * 1024)
        self.assertEqual({ASSET_LIMITS[n] for n in ASSET_NAMES if n != 'sprig.js'}, {262144})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            web = root / 'web'
            web.mkdir()
            for name in ASSET_NAMES:
                for size in (ASSET_LIMITS[name], ASSET_LIMITS[name] + 1):
                    with self.subTest(asset=name, bytes=size):
                        manifest = {}
                        for asset in ASSET_NAMES:
                            data = b' ' * size if asset == name else b'/* fixture */'
                            (web / asset).write_bytes(data)
                            manifest[asset] = hashlib.sha256(data).hexdigest()
                        (web / 'asset-manifest.json').write_text(json.dumps(manifest))
                        if size <= ASSET_LIMITS[name]:
                            self.assertEqual(len(read_assets(root)[name]), size)
                        else:
                            with self.assertRaisesRegex(ManagerError, 'asset_fingerprint_mismatch'):
                                read_assets(root)

    def test_sprig_tampering_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            web = root / 'web'
            web.mkdir()
            data = b'/* original Sprig fixture */'
            for name in ASSET_NAMES:
                (web / name).write_bytes(data)
            (web / 'asset-manifest.json').write_text(json.dumps(
                dict.fromkeys(ASSET_NAMES, hashlib.sha256(data).hexdigest())))
            (web / 'sprig.js').write_bytes(b'/* modified Sprig fixture */')
            with self.assertRaisesRegex(ManagerError, 'asset_fingerprint_mismatch'):
                read_assets(root)
            (web / 'sprig.js').unlink()
            (web / 'sprig.js').symlink_to(web / 'bar.js')
            with self.assertRaisesRegex(ManagerError, 'unsafe_asset_path'):
                read_assets(root)

    def test_frontend_rejection_runs_full_cleanup(self):
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345, profile_root=Path('/owned-fixture'))
        renderer = MagicMock()
        renderer.update.side_effect = ManagerError('renderer_update_refused')
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession') as browser, \
             patch('codex_bar.manager.Renderer', return_value=renderer), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('builtins.print'):
            with self.assertRaisesRegex(ManagerError, '^renderer_update_refused$'):
                run_foreground(duration=1)
        renderer.dispose.assert_called_once()
        browser.connect.return_value.close.assert_called_once()
        worker.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        host.stop.assert_called_once()

    def test_pending_quota_request_keeps_heartbeat_and_stop_responsive(self):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        host = MagicMock()
        host.__enter__.return_value = host
        host.launch.return_value = SimpleNamespace(port=12345, profile_root=Path('/owned-fixture'))
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        renderer = MagicMock()
        renderer.update.return_value = {'mounted':True, 'visible':True}
        with patch('codex_bar.manager.ManagedHost', return_value=host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession'), \
             patch('codex_bar.manager.Renderer', return_value=renderer), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda:clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=sleep), patch('builtins.print'):
            run_foreground(duration=11)
        self.assertEqual(renderer.update.call_count, 3)
        worker.submit.assert_called_once()
        renderer.dispose.assert_called_once()
        host.stop.assert_called_once()
        self.assertLess(clock[0],11.3)


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for isolated export ownership fixtures')
class ExportOwnershipTests(unittest.TestCase):
    def test_hard_crash_bridge_reused_only_by_same_host_and_target_token(self):
        assets = {
            'sprig.js': 'window.CodexUsageBarSprig={mount(){}};',
            'bar.js': 'window.CodexUsageBar={mount(){}};',
            'adaptive.js': 'window.CodexUsageBarAdapter={install(){installs++;return {dispose(){}}}};',
            'bar.css': '/* fixture */',
        }
        # Serialize just as a fresh process reads the private host record.
        seed = json.loads(json.dumps({'recoverySeed': 'a' * 64}))['recoverySeed']
        expressions = [install_expression(assets, recovery_token(value, target), home_only=False)
                       for value, target in [(seed, 'page-one'), (seed, 'page-one'),
                                             ('b' * 64, 'page-one'), (seed, 'page-two')]]
        script = '''
          const vm=require('node:vm'), assert=require('node:assert/strict');
          const c=vm.createContext({window:{},location:{protocol:'app:',host:'-'},installs:0});
          const code=EXPRESSIONS;
          assert.equal(vm.runInContext(code[0],c).installed,true);
          // No dispose: the old manager is gone, its bridge is still alive.
          const recovered=vm.runInContext(code[1],c);
          assert.equal(recovered.installed,true);assert.equal(recovered.reused,true);
          assert.equal(c.installs,1);
          for(const other of code.slice(2)) {
            const denied=vm.runInContext(other,c);
            assert.equal(denied.installed,false);assert.equal(denied.reason,'existing-information-bar');
          }
          assert.equal(c.installs,1);
          console.log('recovery fixture passed');
        '''.replace('EXPRESSIONS', json.dumps(expressions))
        result = subprocess.run([shutil.which('node'), '-e', script],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'recovery fixture passed')

    def run_javascript(self, assertions, *, overrides=None):
        assets = {
            'sprig.js': 'order.push("sprig");window.CodexUsageBarSprig={mount(){}};',
            'bar.js': 'if(!window.CodexUsageBarSprig)throw new Error("missing Sprig");'
                      'order.push("bar");window.CodexUsageBar={mount(){}};',
            'adaptive.js': 'order.push("adaptive");window.CodexUsageBarAdapter={'
                           'install(){installs++;return {dispose(){disposals++}}}};',
            'bar.css': '/* isolated ownership fixture */',
        }
        assets.update(overrides or {})
        session = FakeSession()
        session.value = {'owned': True, 'removed': True}
        renderer = Renderer(session, SimpleNamespace(session_id='fixture'), token='owned-token')
        renderer.installed = True
        renderer.dispose()
        dispose_source = session.calls[-1][1]['expression']
        script = '''
          const vm=require('node:vm'), assert=require('node:assert/strict');
          const key=Symbol.for(KEY);
          const context=()=>vm.createContext({window:{},location:{protocol:'app:',host:'-'},
            document:{querySelector(){return null}},order:[],installs:0,disposals:0,
            foreignSprig:{foreign:true},foreignBar:{foreign:true},
            foreignAdapter:{foreign:true},foreignBridge:{foreign:true}});
          const install=c=>vm.runInContext(INSTALL,c);
          const dispose=c=>vm.runInContext(DISPOSE,c);
        '''.replace('KEY', json.dumps(BRIDGE_KEY)).replace(
            'INSTALL', json.dumps(install_expression(assets, 'owned-token', home_only=False))).replace(
            'DISPOSE', json.dumps(dispose_source))
        result = subprocess.run([shutil.which('node'), '-'], input=script + assertions,
                                text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_owned_exports_install_in_order_and_reuse_without_reexecution(self):
        self.run_javascript('''
          const c=context();assert.equal(install(c).installed,true);
          assert.equal(c.order.join(','),'sprig,bar,adaptive');
          const own=c.window.CodexUsageBarSprig;
          assert.equal(install(c).reused,true);assert.equal(c.installs,1);
          assert.equal(c.window.CodexUsageBarSprig,own);
          c.window.CodexUsageBarSprig=c.foreignSprig;
          assert.equal(install(c).reason,'existing-information-bar');
          assert.equal(c.window.CodexUsageBarSprig,c.foreignSprig);
          assert.equal(c.installs,1);
        ''')

    def test_existing_sprig_property_is_never_overwritten_even_if_falsy(self):
        self.run_javascript('''
          for(const value of [{foreign:true},null,undefined,false]){
            const c=context();c.window.CodexUsageBarSprig=value;
            assert.equal(install(c).reason,'existing-information-bar');
            assert.equal(c.window.CodexUsageBarSprig,value);
            assert.equal(c.order.length,0);assert.equal(c.window[key],undefined);
          }
        ''')

    def test_dispose_removes_all_owned_exports_and_no_foreign_replacements(self):
        self.run_javascript('''
          const c=context();install(c);assert.equal(dispose(c).removed,true);
          assert.equal(c.disposals,1);assert.equal(c.window[key],undefined);
          for(const name of ['CodexUsageBarSprig','CodexUsageBar','CodexUsageBarAdapter'])
            assert.equal(name in c.window,false);
          const d=context();install(d);
          d.window.CodexUsageBarSprig=d.foreignSprig;
          d.window.CodexUsageBar=d.foreignBar;d.window.CodexUsageBarAdapter=d.foreignAdapter;
          assert.equal(dispose(d).removed,true);assert.equal(d.disposals,1);
          assert.equal(d.window.CodexUsageBarSprig,d.foreignSprig);
          assert.equal(d.window.CodexUsageBar,d.foreignBar);
          assert.equal(d.window.CodexUsageBarAdapter,d.foreignAdapter);
        ''')

    def test_failed_mount_preserves_export_and_bridge_replacements(self):
        self.run_javascript('''
          const c=context();assert.equal(install(c).reason,'mount-failed');
          assert.equal(c.window.CodexUsageBarSprig,c.foreignSprig);
          assert.equal(c.window.CodexUsageBar,c.foreignBar);
          assert.equal(c.window.CodexUsageBarAdapter,c.foreignAdapter);
          assert.equal(c.window[key],c.foreignBridge);
        ''', overrides={'adaptive.js': '''
          window.CodexUsageBarAdapter={install(){
            window.CodexUsageBarSprig=foreignSprig;window.CodexUsageBar=foreignBar;
            window.CodexUsageBarAdapter=foreignAdapter;
            window[Symbol.for("codex-usage-bar.bridge.v1")]=foreignBridge;
            throw new Error('synthetic mount failure');
          }};
        '''})

    def test_failure_loading_a_later_asset_cleans_earlier_owned_exports(self):
        self.run_javascript('''
          const c=context();assert.equal(install(c).reason,'mount-failed');
          for(const name of ['CodexUsageBarSprig','CodexUsageBar','CodexUsageBarAdapter'])
            assert.equal(name in c.window,false);
          assert.equal(c.window[key],undefined);
        ''', overrides={'bar.js': 'throw new Error("synthetic later asset failure");'})


class RendererCollectionTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.session.closed = False
        self.session.list_codex_pages.return_value = ('first', 'second')
        self.session.attach_codex_target.side_effect = lambda target: SimpleNamespace(
            target_id=target, session_id='session-' + target)
        self.created = []
        def create(session, page, *, token, home_only):
            renderer = MagicMock()
            renderer.session, renderer.page = session, page
            renderer.token, renderer.home_only = token, home_only
            renderer.installed = False
            renderer.install.side_effect = lambda _: setattr(renderer, 'installed', True)
            renderer.dispose.side_effect = lambda: setattr(renderer, 'installed', False)
            renderer.update.return_value = {'mounted': True, 'visible': True}
            self.created.append(renderer)
            return renderer
        self.factory = patch('codex_bar.manager.Renderer', side_effect=create).start()
        self.connect = patch('codex_bar.manager.BrowserSession.connect', return_value=self.session).start()
        self.addCleanup(patch.stopall)
        self.collection = RendererCollection(12345, lambda: True, {'fixture': 'owned'})

    def test_all_canonical_windows_share_snapshot_but_have_independent_tokens(self):
        snapshot = {'status': 'fresh'}
        result = self.collection.sync(snapshot, now=0)
        self.assertEqual(result, {'pages': 2, 'mounted': 2, 'visible': 2, 'unavailable': 0})
        self.assertEqual(len(self.created), 2)
        self.assertNotEqual(self.created[0].token, self.created[1].token)
        for renderer in self.created:
            self.assertFalse(renderer.home_only)
            renderer.update.assert_called_once_with(snapshot)
        self.connect.assert_called_once()

    def test_new_collection_recovers_same_per_page_tokens_from_verified_host_seed(self):
        first = RendererCollection(12345, lambda: True, {}, recovery_seed='a' * 64)
        first.sync({}, now=0)
        original = {key: value['token'] for key, value in first.targets.items()}
        restored = RendererCollection(12345, lambda: True, {}, recovery_seed='a' * 64)
        restored.sync({}, now=5)
        self.assertEqual({key: value['token'] for key, value in restored.targets.items()}, original)
        new_host = RendererCollection(12345, lambda: True, {}, recovery_seed='b' * 64)
        new_host.sync({}, now=5)
        for key in original:
            self.assertNotEqual(new_host.targets[key]['token'], original[key])

    def test_invalid_recovery_identity_refuses_before_connection(self):
        for seed in (None, True, 1, '', 'z' * 64, 'a' * 63, 'a' * 65):
            with self.subTest(seed=seed), self.assertRaisesRegex(ManagerError, 'recovery_identity'):
                recovery_token(seed, 'page')
        for target in ('', 'a' * 257, 'page\n', '../page', '页面', None):
            with self.subTest(target=target), self.assertRaisesRegex(ManagerError, 'recovery_identity'):
                recovery_token('a' * 64, target)
        self.connect.assert_not_called()

    def test_locale_follows_focused_page_then_visible_page_and_emits_only_changes(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        first.update.return_value = {'locale': 'en', 'pageVisible': True, 'focused': False}
        second.update.return_value = {'locale': 'zh-CN', 'pageVisible': True, 'focused': True}
        with patch('builtins.print') as output:
            self.collection.sync({}, now=5)
            output.assert_called_once_with('codex-usage-bar-locale:zh-CN', flush=True)
            self.collection.sync({}, now=10)
            self.assertEqual(output.call_count, 1)
            # The same focused page changes the app language in place.
            second.update.return_value['locale'] = 'en'
            self.collection.sync({}, now=15)
            self.assertEqual(output.call_args.args, ('codex-usage-bar-locale:en',))
            first.update.return_value = {'locale': 'en', 'pageVisible': False, 'focused': False}
            second.update.return_value = {'locale': 'zh-CN', 'pageVisible': True, 'focused': False}
            self.collection.sync({}, now=20)
            self.assertEqual(output.call_args.args, ('codex-usage-bar-locale:zh-CN',))
            self.assertEqual(output.call_count, 3)

    def test_locale_ties_keep_known_language_and_missing_or_hidden_pages_do_not_reset_it(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        first.update.return_value = {'locale': 'en', 'pageVisible': True}
        second.update.return_value = {'locale': 'zh-CN', 'pageVisible': True, 'focused': True}
        with patch('builtins.print') as output:
            self.collection.sync({}, now=5)
            second.update.return_value['focused'] = False
            self.collection.sync({}, now=10)
            self.assertEqual(output.call_count, 1, 'discovery order must not override the known language on a tie')
            first.update.return_value = {'locale': 'en', 'pageVisible': False}
            second.update.return_value = {'locale': None, 'pageVisible': True, 'focused': True}
            self.collection.sync({}, now=15)
            self.session.list_codex_pages.return_value = ()
            self.collection.sync({}, now=20)
            self.assertEqual(output.call_count, 1)
            self.assertEqual(self.collection._locale, 'zh-CN')

    def test_invalid_locale_and_non_boolean_attention_never_reach_stdout(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        with patch('builtins.print') as output:
            for index, value in enumerate(('en-GB', 'zh', 'zh_CN', ' en', 'en\nPRIVATE',
                                           None, True, 1, ['en'], {'locale': 'en'})):
                first.update.return_value = {'locale': value, 'focused': True, 'pageVisible': True}
                second.update.return_value = {'locale': 'en', 'focused': 'true', 'pageVisible': 1}
                self.collection.sync({}, now=5 + index * 5)
            output.assert_not_called()
            self.assertIsNone(self.collection._locale)

    def test_unavailable_focused_page_cannot_publish_stale_locale(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        first.update.side_effect = CDPError('unavailable')
        second.update.return_value = {'locale': 'en', 'pageVisible': True, 'focused': False}
        with patch('builtins.print') as output:
            result = self.collection.sync({}, now=5)
            self.assertEqual(result['unavailable'], 1)
            output.assert_called_once_with('codex-usage-bar-locale:en', flush=True)
            self.collection.sync({}, now=6)
            self.assertEqual(output.call_count, 1)

    def test_new_window_attaches_and_closed_window_does_not_interrupt_survivor(self):
        self.collection.sync({}, now=0)
        original = self.created.copy()
        self.session.list_codex_pages.return_value = ('second', 'third')
        self.collection.sync({}, now=5)
        self.assertEqual(set(self.collection.targets), {'second', 'third'})
        self.session.detach_page.assert_called_once_with(original[0].page)
        original[0].dispose.assert_not_called()
        self.assertEqual(original[1].update.call_count, 2)
        self.assertEqual(len(self.created), 3)

    def test_page_reload_reinstalls_same_token_without_touching_other_window(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        first.update.side_effect = [ManagerError('renderer_bridge_lost'),
                                   {'mounted': True, 'visible': True}]
        result = self.collection.sync({}, now=5)
        self.assertEqual(result['visible'], 2)
        self.assertEqual(first.install.call_count, 2)
        first.dispose.assert_not_called()
        self.assertEqual(second.install.call_count, 1)

    def test_frontend_rejection_rebuilds_owned_adapter_once(self):
        self.collection.sync({}, now=0)
        first = self.created[0]
        first.update.side_effect = [ManagerError('renderer_update_refused'),
                                   {'mounted': True, 'visible': True}]
        self.assertEqual(self.collection.sync({}, now=5)['visible'], 2)
        first.dispose.assert_called_once()
        self.assertEqual(first.install.call_count, 2)

    def test_one_target_remote_error_keeps_other_window_running(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        first.update.side_effect = CDPError('window closed')
        result = self.collection.sync({}, now=5)
        self.assertEqual(result['visible'], 1)
        self.assertEqual(result['unavailable'], 1)
        self.assertEqual(second.update.call_count, 2)
        self.session.close.assert_not_called()

    def test_foreign_bridge_is_never_disposed_and_retries_back_off(self):
        self.session.list_codex_pages.return_value = ('first',)
        original_factory = self.factory.side_effect
        def foreign(*args, **kwargs):
            renderer = original_factory(*args, **kwargs)
            renderer.install.side_effect = ManagerError('renderer_install_refused')
            return renderer
        self.factory.side_effect = foreign
        for now in (0, 1, 4, 5, 6, 10, 14, 15):
            self.assertEqual(self.collection.sync({}, now=now)['unavailable'], 1)
        self.assertEqual(len(self.created), 3)  # attempts at 0, 5, 15 only
        self.assertEqual(len({renderer.token for renderer in self.created}), 1)
        for renderer in self.created:
            renderer.dispose.assert_not_called()

    def test_transport_reconnect_preserves_tokens_and_rebinds_target_sessions(self):
        self.collection.sync({}, now=0)
        tokens = [renderer.token for renderer in self.created]
        second_session = MagicMock()
        second_session.closed = False
        second_session.list_codex_pages.return_value = ('first', 'second')
        second_session.attach_codex_target.side_effect = self.session.attach_codex_target.side_effect
        self.session.closed = True
        self.connect.return_value = second_session
        self.assertEqual(self.collection.sync({}, now=5)['visible'], 2)
        self.assertEqual([renderer.token for renderer in self.created[2:]], tokens)
        self.assertEqual(self.connect.call_count, 2)
        for renderer in self.created[2:]:
            self.assertIs(renderer.session, second_session)

    def test_cleanup_reconnects_with_owned_tokens_without_reinstalling_ui(self):
        self.collection.sync({}, now=0)
        tokens = [renderer.token for renderer in self.created]
        self.session.closed = True
        replacement = MagicMock()
        replacement.closed = False
        replacement.list_codex_pages.return_value = ('first', 'second')
        replacement.attach_codex_target.side_effect = self.session.attach_codex_target.side_effect
        self.connect.return_value = replacement
        self.collection.dispose()
        self.assertEqual([renderer.token for renderer in self.created[2:]], tokens)
        for renderer in self.created[2:]:
            renderer.install.assert_not_called()
            renderer.dispose.assert_called_once()
        self.assertEqual(self.collection.targets, {})

    def test_cleanup_failure_in_one_window_still_attempts_other_window(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        first.dispose.side_effect = ManagerError('unverified')
        with self.assertRaisesRegex(ManagerError, 'daily_renderer_cleanup_unverified'):
            self.collection.dispose()
        second.dispose.assert_called_once()

    def test_cleanup_timeout_reconnects_once_and_cleans_all_original_owned_windows(self):
        self.collection.sync({}, now=0)
        tokens = {renderer.page.target_id: renderer.token for renderer in self.created}
        first = self.created[0]
        def timeout():
            self.session.closed = True
            raise CDPError('transport receive failed')
        first.dispose.side_effect = timeout
        replacement = MagicMock()
        replacement.closed = False
        replacement.list_codex_pages.return_value = ('first', 'second', 'new-foreign')
        replacement.attach_codex_target.side_effect = self.session.attach_codex_target.side_effect
        self.connect.return_value = replacement
        self.collection.dispose()
        self.assertEqual(self.connect.call_count, 2)  # initial sync + one recovery
        self.assertEqual({renderer.page.target_id: renderer.token for renderer in self.created[2:]}, tokens)
        self.assertEqual([renderer.page.target_id for renderer in self.created[2:]], ['second', 'first'])
        self.assertEqual(replacement.attach_codex_target.call_count, 2)
        for renderer in self.created[2:]:
            renderer.install.assert_not_called()
            renderer.dispose.assert_called_once()
        self.assertEqual(self.collection.targets, {})

    def test_cleanup_persistent_timeout_is_deferred_until_healthy_window_is_cleaned(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        cleanup_order = []
        def timeout(session):
            cleanup_order.append('first')
            session.closed = True
            raise CDPError('transport receive failed')
        first.dispose.side_effect = lambda: timeout(self.session)
        replacement = MagicMock()
        replacement.closed = False
        replacement.list_codex_pages.return_value = ('first', 'second')
        replacement.attach_codex_target.side_effect = self.session.attach_codex_target.side_effect
        self.connect.return_value = replacement
        original_factory = self.factory.side_effect
        def create(*args, **kwargs):
            renderer = original_factory(*args, **kwargs)
            if renderer.page.target_id == 'first':
                renderer.dispose.side_effect = lambda: timeout(replacement)
            else:
                renderer.dispose.side_effect = lambda: cleanup_order.append('second')
            return renderer
        self.factory.side_effect = create
        with self.assertRaisesRegex(ManagerError, 'daily_renderer_cleanup_unverified'):
            self.collection.dispose()
        self.assertEqual(cleanup_order, ['first', 'second', 'first'])
        self.assertEqual(self.connect.call_count, 2)
        recovered_second = self.created[2]
        self.assertEqual(recovered_second.token, second.token)
        recovered_second.dispose.assert_called_once()
        for renderer in self.created[2:]:
            renderer.install.assert_not_called()

    def test_cleanup_window_closing_during_disposal_is_not_failure(self):
        self.collection.sync({}, now=0)
        first, second = self.created
        def closed_window():
            self.session.list_codex_pages.return_value = ('second',)
            raise CDPError('window closed')
        first.dispose.side_effect = closed_window
        self.collection.dispose()
        second.dispose.assert_called_once()
        self.assertEqual(self.connect.call_count, 1)
        self.assertEqual(self.collection.targets, {})

    def test_cleanup_reconnection_budget_is_bounded_and_does_not_claim_success(self):
        self.collection.sync({}, now=0)
        first = self.created[0]
        def timeout():
            self.session.closed = True
            raise CDPError('transport receive failed')
        first.dispose.side_effect = timeout
        self.connect.side_effect = CDPError('reconnection failed')
        with self.assertRaisesRegex(ManagerError, 'daily_renderer_cleanup_unverified'):
            self.collection.dispose()
        self.assertEqual(self.connect.call_count, 2)
        self.assertEqual(set(self.collection.targets), {'first', 'second'})


class DailyManagerTests(unittest.TestCase):
    def setUp(self):
        self.host = MagicMock()
        self.host.__enter__.return_value = self.host
        self.host.quota_home = Path('/fixture/.codex')
        self.host.launch_or_attach.return_value = SimpleNamespace(port=12345)
        self.host.recovery_seed.return_value = 'a' * 64
        self.host.is_running.return_value = True
        self.host.detach.return_value = {'appLeftRunning': True}

    def test_refuses_active_foreign_instance_without_closing_it(self):
        self.host.launch_or_attach.side_effect = HostError('daily_instance_running')
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}):
            with self.assertRaisesRegex(HostError, 'daily_instance_running'):
                run_daily()
        self.host.stop.assert_not_called()

    def test_wait_can_be_cancelled_without_starting_or_stopping_codex(self):
        stop = [False]
        def cancel(_):
            stop[0] = True
        self.host.launch_or_attach.side_effect = HostError('daily_instance_running')
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.stop_signals') as signals, \
             patch('codex_bar.manager.time.sleep', side_effect=cancel), patch('builtins.print'):
            signals.return_value.__enter__.return_value = stop
            run_daily(wait_for_exit=True)
        self.host.launch_or_attach.assert_called_once()
        self.host.stop.assert_not_called()
        self.host.detach.assert_not_called()

    def test_connection_failure_detaches_and_never_stops_daily_codex(self):
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession.connect', side_effect=ManagerError('fixture')), \
             patch('builtins.print'):
            with self.assertRaisesRegex(ManagerError, 'fixture'):
                run_daily(duration=1)
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()

    def test_daily_deadline_disposes_bar_but_preserves_app_and_uses_daily_quota_home(self):
        clock = [0.0]
        renderer = MagicMock()
        renderer.sync.return_value = {'mounted': 1, 'visible': 1, 'unavailable': 0}
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession') as browser, \
             patch('codex_bar.manager.RendererCollection', return_value=renderer), \
             patch('codex_bar.manager.CodexQuotaClient') as quota, \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0]+s)), \
             patch('builtins.print'):
            run_daily(duration=1)
        quota.assert_called_once_with(codex_home=Path('/fixture/.codex'))
        renderer.sync.assert_called_once()
        renderer.dispose.assert_called_once()
        renderer.close.assert_called_once()
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()

    def test_transient_connection_failures_retry_on_heartbeat_with_one_quota_worker(self):
        clock = [0.0]
        collection = MagicMock()
        collection.sync.side_effect = [CDPError('unavailable'), CDPError('unavailable'),
                                      {'mounted': 1, 'visible': 1, 'unavailable': 0}]
        worker = MagicMock()
        worker.submit.return_value.done.return_value = False
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.RendererCollection', return_value=collection), \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0]+s)), \
             patch('builtins.print'):
            run_daily(duration=11)
        self.assertEqual(collection.sync.call_count, 3)
        worker.submit.assert_called_once()
        collection.dispose.assert_called_once()
        collection.close.assert_called_once()
        self.host.stop.assert_not_called()

    def test_user_quitting_app_is_normal_and_skips_disposing_dead_renderer(self):
        self.host.is_running.return_value = False
        self.host.detach.return_value = {'appLeftRunning': False}
        renderer = MagicMock()
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession'), \
             patch('codex_bar.manager.RendererCollection', return_value=renderer), \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor'), patch('builtins.print'):
            run_daily(duration=1)
        renderer.dispose.assert_not_called()
        self.host.stop.assert_not_called()
        self.host.detach.assert_called_once()

    def test_daily_ack_and_mixed_profile_flags_are_rejected_before_actions(self):
        for args in (['daily'], ['daily', '--acknowledge-runtime', '--reuse-approved-profile'],
                     ['status', '--wait-for-exit'], ['status', '--resident'],
                     ['run', '--acknowledge-runtime', '--resident'], ['daily', '--resident'],
                     ['daily', '--acknowledge-runtime', '--supervisor-pid', '123'],
                     ['daily', '--acknowledge-runtime', '--launch-once'],
                     ['daily', '--resident', '--acknowledge-runtime', '--supervisor-pid', '1'],
                     ['daily', '--resident', '--acknowledge-runtime', '--supervisor-pid', '2147483648']):
            with patch('codex_bar.manager.DailyHost') as host, patch('sys.stderr'), self.assertRaises(SystemExit):
                main(args)
            host.assert_not_called()

    def test_resident_cli_dispatches_into_live_loop_entrypoint(self):
        with patch('codex_bar.manager.run_daily') as run:
            self.assertEqual(main(['daily', '--resident', '--acknowledge-runtime', '--duration', '9']), 0)
        run.assert_called_once_with(duration=9, wait_for_exit=False, resident=True, supervisor_pid=None, launch_once=False)

    def test_supervised_cli_passes_identity_to_real_entrypoint(self):
        with patch('codex_bar.manager.run_daily') as run:
            self.assertEqual(main(['daily', '--resident', '--acknowledge-runtime',
                                   '--supervisor-pid', '12345']), 0)
        run.assert_called_once_with(duration=0, wait_for_exit=False, resident=True, supervisor_pid=12345, launch_once=False)

    def test_explicit_launch_once_cli_passes_only_the_requested_permission(self):
        with patch('codex_bar.manager.run_daily') as run:
            self.assertEqual(main(['daily', '--resident', '--acknowledge-runtime', '--launch-once']), 0)
        run.assert_called_once_with(duration=0, wait_for_exit=False, resident=True,
                                    supervisor_pid=None, launch_once=True)

    def test_wrong_supervisor_rejected_before_manager_lock(self):
        with patch('codex_bar.manager.os.getppid', return_value=321), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.DailyHost') as host, \
             self.assertRaisesRegex(ManagerError, 'invalid_supervisor_identity'):
            run_daily(resident=True, supervisor_pid=123)
        host.assert_not_called()

    def test_supervisor_loss_is_latched_and_pid_reappearance_does_not_resume(self):
        stop, parent = [False], [123]
        with patch('codex_bar.manager.os.getppid', side_effect=lambda: parent[0]):
            with supervisor_lifetime(stop, 123):
                parent[0] = 1
                deadline = time.monotonic() + 1
                while not stop[0] and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(stop[0])
                parent[0] = 123
                time.sleep(.25)
                self.assertTrue(stop[0])

    def test_resident_observer_never_grants_launch_permission(self):
        clock = [0.0]
        self.host.launch_or_attach.return_value = None
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager._run_daily_session') as session, \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
             patch('builtins.print') as output:
            run_daily(resident=True, duration=3)
        self.assertEqual(self.host.launch_or_attach.call_count, 3)
        self.assertTrue(all(call.kwargs['allow_launch'] is False
                            for call in self.host.launch_or_attach.call_args_list))
        session.assert_not_called()
        self.assertEqual([call.args[0] for call in output.call_args_list],
                         ['codex-usage-bar-resident:waiting'])

    def test_resident_launch_once_is_consumed_after_one_session(self):
        clock = [0.0]
        endpoints = iter([SimpleNamespace(port=12345)])
        self.host.launch_or_attach.side_effect = lambda **_: next(endpoints, None)
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager._run_daily_session') as session, \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
             patch('builtins.print') as output:
            run_daily(resident=True, launch_once=True, duration=3)
        attempts = self.host.launch_or_attach.call_args_list
        self.assertIs(attempts[0].kwargs['allow_launch'], True)
        self.assertGreater(len(attempts), 1)
        self.assertTrue(all(call.kwargs['allow_launch'] is False for call in attempts[1:]))
        session.assert_called_once()
        self.assertEqual([call.args[0] for call in output.call_args_list],
                         ['codex-usage-bar-resident:waiting'])

    def test_launch_once_failed_after_spawn_cannot_grant_another_launch(self):
        clock, attempts = [0.0], []
        self.host.last_launch_failure = None
        def attach(*, allow_launch, cancelled):
            attempts.append(allow_launch)
            if len(attempts) == 1:
                self.host.last_launch_failure = {'status': 'daily_launch_failed_app_not_stopped'}
                raise HostError('daily_instance_running')
            return None
        self.host.launch_or_attach.side_effect = attach
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager._run_daily_session') as session, \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
             patch('builtins.print') as output:
            run_daily(resident=True, launch_once=True, duration=3)
        self.assertEqual(attempts, [True, False, False])
        session.assert_not_called()
        self.assertEqual([call.args[0] for call in output.call_args_list],
                         ['codex-usage-bar-resident:needs-launcher', 'codex-usage-bar-resident:waiting'])

    def test_stop_during_resident_idle_wait_does_not_launch(self):
        stop = [False]
        self.host.launch_or_attach.return_value = None
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager._run_daily_session') as session, \
             patch('codex_bar.manager.stop_signals') as signals, \
             patch('codex_bar.manager.time.sleep', side_effect=lambda _: stop.__setitem__(0, True)), \
             patch('builtins.print'):
            signals.return_value.__enter__.return_value = stop
            run_daily(resident=True)
        self.host.launch_or_attach.assert_called_once()
        self.assertIs(self.host.launch_or_attach.call_args.kwargs['allow_launch'], False)
        self.assertTrue(self.host.launch_or_attach.call_args.kwargs['cancelled']())
        session.assert_not_called()
        self.host.stop.assert_not_called()

    def test_resident_quota_exception_hides_old_values_then_recovers_without_restarting(self):
        clock = [0.0]
        failed, succeeded = Future(), Future()
        failed.set_exception(RuntimeError('PRIVATE_ERROR_PAYLOAD'))
        succeeded.set_result({'status': 'fresh'})
        worker = MagicMock()
        worker.submit.side_effect = [failed, succeeded]
        collection = MagicMock()
        collection.sync.return_value = {'mounted': 1, 'visible': 1, 'unavailable': 0}
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.RendererCollection', return_value=collection), \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge') as bridge, \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), \
             patch('codex_bar.manager.time.monotonic', side_effect=lambda: clock[0]), \
             patch('codex_bar.manager.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
             patch('builtins.print') as output:
            bridge.return_value.snapshot.return_value = {'status': 'fresh', 'limits': {'fixture': 4}}
            run_daily(duration=7, resident=True)
        self.assertEqual(worker.submit.call_count, 2)
        snapshots = [call.args[0] for call in collection.sync.call_args_list]
        self.assertIn({'status': 'read_failed', 'limits': {}}, snapshots)
        self.assertEqual(snapshots[-1]['status'], 'fresh')
        messages = [call.args[0] for call in output.call_args_list]
        self.assertIn('codex-usage-bar-resident:quota-retrying', messages)
        self.assertEqual(messages.count('codex-usage-bar-resident:attached'), 2)
        recovered = max(index for index, message in enumerate(messages)
                        if message == 'codex-usage-bar-resident:attached')
        self.assertTrue(messages[recovered + 1].startswith('额度条已在 1 个窗口显示；'),
                        'quota recovery must restore the native visible status after attached')
        self.assertNotIn('PRIVATE_ERROR_PAYLOAD', str(messages))
        self.host.launch_or_attach.assert_called_once()
        collection.dispose.assert_called_once()

    def test_resident_identity_error_is_not_retried_as_quota_or_reopen(self):
        self.host.recovery_seed.side_effect = HostError('owned_command_mismatch')
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.ThreadPoolExecutor') as worker, \
             patch('builtins.print') as output, \
             self.assertRaisesRegex(HostError, 'owned_command_mismatch'):
            run_daily(resident=True)
        self.host.launch_or_attach.assert_called_once()
        worker.assert_not_called()
        messages = [call.args[0] for call in output.call_args_list]
        self.assertNotIn('codex-usage-bar-resident:reopening', messages)
        self.assertNotIn('codex-usage-bar-resident:quota-retrying', messages)

    def test_killed_dummy_supervisor_releases_real_manager_lock_without_codex(self):
        # The production resident loop, lifetime watcher and DailyHost lock are
        # real. Only Codex/transport/quota operations are replaced by fixtures.
        # An unsupervised control proves this is not an OS process-group cleanup.
        child_source = r'''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from codex_bar.daily_host import DailyHost
from codex_bar.manager import run_daily
host = object.__new__(DailyHost)
host.state_root = Path(sys.argv[1])
host._lock_fd = None
host.quota_home = host.state_root / 'unused-quota'
host.launch_or_attach = lambda **options: SimpleNamespace(port=12345)
host.recovery_seed = lambda: 'a' * 64
host.validate = lambda: True
host.is_running = lambda: True
host.detach = lambda: {'appLeftRunning': True}
worker = MagicMock()
worker.submit.return_value.done.return_value = False
collection = MagicMock()
def sync(*args, **kwargs):
    print('FIXTURE_MANAGER_READY', flush=True)
    return {'mounted': 1, 'visible': 1, 'unavailable': 0}
collection.sync.side_effect = sync
with patch('codex_bar.manager.DailyHost', return_value=host), \
     patch('codex_bar.manager.read_assets', return_value={}), \
     patch('codex_bar.manager.RendererCollection', return_value=collection), \
     patch('codex_bar.manager.CodexQuotaClient'), \
     patch('codex_bar.manager.QuotaBridge'), \
     patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker):
    run_daily(resident=True, duration=3,
              supervisor_pid=int(sys.argv[2]) if sys.argv[3] == 'supervised' else None)
assert host._lock_fd is None
assert collection.dispose.call_count == 1
print('FIXTURE_MANAGER_EXITED', flush=True)
'''
        parent_source = ('import os, subprocess, sys, time\n'
                         'subprocess.Popen([sys.executable,"-B","-c",' + repr(child_source) +
                         ',sys.argv[1],str(os.getpid()),sys.argv[2]])\n'
                         'time.sleep(6)\n')
        for supervised in (False, True):
            with self.subTest(supervised=supervised), tempfile.TemporaryDirectory() as root:
                parent = subprocess.Popen([sys.executable, '-B', '-c', parent_source, root,
                                           'supervised' if supervised else 'control'],
                                          cwd=Path(__file__).resolve().parents[1],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          start_new_session=True)
                selector = selectors.DefaultSelector()
                selector.register(parent.stdout, selectors.EVENT_READ)
                observed = b''
                try:
                    deadline = time.monotonic() + 5
                    while b'FIXTURE_MANAGER_READY' not in observed and time.monotonic() < deadline:
                        if selector.select(.1):
                            data = os.read(parent.stdout.fileno(), 4096)
                            if not data:
                                break
                            observed += data
                    self.assertIn(b'FIXTURE_MANAGER_READY', observed)
                    with open(Path(root) / 'manager.lock', 'r+b') as lock:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        parent.kill()  # Positive PID of this test's own dummy parent.
                        parent.wait(timeout=3)
                        deadline = time.monotonic() + (1.5 if supervised else .5)
                        released = False
                        while time.monotonic() < deadline:
                            try:
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                released = True
                                fcntl.flock(lock, fcntl.LOCK_UN)
                                break
                            except BlockingIOError:
                                time.sleep(.02)
                        self.assertEqual(released, supervised)
                    tail, _ = parent.communicate(timeout=5)
                    self.assertIn(b'FIXTURE_MANAGER_EXITED', observed + tail)
                finally:
                    selector.close()
                    if parent.poll() is None:
                        parent.kill()
                    # Dummy child has a three-second deadline even on assertion
                    # failure; no unrelated PID or process group is signalled.
                    parent.communicate(timeout=5)

    def test_liveness_failure_cannot_skip_transport_worker_or_host_cleanup(self):
        self.host.is_running.side_effect = HostError('process_inspection_failed')
        worker = MagicMock()
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession') as browser, \
             patch('codex_bar.manager.RendererCollection') as collection, \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor', return_value=worker), patch('builtins.print'):
            with self.assertRaisesRegex(HostError, 'process_inspection_failed'):
                run_daily(duration=1)
        collection.return_value.close.assert_called_once()
        worker.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()

    def test_quit_racing_renderer_disposal_is_normal_if_all_owned_processes_and_port_gone(self):
        self.host.is_running.side_effect = [False, True]
        self.host.detach.return_value = {'status': 'stopped', 'appLeftRunning': False,
                                       'ownedProcessCount': 0, 'debugPortOpen': False}
        renderer = MagicMock()
        renderer.dispose.side_effect = ManagerError('renderer_gone')
        with patch('codex_bar.manager.DailyHost', return_value=self.host), \
             patch('codex_bar.manager.read_assets', return_value={}), \
             patch('codex_bar.manager.BrowserSession'), \
             patch('codex_bar.manager.RendererCollection', return_value=renderer), \
             patch('codex_bar.manager.CodexQuotaClient'), \
             patch('codex_bar.manager.QuotaBridge'), \
             patch('codex_bar.manager.ThreadPoolExecutor'), patch('builtins.print'):
            run_daily(duration=1)
        self.host.detach.assert_called_once()
        self.host.stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
