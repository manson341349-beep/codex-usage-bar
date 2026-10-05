/* SPDX-License-Identifier: MIT
 * Offline localization contract tests. The DOM and companion are stand-ins;
 * these tests make no browser-layout, canvas-pixel, or WebGL claims.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const NOW = Date.parse('2026-10-05T12:00:00Z');
const OFFICIAL = 'Codex 订阅额度 · 官方 account/rateLimits/read';
const OFFICIAL_EN = 'Codex subscription quota · official account/rateLimits/read';
const SOURCE = fs.readFileSync(path.resolve(__dirname, '../web/bar.js'), 'utf8');

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
  }
}
function nodes(root) { return [root, ...root.children.flatMap(nodes)]; }
function byClass(root, name) { return nodes(root).find(node => node.className === name); }
function fixture({ docLang, navigator = {} } = {}) {
  let now = NOW, sequence = 0, inits = 0, destroys = 0;
  const document = new Events(), timers = new Map(), dateLocales = [];
  class Element extends Events {
    constructor(tag) {
      super(); this.nodeType = 1; this.tagName = tag; this.ownerDocument = document;
      this.children = []; this.dataset = {}; this.style = {}; this.attributes = new Map(); this._text = '';
    }
    append(...children) { for (const child of children) this.appendChild(child); }
    appendChild(child) { child.remove(); child.parentElement = this; this.children.push(child); return child; }
    remove() {
      if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this);
      this.parentElement = null;
    }
    set textContent(value) { this.children.forEach(child => { child.parentElement = null; }); this.children = []; this._text = String(value); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set innerHTML(value) { this.textContent = ''; this.html = value; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    contains(node) { return nodes(this).includes(node); }
    getRootNode() { return document; }
    focus() { document.activeElement = this; }
  }
  document.createElement = tag => new Element(tag);
  document.documentElement = document.createElement('html');
  if (docLang !== undefined) document.documentElement.setAttribute('lang', docLang);
  const container = document.createElement('div'); document.documentElement.append(container);
  const window = {
    navigator, setTimeout(fn, delay) { const id = ++sequence; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    CustomEvent: class { constructor(type, values) { this.type = type; Object.assign(this, values); } },
    CodexUsageBarSprig: { mount(button) {
      inits++; button.append(document.createElement('canvas'));
      return { setTheme() {}, setVisible() {}, destroy() { destroys++; }, inspect() { return { initCount: inits, mode: 'fallback' }; } };
    } }
  };
  document.defaultView = window;
  class Clock extends Date {
    static now() { return now; }
    toLocaleString(locale, options) { dateLocales.push(['date', locale]); return super.toLocaleString(locale, options); }
    toLocaleTimeString(locale, options) { dateLocales.push(['time', locale]); return super.toLocaleTimeString(locale, options); }
  }
  vm.runInContext(SOURCE, vm.createContext({ window, Date: Clock }), { filename: 'bar.js' });
  return {
    document, window, timers, dateLocales, inits: () => inits, destroys: () => destroys,
    mount: options => window.CodexUsageBar.mount(container, options),
    tick(ms) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } }
  };
}
function snapshot() {
  return { status: 'fresh', stale: false, sourceLabel: OFFICIAL, updatedAt: '2026-10-05T11:59:00Z',
    limits: { primary: { usedPercent: 25, windowMinutes: 300, resetsAt: NOW / 1000 + 7200, status: 'fresh' },
      secondary: { usedPercent: 40, windowMinutes: 10080, resetsAt: NOW / 1000 + 93600, status: 'fresh' } },
    cache: { status: 'fresh', inputTokens: 1000, cachedInputTokens: 750, observedAt: NOW - 1000 } };
}
function field(bar, key) {
  const index = ['source', 'updated', 'quota', 'cache', 'working'].indexOf(key);
  return nodes(bar.element).filter(node => node.tagName === 'dd')[index];
}
function cell(bar, key) { return nodes(bar.element).find(node => node.dataset.metric === key); }
function detail(bar, key = 'primary') { return byClass(cell(bar, key), 'cbu-detail').textContent; }
function exposedText(bar) {
  return nodes(bar.element).map(node => [node._text, node.title || '', node.getAttribute('aria-label') || ''].join(' ')).join('\n');
}

test('locale defaults and explicit overrides follow document and navigator precedence', () => {
  const cases = [
    [{ docLang: 'zh-Hant', navigator: { language: 'en-US' } }, {}, 'zh-CN'],
    [{ docLang: 'en-GB', navigator: { language: 'zh-CN' } }, {}, 'en'],
    [{ docLang: 'zh-CN' }, { locale: 'fr-FR' }, 'en'],
    [{ docLang: 'en' }, { locale: ' ZH-tw ' }, 'zh-CN'],
    [{ navigator: { language: 'zh-SG' } }, {}, 'zh-CN'],
    [{ navigator: { language: ' ', languages: ['', 'zh-HK'] } }, {}, 'zh-CN'],
    [{ navigator: { language: 'fr', languages: ['zh-CN'] } }, {}, 'en'],
    [{ docLang: ' ' }, {}, 'en'], [{}, { locale: null }, 'en'], [{}, {}, 'en']
  ];
  for (const [environment, options, expected] of cases) {
    const f = fixture(environment), bar = f.mount(options);
    assert.equal(bar.element.getAttribute('lang'), expected);
    assert.equal(bar.element.getAttribute('dir'), 'ltr');
    bar.destroy();
  }
  const f = fixture({ docLang: 'en' }), bar = f.mount({ locale: 'zh' }), other = f.mount({ locale: 'zh' });
  bar.setLocale();
  assert.equal(bar.element.getAttribute('lang'), 'en');
  assert.equal(other.element.getAttribute('lang'), 'zh-CN', 'instances retain independent locales');
  f.document.documentElement.setAttribute('lang', 'zh-Hant'); bar.setLocale();
  assert.equal(bar.element.getAttribute('lang'), 'zh-CN');
  bar.destroy(); other.destroy();
});

test('English visible labels, full accessible names, tooltips, dates and official source are translated', () => {
  const f = fixture(), bar = f.mount({ locale: 'en' }); bar.update(snapshot());
  assert.deepEqual(['secondary', 'primary', 'cache'].map(key => byClass(cell(bar, key), 'cbu-label').textContent),
    ['Weekly left', '5h used', 'Cache hit']);
  assert.deepEqual(['secondary', 'primary', 'cache'].map(key => byClass(cell(bar, key), 'cbu-compact-label').textContent),
    ['Wk', '5h', 'Cache']);
  for (const [key, label] of [['secondary', 'Weekly subscription remaining'], ['primary', '5-hour usage'], ['cache', 'Cache hit rate']]) {
    assert.ok(cell(bar, key).getAttribute('aria-label').startsWith(label + ', '));
    assert.equal(byClass(cell(bar, key), 'cbu-detail').title, detail(bar, key));
  }
  assert.equal(byClass(bar.element, 'cbu-pet').getAttribute('aria-label'), 'Say hello to Sprig');
  assert.equal(byClass(bar.element, 'cbu-toggle').title, 'Collapse usage bar');
  assert.equal(byClass(bar.element, 'cbu-info').title, 'View data source and status');
  assert.equal(byClass(bar.element, 'cbu-source-panel').getAttribute('aria-label'), 'Data source and status');
  assert.equal(field(bar, 'source').textContent, OFFICIAL_EN);
  assert.match(field(bar, 'quota').textContent, /100% − used/);
  assert.match(field(bar, 'cache').textContent, /total cached input ÷ total input/);
  assert.deepEqual(f.dateLocales.slice(-2), [['date', 'en-US'], ['time', 'en-US']]);
  assert.equal(field(bar, 'updated').textContent, new Date(snapshot().updatedAt).toLocaleString('en-US', { hour12: false }));
  assert.ok(field(bar, 'cache').textContent.includes(new Date(NOW - 1000).toLocaleTimeString('en-US', { hour12: false })));
  assert.doesNotMatch(exposedText(bar), /\p{Script=Han}/u);
  bar.setLocale('zh');
  assert.equal(field(bar, 'source').textContent, OFFICIAL);
  assert.deepEqual(f.dateLocales.slice(-2), [['date', 'zh-CN'], ['time', 'zh-CN']]);
  assert.equal(field(bar, 'updated').textContent, new Date(snapshot().updatedAt).toLocaleString('zh-CN', { hour12: false }));
  assert.equal(byClass(bar.element, 'cbu-toggle').title, '折叠额度条');
  bar.destroy();
});

test('all account statuses have bilingual output including unknown and backend failures', () => {
  const expected = [
    ['fresh', 'Data updated', '数据已更新'], ['partial', 'Some windows available', '部分窗口可用'],
    ['stale', 'Saved stale data', '保留的旧数据'], ['waiting', 'Waiting for account data', '等待账户数据'],
    ['unavailable', 'Quota data unavailable', '配额数据不可用'], ['disconnected', 'Data connection lost', '数据连接已断开'],
    ['expired', 'Data expired', '数据已过期'], ['reset_pending', 'Waiting for data after reset', '等待重置后数据'],
    ['unknown', 'Unknown status', '状态未知'], ['missing', 'Not provided by this source', '当前来源未提供'],
    ['error', 'Update failed', '更新失败'], ['auth_error', 'Account verification failed', '账户验证失败'],
    ['identity_unknown', 'Account identity unconfirmed', '账户身份未确认'],
    ['account_changed', 'Account changed; waiting for new data', '账户已切换，等待新数据'],
    ['api_key_unsupported', 'Quota is not supported by this account source', '当前账户来源不支持配额'],
    ['cli_missing', 'Codex CLI unavailable', 'Codex CLI 不可用'], ['launch_failed', 'Could not start quota reader', '额度读取启动失败'],
    ['timeout', 'Quota request timed out', '额度读取超时'], ['protocol_error', 'Invalid quota response', '额度响应无效'],
    ['read_failed', 'Could not read quota', '额度读取失败'], ['unexpected', 'Unknown status', '状态未知'],
    ['constructor', 'Unknown status', '状态未知'], ['__proto__', 'Unknown status', '状态未知']
  ];
  const f = fixture(), bar = f.mount();
  for (const locale of ['en', 'zh-CN']) {
    bar.setLocale(locale);
    for (const [status, english, chinese] of expected) {
      const data = snapshot(); data.status = status; bar.update(data);
      assert.ok(field(bar, 'quota').textContent.startsWith(locale === 'en' ? english : chinese), status + '/' + locale);
      if (locale === 'en') assert.doesNotMatch(exposedText(bar), /\p{Script=Han}/u);
    }
  }
  bar.destroy();
});

test('reset durations, invalid windows and cache states use the active language', () => {
  const f = fixture(), bar = f.mount();
  const cases = [[60, 'Resets in 1m', '1分钟后重置'], [7200, 'Resets in 2h', '2小时后重置'],
    [86400, 'Resets in 1d', '1天后重置'], [93600, 'Resets in 1d 2h', '1天2小时后重置'],
    [null, 'Reset time not provided', '重置时间未提供'], [-1, 'Invalid reset time', '重置时间无效'],
    [0, 'Waiting for data after reset', '等待重置后数据']];
  for (const locale of ['en', 'zh-CN']) {
    bar.setLocale(locale);
    for (const [seconds, english, chinese] of cases) {
      const data = snapshot(); data.limits.primary.resetsAt = seconds === null ? null : seconds < 0 ? seconds : NOW / 1000 + seconds;
      bar.update(data); assert.equal(detail(bar), locale === 'en' ? english : chinese);
    }
    for (const [patch, english, chinese] of [
      [{ windowMinutes: 1 }, 'Window data unavailable', '窗口数据不可用'],
      [{ usedPercent: 101 }, 'Usage data unavailable', '用量数据不可用'],
      [{ status: 'stale' }, 'Stale data · Resets in 2h', '旧数据 · 2小时后重置']
    ]) {
      const data = snapshot(); Object.assign(data.limits.primary, patch); bar.update(data);
      assert.equal(detail(bar), locale === 'en' ? english : chinese);
    }
    for (const [status, english, chinese] of [
      ['fresh', 'This session total', '当前会话累计'], ['stale', 'Previous statistics · This session total', '上次统计 · 当前会话累计'],
      ['waiting', 'Waiting for session statistics', '等待本会话统计'],
      ['unavailable', 'Not provided by this source', '当前来源未提供'], ['disconnected', 'Data connection lost', '数据连接已断开']
    ]) {
      const data = snapshot(); data.cache.status = status; bar.update(data);
      assert.equal(detail(bar, 'cache'), locale === 'en' ? english : chinese);
    }
  }
  bar.destroy();
});

test('language changes preserve DOM, companion, open compact state, values and reset deadline', () => {
  const f = fixture(), bar = f.mount({ locale: 'en' }); bar.update(snapshot());
  const root = bar.element, pet = byClass(root, 'cbu-pet'), canvas = nodes(pet).find(node => node.tagName === 'canvas');
  const panel = byClass(root, 'cbu-source-panel'), toggle = byClass(root, 'cbu-toggle'), info = byClass(root, 'cbu-info');
  toggle.dispatchEvent({ type: 'click' }); info.dispatchEvent({ type: 'click' });
  const ids = [panel.id, info.getAttribute('aria-controls')], deadline = [...f.timers.values()][0].at;
  const values = () => ['secondary', 'primary', 'cache'].map(key => [byClass(cell(bar, key), 'cbu-value').textContent, byClass(cell(bar, key), 'cbu-fill').style.width]);
  const before = values(); let changes = 0;
  root.addEventListener('codex-usage-bar:layoutchange', () => changes++);
  bar.setLocale('zh-TW'); bar.setLocale(' ZH-cn ');
  assert.equal(changes, 1, 'equivalent normalized locale is a no-op');
  assert.equal(bar.element, root); assert.equal(byClass(root, 'cbu-pet'), pet);
  assert.equal(nodes(pet).find(node => node.tagName === 'canvas'), canvas);
  assert.equal(byClass(root, 'cbu-source-panel'), panel);
  assert.deepEqual([panel.id, info.getAttribute('aria-controls')], ids);
  assert.equal(root.dataset.mode, 'compact'); assert.equal(panel.hidden, false);
  assert.equal(toggle.getAttribute('aria-label'), '展开用量条');
  assert.deepEqual(values(), before); assert.equal(f.inits(), 1); assert.equal(f.destroys(), 0);
  assert.equal(f.timers.size, 1); assert.equal([...f.timers.values()][0].at, deadline);
  bar.setLocale('en'); assert.equal(changes, 2); assert.equal(toggle.title, 'Expand usage bar');
  f.tick(7200001);
  assert.equal(detail(bar), 'Waiting for data after reset');
  assert.equal(byClass(cell(bar, 'primary'), 'cbu-value').textContent, '—');
  assert.equal(f.timers.size, 1, 'only the later weekly deadline remains');
  bar.destroy(); assert.equal(f.timers.size, 0); assert.equal(f.destroys(), 1);
  bar.setLocale('zh'); assert.equal(changes, 2, 'disposed instance ignores locale updates');
});

test('custom source text is preserved while invalid dates use localized fallbacks', () => {
  const f = fixture(), bar = f.mount();
  for (const locale of ['en', 'zh-CN']) {
    bar.setLocale(locale);
    const data = snapshot(); data.sourceLabel = '自定义 <img src=x onerror=alert(1)> source';
    data.updatedAt = 'not-a-date'; data.cache.observedAt = Number.MAX_SAFE_INTEGER; bar.update(data);
    assert.equal(field(bar, 'source').textContent, data.sourceLabel);
    assert.equal(field(bar, 'source').children.length, 0, 'source text cannot create markup');
    assert.equal(field(bar, 'updated').textContent, locale === 'en' ? 'Update time not provided' : '更新时间未提供');
    assert.doesNotMatch(field(bar, 'cache').textContent, /Invalid Date/);
    assert.match(field(bar, 'cache').textContent, locale === 'en' ? /Waiting for new statistics/ : /等待新的真实统计/);
    data.sourceLabel = OFFICIAL_EN; bar.update(data);
    assert.equal(field(bar, 'source').textContent, locale === 'en' ? OFFICIAL_EN : OFFICIAL);
  }
  bar.destroy();
});
