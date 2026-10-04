/* SPDX-License-Identifier: MIT
 * Offline regression tests execute the shipped JavaScript in a small synthetic
 * DOM. They do not launch Codex, read a profile, or assert real browser geometry.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ROOT = path.resolve(__dirname, '..');

class Events {
  constructor() { this.events = new Map(); }
  addEventListener(type, fn) {
    if (!this.events.has(type)) this.events.set(type, new Set());
    this.events.get(type).add(fn);
  }
  removeEventListener(type, fn) { this.events.get(type)?.delete(fn); }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    for (const fn of this.events.get(event.type) || []) fn(event);
    return true;
  }
  listenerCount() { return [...this.events.values()].reduce((n, fns) => n + fns.size, 0); }
}

function environment({ renderer = true, adapter = true } = {}) {
  let now = 1_800_000_000_000;
  let sequence = 0;
  const frames = new Map(), timers = new Map(), intervals = new Map(), observers = [];
  const rect = (x, y, width, height) => ({ x, y, left: x, top: y, right: x + width,
    bottom: y + height, width, height });
  class Element extends Events {
    constructor(tag, owner) {
      super(); this.nodeType = 1; this.tagName = tag.toUpperCase(); this.ownerDocument = owner;
      this.parentElement = null; this.childNodes = []; this.attributes = new Map();
      this.dataset = {}; this.hidden = false; this.inert = false; this.className = '';
      this.style = { setProperty(name, value) { this[name] = value; } };
      this._text = ''; this._html = '';
      this.classList = {
        contains: name => this.className.split(/\s+/).includes(name),
        add: (...names) => { this.className = [...new Set(this.className.split(/\s+/).concat(names))].join(' ').trim(); },
        remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(' '); }
      };
    }
    get children() { return this.childNodes.filter(node => node.nodeType === 1); }
    get textContent() { return this._text + this.childNodes.map(node => node.textContent || '').join(''); }
    set textContent(value) { this._text = String(value); this.childNodes = []; }
    get innerHTML() { return this._html; }
    set innerHTML(value) { this._html = value; this.childNodes = []; }
    append(...nodes) { for (const node of nodes) this.appendChild(node); }
    appendChild(node) { node.remove?.(); this.childNodes.push(node); node.parentElement = this; return node; }
    remove() {
      if (this.parentElement) this.parentElement.childNodes = this.parentElement.childNodes.filter(node => node !== this);
      this.parentElement = null;
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    hasAttribute(name) { return this.attributes.has(name); }
    removeAttribute(name) { this.attributes.delete(name); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) {
      return selector.split(',').some(branch => {
        const attrs = [...branch.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)];
        const classes = [...branch.matchAll(/\.([\w-]+)/g)];
        return attrs.every(([, key, value]) => this.hasAttribute(key) && (value === undefined || this.getAttribute(key) === value)) &&
          classes.every(([, value]) => this.classList.contains(value)) && (attrs.length + classes.length > 0);
      });
    }
    querySelectorAll(selector) {
      const result = [];
      for (const child of this.children) {
        if (child.matches(selector)) result.push(child);
        result.push(...child.querySelectorAll(selector));
      }
      return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    attachShadow() { this.shadowRoot = new Element('shadow-root', this.ownerDocument); this.shadowRoot.host = this; return this.shadowRoot; }
    getRootNode() { let node = this; while (node.parentElement) node = node.parentElement; return node.host ? node : this.ownerDocument; }
    get isConnected() {
      let node = this; while (node.parentElement) node = node.parentElement;
      return node === this.ownerDocument.documentElement || Boolean(node.host?.isConnected);
    }
    focus() {
      const previous = this.ownerDocument.activeElement;
      if (previous?.shadowRoot) previous.shadowRoot.activeElement = null;
      const scope = this.getRootNode(); scope.activeElement = this;
      this.ownerDocument.activeElement = scope.host || this;
      this.dispatchEvent({ type: 'focus' });
    }
    getBoundingClientRect() {
      if (this.style.display === 'none') return rect(0, 0, 0, 0);
      if (this.box) return this.box;
      if (this === this.ownerDocument.documentElement || this === this.ownerDocument.body) return rect(0, 0, 1200, 900);
      if (this.classList.contains('ProseMirror')) return rect(100, 310, 600, 100);
      if (this.classList.contains('cbu-bar')) return rect(100, 200, 600, 90);
      return rect(100, 200, 600, 100);
    }
    get offsetWidth() { return this.getBoundingClientRect().width; }
    get offsetHeight() { return this.getBoundingClientRect().height; }
    get clientWidth() { return this.offsetWidth; }
    get clientHeight() { return this.offsetHeight; }
    get clientLeft() { return 0; }
    get clientTop() { return 0; }
    get scrollWidth() { return this.offsetWidth; }
  }
  const document = new Events();
  document.createElement = tag => new Element(tag, document);
  document.documentElement = document.createElement('html');
  document.body = document.createElement('body'); document.documentElement.append(document.body);
  document.querySelectorAll = selector => document.documentElement.querySelectorAll(selector);
  document.querySelector = selector => document.documentElement.querySelector(selector);
  document.activeElement = document.body;
  const media = new Events(); media.matches = false;
  class Observer {
    constructor(fn) { this.fn = fn; this.disconnected = false; this.nodes = new Set(); observers.push(this); }
    observe(node) { this.nodes.add(node); } unobserve(node) { this.nodes.delete(node); }
    disconnect() { this.disconnected = true; this.nodes.clear(); }
  }
  const window = new Events();
  Object.assign(window, {
    document, location: { href: 'app://-/' }, innerWidth: 1200, innerHeight: 900,
    performance: { now: () => now }, MutationObserver: Observer, ResizeObserver: Observer,
    requestAnimationFrame: fn => { const id = ++sequence; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id), matchMedia: () => media,
    setTimeout: (fn, delay) => { const id = ++sequence; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout: id => timers.delete(id),
    setInterval: fn => { const id = ++sequence; intervals.set(id, fn); return id; },
    clearInterval: id => intervals.delete(id),
    getComputedStyle: node => Object.assign({ display: 'block', visibility: 'visible', opacity: '1',
      contentVisibility: 'visible', position: 'static', overflowX: 'visible', overflowY: 'visible',
      transform: 'none', zoom: '1' }, node.style),
    CustomEvent: class { constructor(type, options) { this.type = type; Object.assign(this, options); } }
  });
  document.defaultView = window;
  const context = vm.createContext({ window, document, Element, URL, MutationObserver: Observer,
    ResizeObserver: Observer, Date: class extends Date { static now() { return now; } }, console });
  function load(name) { vm.runInContext(fs.readFileSync(path.join(ROOT, 'web', name), 'utf8'), context, { filename: name }); }
  if (renderer) load('bar.js');
  if (adapter) load('adaptive.js');
  const composer = document.createElement('div');
  composer.setAttribute('data-codex-composer-root', ''); composer.setAttribute('data-composer-placement', 'home');
  const portal = document.createElement('div'); portal.setAttribute('data-above-composer-portal', '');
  const editor = document.createElement('div'); editor.className = 'ProseMirror'; editor.setAttribute('contenteditable', 'true');
  const paragraph = document.createElement('p'); paragraph.append(document.createElement('br'));
  editor.append(paragraph); composer.append(portal, editor); document.body.append(composer);
  return {
    window, document, composer, portal, editor, media, frames, timers, intervals, observers,
    now: () => now, install: (homeOnly = true) => window.CodexUsageBarAdapter.install({ css: '', homeOnly }),
    flush() { const pending = [...frames.values()]; frames.clear(); for (const fn of pending) fn(); },
    mutate() { for (const observer of observers) if (!observer.disconnected) observer.fn([{ type: 'childList', target: editor, addedNodes: [], removedNodes: [] }]); },
    tick(ms) {
      now += ms;
      for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); }
      for (const fn of [...intervals.values()]) fn();
      this.flush();
    },
    mounted() { return document.querySelector('[data-codex-usage-bar]'); }
  };
}

// A controlled geometry model, separate from the simple DOM above: measurements
// respond to the adapter's actual width/left writes, renderer mode and CSS zoom.
// This tests the algorithm; browser fixtures separately validate browser layout.
function alignmentFixture(overrides = {}) {
  const env = environment();
  const geometry = Object.assign({ left: 100.25, shellWidth: 736.4, scale: 1,
    paddingLeft: 13.2, paddingRight: 7.4, shellTop: 450, hostTop: 180,
    panelHeight: 140, ignoreOffset: false }, overrides);
  let shell = env.document.createElement('div');
  shell.append(env.editor); env.composer.append(shell);
  const prototype = Object.getPrototypeOf(shell);
  const originalRect = prototype.getBoundingClientRect;
  const originalStyle = env.window.getComputedStyle;
  const box = (left, top, width, height) => ({ x: left, y: top, left, top,
    right: left + width, bottom: top + height, width, height });
  const hostWidth = host => host?.style.width && host.style.width !== '100%' ?
    parseFloat(host.style.width) : Math.max(0, geometry.shellWidth - geometry.paddingLeft - geometry.paddingRight);
  const barHeight = () => {
    const bar = env.mounted()?.shadowRoot.querySelector('.cbu-bar');
    const panel = bar?.querySelector('.cbu-source-panel');
    return (bar?.dataset.mode === 'compact' ? 44 : 90) + (panel && !panel.hidden ? geometry.panelHeight : 0);
  };
  prototype.getBoundingClientRect = function () {
    if (this.style.display === 'none') return box(0, 0, 0, 0);
    if (this === shell) return box(geometry.left, geometry.shellTop, geometry.shellWidth * geometry.scale, 130 * geometry.scale);
    if (this === env.editor) return box(geometry.left + 12 * geometry.scale,
      geometry.shellTop + 12 * geometry.scale, (geometry.shellWidth - 24) * geometry.scale, 70 * geometry.scale);
    if (this === env.composer) return box(geometry.left, geometry.hostTop - 10,
      geometry.shellWidth * geometry.scale, geometry.shellTop + 130 * geometry.scale - geometry.hostTop + 10);
    if (this === env.portal) return box(geometry.left, geometry.hostTop,
      geometry.shellWidth * geometry.scale, (barHeight() + 10) * geometry.scale);
    const isBar = this.classList.contains('cbu-bar');
    if (this.hasAttribute('data-codex-usage-bar') || isBar) {
      const host = isBar ? this.getRootNode().host : this;
      const shift = geometry.ignoreOffset ? 0 : parseFloat(host.style.left || '0');
      const width = isBar ? Math.max(320, hostWidth(host)) : hostWidth(host);
      return box(geometry.left + (geometry.paddingLeft + shift) * geometry.scale,
        geometry.hostTop, width * geometry.scale, barHeight() * geometry.scale);
    }
    return originalRect.call(this);
  };
  for (const key of ['offsetWidth', 'clientWidth']) {
    Object.defineProperty(prototype, key, { configurable: true, get() {
      if (this === env.portal || this === env.composer || this === shell) return Math.round(geometry.shellWidth);
      if (this.hasAttribute('data-codex-usage-bar')) return Math.round(hostWidth(this));
      return this.getBoundingClientRect().width;
    } });
  }
  env.window.getComputedStyle = node => {
    const style = originalStyle(node);
    if (node === env.portal) Object.assign(style, { paddingLeft: geometry.paddingLeft + 'px', paddingRight: geometry.paddingRight + 'px' });
    if (node.hasAttribute('data-codex-usage-bar')) style.width = hostWidth(node) + 'px';
    return style;
  };
  return { env, geometry, get shell() { return shell; }, replaceShell() {
    const previous = shell;
    shell = env.document.createElement('div'); shell.append(env.editor);
    previous.remove(); env.composer.append(shell);
    return previous;
  } };
}

function snapshot(env, overrides = {}) {
  return Object.assign({ status: 'fresh', stale: false, sourceLabel: 'Offline fixture', updatedAt: new Date(env.now()).toISOString(),
    limits: { primary: { usedPercent: 12, windowMinutes: 300, resetsAt: env.now() / 1000 + 3600, status: 'fresh' },
      secondary: { usedPercent: 45, windowMinutes: 10080, resetsAt: env.now() / 1000 + 86400, status: 'fresh' } } }, overrides);
}
function cell(root, key) { return root.querySelectorAll('.cbu-metric').find(node => node.dataset.metric === key); }
function label(root, key) { return cell(root, key).querySelector('.cbu-value').textContent; }
function mountBar(env) {
  const host = env.document.createElement('div'); env.document.body.append(host);
  const shadow = host.attachShadow({ mode: 'open' });
  const container = env.document.createElement('div'); shadow.append(container);
  return env.window.CodexUsageBar.mount(container, { theme: 'light', accountOnly: true });
}

const CACHE_THREAD = '11111111-2222-4333-8444-555555555555';
const OTHER_CACHE_THREAD = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const CACHE_HOST = 'offline-cache-host-a';
const OTHER_CACHE_HOST = 'offline-cache-host-b';
function cacheSidebarRow(env, threadId = CACHE_THREAD, hostId = CACHE_HOST) {
  const row = env.document.createElement('div');
  row.setAttribute('data-app-action-sidebar-thread-id', 'local:' + threadId);
  row.setAttribute('data-app-action-sidebar-thread-kind', 'local');
  if (hostId !== null) row.setAttribute('data-app-action-sidebar-thread-host-id', hostId);
  env.document.body.append(row);
  return row;
}
function cacheEnvironment() {
  const env = environment();
  env.composer.setAttribute('data-composer-placement', 'thread');
  env.portal.setAttribute('data-above-composer-conversation-id', CACHE_THREAD);
  const row = cacheSidebarRow(env), api = env.install(false);
  api.setAccountSnapshot(snapshot(env));
  return { env, row, api, bar: () => env.mounted()?.shadowRoot.querySelector('.cbu-bar') };
}
function tokenUsageEvent(inputTokens, cachedInputTokens, overrides = {}) {
  return { type: 'message', source: null, origin: '', data: {
    type: 'mcp-notification', method: 'thread/tokenUsage/updated', hostId: CACHE_HOST,
    params: { threadId: CACHE_THREAD, turnId: 'offline-fixture-turn',
      tokenUsage: { total: { inputTokens, cachedInputTokens } } }, ...overrides
  } };
}

test('quota rendering preserves zero, unknown 5h and unknown cache without using unrelated fields', () => {
  const env = environment({ adapter: false }), bar = mountBar(env);
  const input = snapshot(env); input.limits.primary.usedPercent = 0;
  bar.update(input); assert.equal(label(bar.element, 'primary'), '0%');
  delete input.limits.primary; input.working = true; input.cache = 95; input.context = 'PRIVATE_FIXTURE_DO_NOT_RENDER';
  bar.update(input);
  assert.equal(label(bar.element, 'primary'), '—'); assert.equal(label(bar.element, 'cache'), '—');
  assert.match(cell(bar.element, 'primary').textContent, /当前来源未提供/);
  assert.doesNotMatch(bar.element.textContent, /无限|PRIVATE_FIXTURE|95%/);
  assert.match(bar.element.textContent, /运行状态未接通/);
  bar.destroy(); assert.equal(env.timers.size, 0);
});

test('weekly balance converts valid used quota and preserves unknown states', () => {
  const env = environment({ adapter: false }), bar = mountBar(env);
  for (const [used, remaining] of [[0, 100], [45, 55], [100, 0]]) {
    const input = snapshot(env); input.limits.secondary.usedPercent = used; bar.update(input);
    const weekly = cell(bar.element, 'secondary');
    assert.equal(label(bar.element, 'secondary'), remaining + '%');
    assert.equal(weekly.querySelector('.cbu-fill').style.width, remaining + '%');
    assert.match(weekly.getAttribute('aria-label'), /^每周订阅剩余，/);
    assert.equal(weekly.querySelector('.cbu-compact-label').textContent, '周余');
    assert.equal(label(bar.element, 'primary'), '12%');
  }
  for (const value of [-1, 101, NaN, Infinity, '42', true, null]) {
    const input = snapshot(env); input.limits.secondary.usedPercent = value; bar.update(input);
    assert.equal(label(bar.element, 'secondary'), '—');
  }
  const missing = snapshot(env); delete missing.limits.secondary; bar.update(missing);
  assert.equal(label(bar.element, 'secondary'), '—');
  bar.update(snapshot(env, { status: 'account_changed', stale: true }));
  assert.equal(label(bar.element, 'secondary'), '—');
  const expired = snapshot(env); expired.limits.secondary.resetsAt = env.now() / 1000 - 1; bar.update(expired);
  assert.equal(label(bar.element, 'secondary'), '—');
  bar.destroy();
});

test('malformed quota numbers and wrong windows never render as valid usage', () => {
  const env = environment({ adapter: false }), bar = mountBar(env);
  for (const value of [-1, 101, NaN, Infinity, '42', true, null]) {
    const input = snapshot(env); input.limits.primary.usedPercent = value; bar.update(input);
    assert.equal(label(bar.element, 'primary'), '—');
  }
  const wrong = snapshot(env); wrong.limits.primary.windowMinutes = 240; bar.update(wrong);
  assert.equal(label(bar.element, 'primary'), '—'); bar.destroy();
});

test('reset deadline hides old percentages without waiting for a backend refresh', () => {
  const env = environment({ adapter: false }), bar = mountBar(env);
  const input = snapshot(env); input.limits.primary.resetsAt = env.now() / 1000 + 1;
  bar.update(input); assert.equal(label(bar.element, 'primary'), '12%');
  env.tick(1001); assert.equal(label(bar.element, 'primary'), '—');
  assert.match(cell(bar.element, 'primary').textContent, /等待重置后数据/); bar.destroy();
});

test('stale usage is explicit and account changes remove prior percentages', () => {
  const env = environment({ adapter: false }), bar = mountBar(env);
  bar.update(snapshot(env, { status: 'stale', stale: true }));
  assert.equal(label(bar.element, 'primary'), '12%'); assert.match(cell(bar.element, 'primary').textContent, /旧数据/);
  bar.update(snapshot(env, { status: 'account_changed', stale: true }));
  assert.equal(label(bar.element, 'primary'), '—'); bar.destroy();
});

test('source panel opens only on click and keeps its disclosure state accessible', () => {
  const env = environment({ adapter: false }), bar = mountBar(env), root = bar.element;
  const info = root.querySelector('.cbu-info'), panel = root.querySelector('.cbu-source-panel');
  assert.equal(info.getAttribute('aria-controls'), panel.id);
  assert.equal(info.getAttribute('aria-expanded'), 'false');
  info.dispatchEvent({ type: 'pointerenter' }); info.focus();
  assert.equal(panel.hidden, true); assert.equal(info.getAttribute('aria-expanded'), 'false');
  info.dispatchEvent({ type: 'click' });
  assert.equal(panel.hidden, false); assert.equal(info.getAttribute('aria-expanded'), 'true');
  info.dispatchEvent({ type: 'pointerleave' }); root.dispatchEvent({ type: 'pointerleave' });
  assert.equal(panel.hidden, false);
  info.dispatchEvent({ type: 'click' });
  assert.equal(panel.hidden, true); assert.equal(info.getAttribute('aria-expanded'), 'false');
  info.dispatchEvent({ type: 'click' });
  let stopped = 0;
  root.dispatchEvent({ type: 'keydown', key: 'Escape', stopPropagation() { stopped++; } });
  assert.equal(panel.hidden, true); assert.equal(info.getAttribute('aria-expanded'), 'false');
  assert.equal(root.getRootNode().activeElement, info); assert.equal(stopped, 1);
  root.dispatchEvent({ type: 'keydown', key: 'Escape', stopPropagation() { stopped++; } });
  assert.equal(stopped, 1); bar.destroy();
});

test('outside pointer closes details without consuming native events and shadow clicks stay inside', () => {
  const env = environment({ adapter: false }), bar = mountBar(env), root = bar.element;
  const info = root.querySelector('.cbu-info'), panel = root.querySelector('.cbu-source-panel');
  info.dispatchEvent({ type: 'click' });
  const shadow = root.getRootNode();
  env.document.dispatchEvent({ type: 'pointerdown', target: shadow.host,
    composedPath: () => [panel, root, shadow, shadow.host, env.document],
    preventDefault() { assert.fail('internal click was cancelled'); },
    stopPropagation() { assert.fail('internal click propagation was stopped'); } });
  assert.equal(panel.hidden, false);
  env.document.dispatchEvent({ type: 'pointerdown', target: panel });
  assert.equal(panel.hidden, false);
  let continued = 0;
  const observer = () => { continued++; };
  env.document.addEventListener('pointerdown', observer);
  env.document.dispatchEvent({ type: 'pointerdown', target: env.editor,
    composedPath: () => [env.editor, env.composer, env.document],
    preventDefault() { assert.fail('native composer event was cancelled'); },
    stopPropagation() { assert.fail('native composer event propagation was stopped'); } });
  assert.equal(panel.hidden, true); assert.equal(info.getAttribute('aria-expanded'), 'false');
  assert.equal(continued, 1);
  env.document.removeEventListener('pointerdown', observer); bar.destroy();
  assert.equal(env.document.listenerCount(), 0);
});

test('focus moving within the shadow bar retains details and focus leaving closes them', () => {
  const env = environment({ adapter: false }), bar = mountBar(env), root = bar.element;
  const info = root.querySelector('.cbu-info'), panel = root.querySelector('.cbu-source-panel');
  info.focus(); info.dispatchEvent({ type: 'click' });
  root.querySelector('.cbu-toggle').focus(); root.dispatchEvent({ type: 'focusout' }); env.tick(0);
  assert.equal(panel.hidden, false);
  env.editor.focus(); root.dispatchEvent({ type: 'focusout' }); env.tick(0);
  assert.equal(panel.hidden, true); assert.equal(info.getAttribute('aria-expanded'), 'false');
  bar.destroy(); assert.equal(env.timers.size, 0);
});

test('collapse closes details, updates disclosure labels, and preserves state across data refresh', () => {
  const env = environment({ adapter: false }), bar = mountBar(env), root = bar.element;
  const info = root.querySelector('.cbu-info'), panel = root.querySelector('.cbu-source-panel');
  const toggle = root.querySelector('.cbu-toggle');
  info.dispatchEvent({ type: 'click' }); toggle.dispatchEvent({ type: 'click' });
  assert.equal(root.dataset.mode, 'compact'); assert.equal(panel.hidden, true);
  assert.equal(info.getAttribute('aria-expanded'), 'false');
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(toggle.getAttribute('aria-label'), '展开用量条');
  bar.update(snapshot(env)); assert.equal(root.dataset.mode, 'compact');
  assert.equal(label(root, 'secondary'), '55%'); assert.equal(label(root, 'primary'), '12%');
  info.dispatchEvent({ type: 'click' }); assert.equal(panel.hidden, false);
  toggle.dispatchEvent({ type: 'click' });
  assert.equal(root.dataset.mode, 'expanded'); assert.equal(panel.hidden, true);
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(toggle.getAttribute('aria-label'), '收起用量条'); bar.destroy();
});

test('renderer disposal removes document listeners and pending focus and greeting timers', () => {
  const env = environment({ adapter: false }), bar = mountBar(env), root = bar.element;
  assert.equal(env.document.listenerCount(), 1);
  const pet = root.querySelector('.cbu-pet'); pet.dispatchEvent({ type: 'click' }); assert.equal(pet.classList.contains('is-nodding'), true);
  env.tick(650); assert.equal(pet.classList.contains('is-nodding'), false);
  pet.dispatchEvent({ type: 'click' }); root.dispatchEvent({ type: 'focusout' });
  assert.equal(env.timers.size, 2); bar.destroy(); bar.destroy();
  assert.equal(env.timers.size, 0); assert.equal(pet.listenerCount(), 0); assert.equal(root.listenerCount(), 0);
  assert.equal(env.document.listenerCount(), 0);
});

test('adapter mounts exactly one empty home composer and rejects unsupported routes or turns', () => {
  const env = environment(), api = env.install(); assert.equal(api.inspect().status, 'mounted');
  for (const url of ['app://-/thread/fixture', 'app://-/?thread=fixture', 'app://-/#fixture', 'https://example.invalid/']) {
    env.window.location.href = url; assert.equal(api.setAccountSnapshot(snapshot(env)), true); assert.equal(env.mounted(), null);
  }
  env.window.location.href = 'app://-/'; api.setAccountSnapshot(snapshot(env)); assert.ok(env.mounted());
  const turn = env.document.createElement('article'); turn.setAttribute('data-turn-key', 'fixture'); env.document.body.append(turn);
  api.setAccountSnapshot(snapshot(env)); assert.equal(env.mounted(), null); assert.equal(api.inspect().reason, 'conversation-present');
  api.dispose();
});

test('editor text nodes are refused structurally without reading their content', () => {
  const env = environment(), api = env.install();
  env.editor.childNodes[0].childNodes.push({ nodeType: 3, get textContent() { throw new Error('forbidden text access'); } });
  assert.equal(api.setAccountSnapshot(snapshot(env)), true); assert.equal(env.mounted(), null);
  assert.equal(api.inspect().reason, 'nonempty-editor'); api.dispose();
});

test('input immediately unmounts and IME composition stays unmounted until it ends', () => {
  const env = environment(), api = env.install();
  env.document.dispatchEvent({ type: 'beforeinput', target: env.editor }); assert.equal(env.mounted(), null);
  env.flush(); assert.ok(env.mounted());
  env.document.dispatchEvent({ type: 'compositionstart', target: env.editor }); assert.equal(env.mounted(), null);
  env.flush(); assert.equal(env.mounted(), null); assert.equal(api.inspect().reason, 'editor-composing');
  api.setAccountSnapshot(snapshot(env)); assert.equal(env.mounted(), null);
  env.document.dispatchEvent({ type: 'compositionend', target: env.editor }); env.flush(); assert.ok(env.mounted());
  api.dispose();
});

test('ambiguous composers, associated portals and external ownership never mount', () => {
  const env = environment(), api = env.install();
  env.portal.setAttribute('data-above-composer-conversation-id', 'fixture'); api.setAccountSnapshot(snapshot(env)); assert.equal(env.mounted(), null);
  env.portal.removeAttribute('data-above-composer-conversation-id');
  const duplicate = env.document.createElement('div'); duplicate.setAttribute('data-codex-composer-root', ''); duplicate.setAttribute('data-composer-placement', 'home'); env.document.body.append(duplicate);
  api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().reason, 'ambiguous-home'); duplicate.remove();
  const foreign = env.document.createElement('div'); foreign.setAttribute('data-codex-usage-bar', ''); env.document.body.append(foreign);
  api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().reason, 'ownership-conflict'); api.dispose(); assert.equal(env.mounted(), foreign);
});

test('adapter requires an explicit boolean mode', () => {
  const env = environment();
  for (const value of [undefined, null, 0, 1, 'false', 'true']) {
    assert.throws(() => env.window.CodexUsageBarAdapter.install({ css: '', homeOnly: value }), /invalid-options/);
  }
  const api = env.install(false); assert.equal(api.inspect().homeOnly, false); api.dispose();
});

test('persistent mode accepts canonical routes without conversation text or optional identifier access', () => {
  const env = environment();
  env.composer.setAttribute('data-composer-placement', 'thread');
  env.portal.setAttribute('data-above-composer-conversation-id', 'PRIVATE_IDENTIFIER_DO_NOT_READ');
  const readAttribute = env.portal.getAttribute.bind(env.portal);
  env.portal.getAttribute = name => {
    if (name === 'data-above-composer-conversation-id') throw new Error('forbidden identifier access');
    return readAttribute(name);
  };
  for (const name of ['textContent', 'innerHTML', 'innerText']) {
    Object.defineProperty(env.editor, name, { get() { throw new Error('forbidden editor access'); } });
  }
  const turn = env.document.createElement('article'); turn.setAttribute('data-turn-key', 'PRIVATE_TURN');
  Object.defineProperty(turn, 'textContent', { get() { throw new Error('forbidden turn access'); } });
  env.document.body.append(turn);
  const api = env.install(false);
  for (const url of ['app://-/thread/fixture', 'app://-/?thread=fixture', 'app://-/#fixture']) {
    env.window.location.href = url; assert.equal(api.setAccountSnapshot(snapshot(env)), true);
    assert.equal(api.inspect().status, 'mounted'); assert.equal(api.inspect().reason, 'composer-ready');
    assert.doesNotMatch(JSON.stringify(api.inspect()), /PRIVATE|fixture/);
  }
  api.dispose();
});

test('persistent mode refuses other origins, credentials and debug ports', () => {
  const env = environment(), api = env.install(false);
  for (const url of ['https://example.invalid/', 'app://example.invalid/', 'app://user@-/', 'app://-:1234/']) {
    env.window.location.href = url; api.setAccountSnapshot(snapshot(env));
    assert.equal(env.mounted(), null); assert.equal(api.inspect().reason, 'unsupported-route');
  }
  env.window.location.href = 'app://-/thread/fixture'; api.setAccountSnapshot(snapshot(env));
  assert.equal(api.inspect().status, 'mounted'); api.dispose();
});

test('persistent mode retains one bar during text input and IME composition', () => {
  const env = environment(), api = env.install(false), host = env.mounted();
  env.editor.childNodes[0].childNodes.push({ nodeType: 3, get textContent() { throw new Error('forbidden text access'); } });
  for (const type of ['beforeinput', 'input', 'compositionstart', 'input', 'compositionend']) {
    env.document.dispatchEvent({ type, target: env.editor });
    assert.equal(env.mounted(), host); env.flush();
    assert.equal(env.mounted(), host); assert.equal(api.inspect().status, 'mounted');
  }
  assert.equal(env.document.querySelectorAll('[data-codex-usage-bar]').length, 1); api.dispose();
});

test('persistent mode retains the bar when sending disables the same editor', () => {
  const env = environment(), api = env.install(false), host = env.mounted();
  env.editor.setAttribute('contenteditable', 'false'); env.editor.setAttribute('aria-disabled', 'true');
  env.mutate(); env.flush();
  assert.equal(api.inspect().status, 'mounted'); assert.equal(env.mounted(), host);
  env.editor.setAttribute('contenteditable', 'true'); env.editor.removeAttribute('aria-disabled');
  env.mutate(); env.flush(); assert.equal(env.mounted(), host); api.dispose();
});

test('persistent mode selects only the visible supported composer and its direct portal', () => {
  const env = environment();
  env.composer.setAttribute('data-composer-placement', 'thread');
  const hiddenHome = env.document.createElement('div'); hiddenHome.hidden = true;
  hiddenHome.setAttribute('data-codex-composer-root', ''); hiddenHome.setAttribute('data-composer-placement', 'home');
  const hiddenEditor = env.document.createElement('div'); hiddenEditor.className = 'ProseMirror';
  hiddenEditor.setAttribute('contenteditable', 'true'); hiddenEditor.hidden = true;
  const wrapper = env.document.createElement('div'), nested = env.document.createElement('div');
  nested.setAttribute('data-above-composer-portal', ''); nested.hidden = true; wrapper.append(nested);
  env.composer.append(hiddenEditor, wrapper); env.document.body.append(hiddenHome);
  const api = env.install(false); assert.equal(api.inspect().status, 'mounted');
  assert.equal(env.mounted().parentElement, env.portal);
  assert.match(env.mounted().style.cssText, /grid-column:1 \/ -1/);
  hiddenHome.hidden = false; api.setAccountSnapshot(snapshot(env));
  assert.equal(env.mounted(), null); assert.equal(api.inspect().reason, 'ambiguous-composer');
  hiddenHome.remove(); env.composer.setAttribute('data-composer-placement', 'unknown');
  api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().reason, 'unsupported-composer'); api.dispose();
});

test('persistent mode remounts safely after editor and whole composer replacement', () => {
  const env = environment(), api = env.install(false), firstHost = env.mounted();
  const editor = env.document.createElement('div'); editor.className = 'ProseMirror'; editor.setAttribute('contenteditable', 'true');
  env.editor.remove(); env.composer.append(editor); env.mutate(); env.flush();
  const secondHost = env.mounted(); assert.notEqual(secondHost, firstHost); assert.equal(firstHost.isConnected, false);
  const composer = env.document.createElement('div'); composer.setAttribute('data-codex-composer-root', '');
  composer.setAttribute('data-composer-placement', 'thread');
  const portal = env.document.createElement('div'); portal.setAttribute('data-above-composer-portal', '');
  const nextEditor = env.document.createElement('div'); nextEditor.className = 'ProseMirror'; nextEditor.setAttribute('contenteditable', 'false');
  composer.append(portal, nextEditor); env.composer.remove(); env.document.body.append(composer);
  env.mutate(); env.flush(); assert.equal(api.inspect().status, 'mounted');
  assert.equal(env.mounted().parentElement, portal); assert.equal(secondHost.isConnected, false);
  assert.equal(env.document.querySelectorAll('[data-codex-usage-bar]').length, 1); api.dispose();
  assert.equal(env.document.listenerCount(), 0); assert.equal(env.frames.size + env.timers.size + env.intervals.size, 0);
  assert.ok(env.observers.every(observer => observer.disconnected));
});

test('persistent mode refuses ambiguous visible editors and never deletes foreign owned bars', () => {
  const env = environment(), api = env.install(false);
  const extra = env.document.createElement('div'); extra.className = 'ProseMirror'; extra.setAttribute('contenteditable', 'false');
  env.composer.append(extra); api.setAccountSnapshot(snapshot(env));
  assert.equal(env.mounted(), null); assert.equal(api.inspect().reason, 'ambiguous-editor'); extra.remove();
  const foreign = env.document.createElement('div'); foreign.setAttribute('data-codex-usage-bar', ''); env.document.body.append(foreign);
  api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().reason, 'ownership-conflict');
  api.dispose(); assert.equal(env.mounted(), foreign);
});

test('persistent mode hides overlapping native portal controls and recovers when their geometry changes', () => {
  const env = environment(), native = env.document.createElement('button'); env.portal.append(native);
  const api = env.install(false); assert.equal(api.inspect().status, 'hidden');
  assert.equal(api.inspect().reason, 'native-portal-overlap'); assert.equal(env.mounted().style.display, 'none');
  native.box = { x: 100, y: 150, left: 100, top: 150, right: 700, bottom: 180, width: 600, height: 30 };
  api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().status, 'mounted'); api.dispose();
});

test('heartbeat expiry clears percentages and fresh updates recover', () => {
  const env = environment(), api = env.install(); api.setAccountSnapshot(snapshot(env));
  let bar = env.mounted().shadowRoot.querySelector('.cbu-bar'); assert.equal(label(bar, 'primary'), '12%');
  env.tick(15001); assert.equal(label(bar, 'primary'), '—'); assert.match(bar.textContent, /数据连接已断开/);
  api.setAccountSnapshot(snapshot(env)); assert.equal(label(bar, 'primary'), '12%'); api.dispose();
});

test('unsafe fixture geometry collapses the bar and can recover after resize', () => {
  const env = environment(), api = env.install();
  env.editor.box = { x: 100, y: 230, left: 100, top: 230, right: 700, bottom: 330, width: 600, height: 100 };
  api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().status, 'hidden');
  assert.equal(api.inspect().layout.hiddenReason, 'native-overlap'); assert.equal(env.mounted().style.display, 'none');
  delete env.editor.box; api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().status, 'mounted'); api.dispose();
});

test('input alignment compensates asymmetric portal padding and fractional zoom without drift', () => {
  for (const [scale, paddingLeft, paddingRight] of [[1, 13, 13], [1, 25, 7], [1.25, 13.2, 7.4]]) {
    const { env, shell } = alignmentFixture({ scale, paddingLeft, paddingRight });
    const originalPortalStyle = { ...env.portal.style }, originalShellStyle = { ...shell.style };
    const api = env.install(false);
    for (let iteration = 0; iteration < 8; iteration += 1) {
      api.setAccountSnapshot(snapshot(env));
      const layout = api.inspect().layout;
      assert.equal(api.inspect().status, 'mounted');
      assert.ok(Math.abs(layout.bar.left - shell.getBoundingClientRect().left) < 1e-8);
      assert.ok(Math.abs(layout.bar.right - shell.getBoundingClientRect().right) < 1e-8);
      assert.equal(layout.bar.width, layout.shell.width);
    }
    assert.deepEqual(env.portal.style, originalPortalStyle); assert.deepEqual(shell.style, originalShellStyle);
    assert.ok(env.observers.some(observer => observer.nodes.has(shell)));
    api.dispose();
  }
});

test('compact, expanded and details layouts retain input edges across resize', () => {
  const { env, geometry } = alignmentFixture();
  const api = env.install(false), host = env.mounted(), bar = host.shadowRoot.querySelector('.cbu-bar');
  const toggle = bar.querySelector('.cbu-toggle'), info = bar.querySelector('.cbu-info');
  for (const control of [toggle, toggle, info, info]) {
    geometry.shellWidth -= 21.25;
    control.dispatchEvent({ type: 'click' });
    env.document.dispatchEvent({ type: 'codex-usage-bar:layoutchange', target: host }); env.flush();
    const layout = api.inspect().layout;
    assert.equal(api.inspect().status, 'mounted');
    assert.ok(Math.abs(layout.bar.left - layout.shell.left) < 1e-8);
    assert.ok(Math.abs(layout.bar.right - layout.shell.right) < 1e-8);
  }
  api.dispose();
});

test('replacing the structural input branch remounts safely even when the editor is reused', () => {
  const fixture = alignmentFixture(), { env } = fixture;
  const api = env.install(false), previousHost = env.mounted(), previousShell = fixture.replaceShell();
  env.mutate(); env.flush();
  assert.equal(api.inspect().status, 'mounted'); assert.equal(previousHost.isConnected, false);
  assert.notEqual(env.mounted(), previousHost);
  assert.ok(env.observers.some(observer => observer.nodes.has(fixture.shell)));
  assert.ok(env.observers.every(observer => !observer.nodes.has(previousShell)));
  fixture.shell.style.display = 'contents'; env.mutate(); env.flush();
  assert.equal(api.inspect().reason, 'unsupported-input-surface'); assert.equal(env.mounted(), null);
  api.dispose();
});

test('unmeasurable neutral width recovers when only portal padding changes', () => {
  const { env, geometry } = alignmentFixture({ shellWidth: 600, paddingLeft: 300, paddingRight: 300 });
  const api = env.install(false);
  assert.equal(api.inspect().status, 'hidden'); assert.equal(api.inspect().reason, 'unmeasurable');
  const previousPortal = env.portal.getBoundingClientRect();
  geometry.paddingLeft = 30; geometry.paddingRight = 50;
  env.mutate(); env.flush();
  assert.deepEqual(env.portal.getBoundingClientRect(), previousPortal);
  assert.equal(api.inspect().status, 'mounted');
  assert.ok(Math.abs(api.inspect().layout.bar.left - geometry.left) < 1e-8);
  api.dispose();
});

test('alignment rejects a narrow input, ignored offsets and input-shell overlap then recovers', () => {
  const { env, geometry } = alignmentFixture({ shellWidth: 300 });
  const api = env.install(false);
  assert.equal(api.inspect().reason, 'content-overflow'); assert.equal(env.mounted().style.display, 'none');
  geometry.shellWidth = 600; geometry.ignoreOffset = true;
  api.setAccountSnapshot(snapshot(env)); assert.equal(api.inspect().reason, 'input-misaligned');
  geometry.ignoreOffset = false; geometry.shellTop = 265;
  api.setAccountSnapshot(snapshot(env));
  assert.equal(api.inspect().reason, 'native-overlap');
  assert.ok(api.inspect().layout.editor.top > geometry.hostTop + 90, 'editor alone would not detect shell overlap');
  geometry.shellTop = 450; api.setAccountSnapshot(snapshot(env));
  assert.equal(api.inspect().status, 'mounted'); api.dispose();
});

test('details that exceed the viewport close before the adapter hides the otherwise safe bar', () => {
  const env = environment(), api = env.install(false), host = env.mounted();
  const root = host.shadowRoot.querySelector('.cbu-bar');
  const info = root.querySelector('.cbu-info'), panel = root.querySelector('.cbu-source-panel');
  const box = (y, height) => ({ x: 100, y, left: 100, top: y, right: 700,
    bottom: y + height, width: 600, height });
  // This fixture models a bottom-anchored composer: a long details panel grows
  // above the viewport, but closing it restores the safe base bar geometry.
  host.getBoundingClientRect = () => box(panel.hidden ? 200 : -80, panel.hidden ? 100 : 380);
  root.getBoundingClientRect = () => box(panel.hidden ? 200 : -80, panel.hidden ? 90 : 380);
  info.dispatchEvent({ type: 'click' }); assert.equal(panel.hidden, false);
  // Real composed DOM events are retargeted to the shadow host. The synthetic
  // DOM does not bubble, so deliver that event at the adapter's document hook.
  env.document.dispatchEvent({ type: 'codex-usage-bar:layoutchange', target: host }); env.flush();
  assert.equal(panel.hidden, true); assert.equal(info.getAttribute('aria-expanded'), 'false');
  assert.equal(api.inspect().status, 'mounted'); assert.equal(api.inspect().layout.visible, true);
  assert.equal(api.inspect().layout.safety.safe, true);
  assert.equal(api.inspect().layout.naturalHeight, 90);
  assert.equal(host.style.display, 'block'); assert.equal(host.style.visibility, 'visible');
  // Closing must reset the renderer's actual open state, not only its CSS.
  info.dispatchEvent({ type: 'click' }); assert.equal(panel.hidden, false);
  api.dispose(); assert.equal(env.document.listenerCount(), 0);
});

test('closing unsafe details never bypasses geometry protection for an unsafe base bar', () => {
  const env = environment(), api = env.install(false), host = env.mounted();
  const root = host.shadowRoot.querySelector('.cbu-bar');
  const info = root.querySelector('.cbu-info'), panel = root.querySelector('.cbu-source-panel');
  env.editor.box = { x: 100, y: 230, left: 100, top: 230, right: 700,
    bottom: 330, width: 600, height: 100 };
  info.dispatchEvent({ type: 'click' }); assert.equal(panel.hidden, false);
  env.document.dispatchEvent({ type: 'codex-usage-bar:layoutchange', target: host }); env.flush();
  assert.equal(panel.hidden, true); assert.equal(info.getAttribute('aria-expanded'), 'false');
  assert.equal(api.inspect().status, 'hidden'); assert.equal(api.inspect().reason, 'native-overlap');
  assert.equal(host.style.display, 'none'); api.dispose();
});

test('renderer failures dispose all observers/timers/listeners and reject further updates', () => {
  const env = environment(), api = env.install();
  const original = env.window.getComputedStyle; env.window.getComputedStyle = () => { throw new Error('fixture failure'); };
  assert.equal(api.setAccountSnapshot(snapshot(env)), false); assert.equal(api.inspect().status, 'unsupported');
  assert.equal(env.mounted(), null); assert.equal(env.frames.size, 0); assert.equal(env.intervals.size, 0); assert.equal(env.timers.size, 0);
  assert.ok(env.observers.every(observer => observer.disconnected)); assert.equal(env.document.listenerCount(), 0); assert.equal(env.window.listenerCount(), 0);
  env.window.getComputedStyle = original; assert.equal(api.setAccountSnapshot(snapshot(env)), false); api.dispose();
});

test('invalid payloads cannot refresh heartbeat; disposal is complete and idempotent', () => {
  const env = environment(), api = env.install(); api.setAccountSnapshot(snapshot(env));
  for (const value of [null, [], false, 'fixture']) assert.equal(api.setAccountSnapshot(value), false);
  env.tick(15001); assert.match(env.mounted().shadowRoot.textContent, /数据连接已断开/);
  api.dispose(); api.dispose(); assert.equal(env.mounted(), null); assert.equal(api.setAccountSnapshot(snapshot(env)), false);
  assert.equal(env.document.listenerCount(), 0); assert.equal(env.window.listenerCount(), 0); assert.equal(env.media.listenerCount(), 0);
  assert.equal(env.frames.size + env.timers.size + env.intervals.size, 0);
});

test('official token usage metadata renders real zero and full cache ratios and copies only counts', () => {
  const { env, api, bar } = cacheEnvironment();
  assert.equal(label(bar(), 'cache'), '—'); assert.equal(api.inspect().cacheStatus, 'waiting');
  const message = tokenUsageEvent(800, 0);
  env.window.dispatchEvent(message);
  assert.equal(label(bar(), 'cache'), '0%'); assert.equal(api.inspect().cacheStatus, 'fresh');
  assert.match(cell(bar(), 'cache').textContent, /当前会话累计/);
  message.data.params.tokenUsage.total.cachedInputTokens = 800;
  env.tick(1); assert.equal(label(bar(), 'cache'), '0%');
  env.window.dispatchEvent(message); assert.equal(label(bar(), 'cache'), '100%');
  env.window.dispatchEvent(tokenUsageEvent(800, 300)); assert.equal(label(bar(), 'cache'), '37.5%');
  api.dispose();
});

test('cache requires the selected UUID and an unambiguous matching sidebar host', () => {
  const cases = [
    ['other event host', () => {}, event => { event.data.hostId = OTHER_CACHE_HOST; }],
    ['missing event host', () => {}, event => { delete event.data.hostId; }],
    ['other event thread', () => {}, event => { event.data.params.threadId = OTHER_CACHE_THREAD; }],
    ['missing sidebar host', ({ row }) => row.removeAttribute('data-app-action-sidebar-thread-host-id')],
    ['empty sidebar host', ({ row }) => row.setAttribute('data-app-action-sidebar-thread-host-id', '')],
    ['unsafe sidebar host', ({ row }) => row.setAttribute('data-app-action-sidebar-thread-host-id', CACHE_HOST + '\n')],
    ['ambiguous hosts', ({ env }) => cacheSidebarRow(env, CACHE_THREAD, OTHER_CACHE_HOST)],
    ['ambiguous missing host', ({ env }) => cacheSidebarRow(env, CACHE_THREAD, null)],
    ['other sidebar thread', ({ row }) => row.setAttribute('data-app-action-sidebar-thread-id', 'local:' + OTHER_CACHE_THREAD)],
    ['wrong sidebar kind', ({ row }) => row.setAttribute('data-app-action-sidebar-thread-kind', 'remote')],
    ['inactive page only', ({ env, row }) => {
      const oldPage = env.document.createElement('div'); oldPage.setAttribute('data-app-shell-active-page', 'false');
      env.document.body.append(oldPage); oldPage.append(row);
    }],
    ['non UUID portal', ({ env }) => env.portal.setAttribute('data-above-composer-conversation-id', 'chatgpt:' + CACHE_THREAD)],
    ['home composer', ({ env }) => env.composer.setAttribute('data-composer-placement', 'home')]
  ];
  for (const [name, change, changeEvent] of cases) {
    const fixture = cacheEnvironment(); change(fixture);
    const event = tokenUsageEvent(800, 300); changeEvent?.(event);
    fixture.env.window.dispatchEvent(event);
    assert.equal(label(fixture.bar(), 'cache'), '—', name);
    assert.notEqual(fixture.api.inspect().cacheStatus, 'fresh', name);
    fixture.api.dispose();
  }
});

test('inactive sidebar copies cannot override the current host and identical host rows remain unambiguous', () => {
  const { env, api, bar } = cacheEnvironment();
  const oldPage = env.document.createElement('div'); oldPage.setAttribute('data-app-shell-active-page', 'false');
  env.document.body.append(oldPage); oldPage.append(cacheSidebarRow(env, CACHE_THREAD, OTHER_CACHE_HOST));
  cacheSidebarRow(env);
  env.window.dispatchEvent(tokenUsageEvent(800, 300));
  assert.equal(label(bar(), 'cache'), '37.5%'); api.dispose();
});

test('cache accepts only official local message transport and filters unrelated payloads before reading params', () => {
  const { env, api, bar } = cacheEnvironment();
  let forbiddenReads = 0;
  const forbidden = () => { forbiddenReads++; throw new Error('must not inspect unrelated payload'); };
  for (const transport of [{ source: env.window, origin: '' }, { source: {}, origin: '' },
    { source: null, origin: 'https://example.invalid' }, { source: null, origin: 'null' }]) {
    const event = { type: 'message', ...transport };
    Object.defineProperty(event, 'data', { get: forbidden }); env.window.dispatchEvent(event);
  }
  for (const header of [{ type: 'other', method: 'thread/tokenUsage/updated' },
    { type: 'mcp-notification', method: 'item/agentMessage/delta' },
    { type: 'mcp-notification', method: 'thread/started' },
    { marker: 'codex-host-chunked-message-v1', kind: 'chunk' }]) {
    Object.defineProperty(header, 'params', { get: forbidden });
    env.window.dispatchEvent({ type: 'message', source: null, origin: '', data: header });
  }
  assert.equal(forbiddenReads, 0); assert.equal(label(bar(), 'cache'), '—');
  env.window.dispatchEvent(tokenUsageEvent(800, 300));
  assert.equal(label(bar(), 'cache'), '37.5%'); api.dispose();
});

test('cache never reads body, turn content, unused token fields or usage for another thread or host', () => {
  const { env, api, bar } = cacheEnvironment();
  let forbiddenReads = 0;
  const forbid = (object, key) => Object.defineProperty(object, key, { enumerable: true,
    get() { forbiddenReads++; throw new Error('forbidden content access'); } });
  for (const header of [{ threadId: OTHER_CACHE_THREAD }, { hostId: OTHER_CACHE_HOST }]) {
    const event = tokenUsageEvent(800, 300);
    if (header.threadId) event.data.params.threadId = header.threadId;
    if (header.hostId) event.data.hostId = header.hostId;
    forbid(event.data.params, 'tokenUsage'); env.window.dispatchEvent(event);
  }
  const event = tokenUsageEvent(800, 300), params = event.data.params, usage = params.tokenUsage;
  for (const field of ['body', 'turn', 'items']) forbid(params, field);
  for (const field of ['body', 'last', 'modelContextWindow']) forbid(usage, field);
  for (const field of ['body', 'outputTokens', 'totalTokens', 'reasoningOutputTokens', 'percent']) forbid(usage.total, field);
  env.window.dispatchEvent(event);
  assert.equal(forbiddenReads, 0); assert.equal(label(bar(), 'cache'), '37.5%'); api.dispose();
});

test('invalid and fabricated cache numbers clear a previous reading without breaking the bar', () => {
  const { env, api, bar } = cacheEnvironment();
  for (const [input, cached] of [[0, 0], [-1, 0], [100, -1], [100, 101], [1.5, 1],
    [100, 0.5], [Number.MAX_SAFE_INTEGER + 1, 1], [100, Number.MAX_SAFE_INTEGER + 1],
    [NaN, 0], [100, Infinity], ['100', 50], [100, '50'], [true, 0], [100, false],
    [null, 0], [100, null], [undefined, 95]]) {
    env.window.dispatchEvent(tokenUsageEvent(800, 300)); assert.equal(label(bar(), 'cache'), '37.5%');
    env.window.dispatchEvent(tokenUsageEvent(input, cached));
    assert.equal(label(bar(), 'cache'), '—'); assert.equal(api.inspect().cacheStatus, 'waiting');
    assert.equal(api.inspect().status, 'mounted');
  }
  const invented = tokenUsageEvent(undefined, undefined);
  invented.data.params.tokenUsage.total = { percent: 95, usedPercent: 95, hitRate: 0.95 };
  env.window.dispatchEvent(invented); assert.equal(label(bar(), 'cache'), '—');
  env.window.dispatchEvent(tokenUsageEvent(Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER));
  assert.equal(label(bar(), 'cache'), '100%'); api.dispose();
});

test('changing the visible thread clears cache and requires a new matching notification', () => {
  const { env, row, api, bar } = cacheEnvironment();
  env.window.dispatchEvent(tokenUsageEvent(800, 300)); assert.equal(label(bar(), 'cache'), '37.5%');
  env.portal.setAttribute('data-above-composer-conversation-id', OTHER_CACHE_THREAD);
  row.setAttribute('data-app-action-sidebar-thread-id', 'local:' + OTHER_CACHE_THREAD);
  env.mutate(); env.flush();
  assert.equal(label(bar(), 'cache'), '—'); assert.equal(api.inspect().cacheStatus, 'waiting');
  env.window.dispatchEvent(tokenUsageEvent(800, 800)); assert.equal(label(bar(), 'cache'), '—');
  const next = tokenUsageEvent(800, 400); next.data.params.threadId = OTHER_CACHE_THREAD;
  env.window.dispatchEvent(next); assert.equal(label(bar(), 'cache'), '50%');
  env.portal.setAttribute('data-above-composer-conversation-id', CACHE_THREAD);
  row.setAttribute('data-app-action-sidebar-thread-id', 'local:' + CACHE_THREAD);
  env.mutate(); env.flush(); assert.equal(label(bar(), 'cache'), '—'); api.dispose();
});

test('changing or losing the trusted host clears cached counts even when the thread ID stays the same', () => {
  const { env, row, api, bar } = cacheEnvironment();
  env.window.dispatchEvent(tokenUsageEvent(800, 300)); assert.equal(label(bar(), 'cache'), '37.5%');
  row.setAttribute('data-app-action-sidebar-thread-host-id', OTHER_CACHE_HOST);
  env.mutate(); env.flush(); assert.equal(label(bar(), 'cache'), '—');
  env.window.dispatchEvent(tokenUsageEvent(800, 800)); assert.equal(label(bar(), 'cache'), '—');
  env.window.dispatchEvent(tokenUsageEvent(800, 400, { hostId: OTHER_CACHE_HOST }));
  assert.equal(label(bar(), 'cache'), '50%');
  row.remove(); env.mutate(); env.flush(); assert.equal(label(bar(), 'cache'), '—');
  env.document.body.append(row); env.mutate(); env.flush(); assert.equal(label(bar(), 'cache'), '—');
  api.dispose();
});

test('losing a visible composer candidate clears usage before the same composer becomes visible again', () => {
  const { env, api, bar } = cacheEnvironment();
  env.window.dispatchEvent(tokenUsageEvent(800, 300)); assert.equal(label(bar(), 'cache'), '37.5%');
  env.composer.hidden = true; env.mutate(); env.flush();
  assert.equal(api.inspect().status, 'unmounted'); assert.equal(env.mounted(), null);
  assert.equal(api.inspect().cacheStatus, 'unavailable');
  env.composer.hidden = false; env.mutate(); env.flush();
  assert.equal(api.inspect().status, 'mounted'); assert.equal(label(bar(), 'cache'), '—');
  assert.equal(api.inspect().cacheStatus, 'waiting'); api.dispose();
});

test('cache ages after two minutes while account heartbeats remain healthy', () => {
  const { env, api, bar } = cacheEnvironment();
  env.window.dispatchEvent(tokenUsageEvent(800, 300));
  for (let step = 0; step < 12; step++) {
    env.tick(10000); api.setAccountSnapshot(snapshot(env));
    assert.equal(api.inspect().cacheStatus, step === 11 ? 'stale' : 'fresh');
  }
  assert.equal(label(bar(), 'cache'), '37.5%'); assert.match(cell(bar(), 'cache').textContent, /上次统计/);
  assert.equal(cell(bar(), 'cache').dataset.stale, 'true');
  env.window.dispatchEvent(tokenUsageEvent(1000, 400));
  assert.equal(label(bar(), 'cache'), '40%'); assert.equal(api.inspect().cacheStatus, 'fresh');
  api.dispose();
});

test('manager heartbeat loss hides cache and recovery preserves the original observation age', () => {
  const { env, api, bar } = cacheEnvironment();
  env.window.dispatchEvent(tokenUsageEvent(800, 300));
  env.tick(15001);
  assert.equal(api.inspect().cacheStatus, 'disconnected'); assert.equal(label(bar(), 'cache'), '—');
  env.tick(105000); api.setAccountSnapshot(snapshot(env));
  assert.equal(api.inspect().cacheStatus, 'stale'); assert.equal(label(bar(), 'cache'), '37.5%');
  assert.match(cell(bar(), 'cache').textContent, /上次统计/); api.dispose();
});

test('account snapshots cannot forge usage statistics or cause upstream cache fields to be read', () => {
  const { env, api, bar } = cacheEnvironment();
  let forbiddenReads = 0;
  const account = snapshot(env);
  Object.defineProperty(account, 'cache', { get() { forbiddenReads++; throw new Error('upstream cache is not trusted'); } });
  assert.equal(api.setAccountSnapshot(account), true); assert.equal(forbiddenReads, 0);
  assert.equal(label(bar(), 'cache'), '—');
  const forged = snapshot(env, { cache: { status: 'fresh', inputTokens: 100, cachedInputTokens: 95, percent: 95 } });
  api.setAccountSnapshot(forged); assert.equal(label(bar(), 'cache'), '—');
  env.window.dispatchEvent(tokenUsageEvent(800, 300));
  api.setAccountSnapshot(forged); assert.equal(label(bar(), 'cache'), '37.5%'); api.dispose();
});

test('inspection exposes no cache identity or counts and disposal releases the passive listener', () => {
  const { env, api } = cacheEnvironment();
  assert.equal(env.window.events.get('message').size, 1);
  env.window.dispatchEvent(tokenUsageEvent(987654321, 123456789));
  const inspection = api.inspect(), serialized = JSON.stringify(inspection);
  assert.equal(inspection.cacheStatus, 'fresh');
  for (const privateValue of [CACHE_THREAD, CACHE_HOST, '987654321', '123456789',
    'inputTokens', 'cachedInputTokens', 'threadId', 'hostId']) assert.equal(serialized.includes(privateValue), false);
  assert.deepEqual(Object.keys(inspection).sort(), ['cacheStatus', 'homeOnly', 'layout', 'reason', 'status']);
  api.dispose(); assert.equal(env.window.events.get('message').size, 0); assert.equal(env.window.listenerCount(), 0);
  env.window.dispatchEvent(tokenUsageEvent(100, 100)); assert.equal(env.mounted(), null);
  assert.equal(env.frames.size + env.timers.size + env.intervals.size, 0);
  assert.equal(api.inspect().cacheStatus, 'unavailable');
  const isolated = environment(), isolatedApi = isolated.install();
  assert.equal(isolated.window.events.get('message')?.size ?? 0, 0); isolatedApi.dispose();
});
