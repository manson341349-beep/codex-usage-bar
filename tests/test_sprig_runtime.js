/* SPDX-License-Identifier: MIT
 * Offline runtime lifecycle tests with real motion logic and a fake renderer.
 * These assertions cover draw scheduling, not WebGL pixels or GPU performance.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const SOURCE = path.resolve(__dirname, '../web/sprig-source');

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener, { signal } = {}) {
    if (signal?.aborted) return;
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
    signal?.addEventListener('abort', () => this.listeners.get(type).delete(listener), { once: true });
  }
  emit(type, values = {}) {
    for (const listener of [...(this.listeners.get(type) || [])]) listener({ type, target: this, ...values });
  }
}

function fixture({ reduced = true } = {}) {
  const doc = new Events(), win = new Events(), media = new Events();
  const scheduled = new Map(), observers = { resize: [], intersection: [], mutation: [] };
  const colors = { background: 'rgb(255, 255, 255)', accent: '#8bbdab' };
  let sequence = 0;
  class Element extends Events {
    constructor(tag) {
      super(); this.tagName = tag; this.ownerDocument = doc; this.style = {};
      this.children = []; this.parentElement = null; this.attributes = new Map();
    }
    append(node) { node.remove(); this.children.push(node); node.parentElement = this; }
    remove() {
      if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this);
      this.parentElement = null;
    }
    setAttribute(name, value) { this.attributes.set(name, value); }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    querySelector(selector) {
      return this.children.find(node => '.' + node.className === selector) || null;
    }
    get isConnected() { return this === doc.documentElement || Boolean(this.parentElement?.isConnected); }
    getBoundingClientRect() { return { left: 0, top: 0, width: 56, height: 56 }; }
  }
  doc.defaultView = win; doc.hidden = false; doc.hasFocus = () => true;
  doc.createElement = tag => new Element(tag);
  doc.documentElement = new Element('html');
  const outside = new Element('div'), root = new Element('div'), button = new Element('button');
  const fallback = new Element('svg'); fallback.className = 'cbu-sprig-fallback';
  doc.documentElement.append(outside); outside.append(root); root.append(button); button.append(fallback);
  media.matches = reduced; win.matchMedia = () => media; win.devicePixelRatio = 1;
  win.requestAnimationFrame = callback => { const id = ++sequence; scheduled.set(id, callback); return id; };
  win.cancelAnimationFrame = id => scheduled.delete(id);
  win.getComputedStyle = node => ({
    display: node.style.display || 'block', visibility: node.style.visibility || 'visible',
    backgroundColor: node === root ? colors.background : 'transparent', color: node.style.color,
    getPropertyValue: name => node === root && name === '--cbu-accent' ? colors.accent : ''
  });
  const observer = kind => class {
    constructor(callback) { this.callback = callback; this.active = true; observers[kind].push(this); }
    observe() {}
    disconnect() { this.active = false; }
  };
  class Color {
    constructor(value) {
      const rgb = typeof value === 'number' ? [value >> 16, value >> 8 & 255, value & 255] :
        value.startsWith('#') ? [1, 3, 5].map(i => parseInt(value.slice(i, i + 2), 16)) :
          value.match(/[\d.]+/g).map(Number);
      [this.r, this.g, this.b] = rgb.map(n => n / 255);
    }
    lerp(other, amount) { for (const key of ['r', 'g', 'b']) this[key] += (other[key] - this[key]) * amount; return this; }
    copy(other) { Object.assign(this, other); return this; }
  }
  class Renderer {
    constructor() { this.shadowMap = {}; this.info = { render: { calls: 1, triangles: 12 } }; }
    setPixelRatio(value) { this.ratio = value; }
    getPixelRatio() { return this.ratio; }
    setSize() {}
    setClearColor() {}
    getContext() { return { getExtension: () => null, isContextLost: () => false }; }
    render() {}
    dispose() {}
    forceContextLoss() {}
  }
  const context = vm.createContext({
    window: win, AbortController, performance: { now: () => 1 },
    ResizeObserver: observer('resize'), IntersectionObserver: observer('intersection'),
    MutationObserver: observer('mutation'),
    THREE: { WebGLRenderer: Renderer, Color, OrthographicCamera: class {
      constructor() { this.position = { set() {} }; }
      lookAt() {}
    } },
    makeStage: () => ({
      scene: { traverse() {} }, env: { dispose() {} },
      model: { setDetail() {}, apply() {}, materials: { mint: { color: new Color(0) } } },
      contact: { position: {}, material: {}, scale: { setScalar() {} } }
    })
  });
  const motion = fs.readFileSync(path.join(SOURCE, 'motion.js'), 'utf8').replace(/^export /gm, '');
  const runtime = fs.readFileSync(path.join(SOURCE, 'runtime.js'), 'utf8').replace(/^import .*;\s*$/gm, '');
  vm.runInContext(motion + '\n' + runtime, context, { filename: 'sprig-runtime-fixture.js' });
  const companion = win.CodexUsageBarSprig.mount(button, { root });
  return {
    companion, button, doc, win, media, colors, scheduled,
    frames: () => companion.inspect().frames,
    notify(kind, records) {
      const defaults = kind === 'intersection' ? [{ isIntersecting: true }] : [{ target: outside }];
      for (const entry of observers[kind]) if (entry.active) entry.callback(records || defaults);
    },
    tick(time) {
      for (const [id, callback] of [...scheduled]) { scheduled.delete(id); callback(time); }
    }
  };
}

test('reduced motion draws once and stays quiet through unchanged lifecycle notifications', t => {
  const f = fixture(); t.after(() => f.companion.destroy());
  assert.equal(f.frames(), 0);
  f.companion.setVisible(true);
  assert.equal(f.frames(), 1);
  for (let i = 0; i < 20; i++) {
    f.companion.setVisible(true);
    f.companion.setTheme('light');
    f.notify('mutation'); // Adapter geometry writes outside the companion root.
    f.notify('resize');
    f.notify('intersection');
    f.button.emit('pointermove', { clientX: i, clientY: 12 });
  }
  f.button.emit('pointerleave');
  assert.equal(f.frames(), 1, 'unchanged pixels must not trigger another render');
  assert.equal(f.scheduled.size, 0, 'static mode must not schedule an animation loop');
  assert.equal(f.companion.inspect().initCount, 1);
});

test('actual theme and effective pixel ratio changes invalidate one static draw', t => {
  const f = fixture(); t.after(() => f.companion.destroy());
  f.companion.setVisible(true);
  f.colors.accent = '#bb9988'; f.notify('mutation');
  assert.equal(f.frames(), 2);
  f.companion.setTheme('light'); f.notify('mutation');
  assert.equal(f.frames(), 2);
  f.colors.background = 'rgb(12, 12, 12)'; f.notify('mutation');
  assert.equal(f.frames(), 3);
  f.win.devicePixelRatio = 2; f.notify('resize');
  assert.equal(f.frames(), 4);
  assert.equal(f.companion.inspect().pixelRatio, 2);
  f.win.devicePixelRatio = 3; f.notify('resize');
  assert.equal(f.frames(), 4, 'DPR above the cap does not change rendered resolution');
  assert.equal(f.scheduled.size, 0);
});

test('static click response and hide/show repaint survive draw deduplication', t => {
  const f = fixture(); t.after(() => f.companion.destroy());
  f.companion.setVisible(true);
  f.button.emit('click');
  assert.equal(f.frames(), 2);
  assert.equal(f.companion.inspect().state, 'play');
  f.button.emit('click');
  assert.equal(f.frames(), 2, 'repeated play requests do not restart a held response');
  f.companion.setVisible(false); f.companion.setTheme('dark');
  assert.equal(f.frames(), 2);
  f.companion.setVisible(true);
  assert.equal(f.frames(), 3);
  f.doc.hidden = true; f.doc.emit('visibilitychange');
  f.colors.accent = '#aabbcc'; f.notify('mutation');
  assert.equal(f.frames(), 3, 'hidden changes remain pending');
  f.doc.hidden = false; f.doc.emit('visibilitychange');
  assert.equal(f.frames(), 4);
  f.notify('intersection', [{ isIntersecting: false }]);
  f.notify('intersection', [{ isIntersecting: true }]);
  assert.equal(f.frames(), 5);
  assert.equal(f.scheduled.size, 0);
});

test('normal motion keeps one animation loop and reduced motion cancels it', t => {
  const f = fixture({ reduced: false }); t.after(() => f.companion.destroy());
  f.companion.setVisible(true);
  f.companion.setVisible(true); f.notify('resize'); f.notify('intersection');
  assert.equal(f.scheduled.size, 1);
  f.tick(16); f.tick(32); f.tick(48);
  assert.equal(f.frames(), 3);
  assert.equal(f.scheduled.size, 1);
  f.media.matches = true; f.media.emit('change', { matches: true });
  assert.equal(f.frames(), 4);
  assert.equal(f.scheduled.size, 0);
  f.tick(64); f.notify('mutation'); f.companion.setVisible(true);
  assert.equal(f.frames(), 4);
  f.media.matches = false; f.media.emit('change', { matches: false });
  assert.equal(f.scheduled.size, 1);
  f.tick(80);
  assert.equal(f.frames(), 5);
  f.companion.setVisible(false);
  assert.equal(f.scheduled.size, 0);
  f.tick(96);
  assert.equal(f.frames(), 5);
});
