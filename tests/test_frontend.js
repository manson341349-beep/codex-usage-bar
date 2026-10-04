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
    constructor(fn) { this.fn = fn; this.disconnected = false; observers.push(this); }
    observe() {} unobserve() {} disconnect() { this.disconnected = true; }
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
  assert.equal(label(root, 'secondary'), '45%'); assert.equal(label(root, 'primary'), '12%');
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

test('persistent mode accepts canonical app routes and ignores conversation text and identifier values', () => {
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
