/* MIT License — Copyright (c) 2026 codex-usage-bar contributors */
(function (global) {
  'use strict';

  var nextId = 0;
  var MAX_TIMEOUT = 2147483647;
  var BLOCKED = ['waiting', 'unavailable', 'disconnected', 'expired', 'unknown', 'missing', 'identity_unknown', 'account_changed', 'api_key_unsupported'];
  var STATUS_LABELS = {
    fresh: '数据已更新', partial: '部分窗口可用', stale: '保留的旧数据',
    waiting: '等待账户数据', unavailable: '配额数据不可用', disconnected: '数据连接已断开',
    expired: '数据已过期', reset_pending: '等待重置后数据', unknown: '状态未知',
    missing: '当前来源未提供', error: '更新失败', auth_error: '账户验证失败',
    identity_unknown: '账户身份未确认', account_changed: '账户已切换，等待新数据',
    api_key_unsupported: '当前账户来源不支持配额'
  };
  var robot = '<svg viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">' +
    '<ellipse cx="32" cy="58" rx="17" ry="3" fill="currentColor" opacity=".08"/>' +
    '<g class="cbu-robot">' +
    '<path d="M29 15V10c0-2 1-3 3-3" stroke="var(--cbu-ink)" stroke-width="2.5" stroke-linecap="round"/>' +
    '<circle cx="34" cy="7" r="3.3" fill="var(--cbu-mint)" stroke="var(--cbu-ink)" stroke-width="1.8"/>' +
    '<path d="M21 48v5c0 2-2 3-4 3h-2" stroke="var(--cbu-ink)" stroke-width="4" stroke-linecap="round"/>' +
    '<path d="M42 48v5c0 2 2 3 4 3h2" stroke="var(--cbu-ink)" stroke-width="4" stroke-linecap="round"/>' +
    '<path d="M16 31c-5 0-7 4-6 8l1 3" stroke="var(--cbu-ink)" stroke-width="4" stroke-linecap="round"/>' +
    '<path class="cbu-arm" d="M48 32c5 0 7-4 6-8l-1-3" stroke="var(--cbu-ink)" stroke-width="4" stroke-linecap="round"/>' +
    '<path d="M32 14c-11 0-18 7-18 18v8c0 10 7 15 18 15s18-5 18-15v-8c0-11-7-18-18-18Z" fill="var(--cbu-mint)" stroke="var(--cbu-ink)" stroke-width="2"/>' +
    '<path d="M19 24c2-4 6-6 11-6" stroke="var(--cbu-highlight)" stroke-width="2.2" stroke-linecap="round"/>' +
    '<path d="M32 23c-9 0-13 3-13 10v3c0 6 5 9 13 9s13-3 13-9v-3c0-7-4-10-13-10Z" fill="var(--cbu-ink)"/>' +
    '<g class="cbu-eyes" stroke="var(--cbu-eye)" stroke-width="3" stroke-linecap="round"><path d="M26 32v4"/><path d="M38 32v4"/></g>' +
    '<path d="M29 40c2 1.4 4 1.4 6 0" stroke="var(--cbu-eye)" stroke-width="1.3" stroke-linecap="round"/>' +
    '<path d="M28 49h8" stroke="var(--cbu-ink)" stroke-opacity=".22" stroke-width="2" stroke-linecap="round"/>' +
    '</g><path class="cbu-heart" d="M52 15c-7-4-7-8-4-9 2-1 4 0 4 2 1-2 3-3 5-2 3 2 1 6-5 9Z" fill="var(--cbu-heart-color)"/></svg>';

  function element(document, tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function finiteNumber(value) { return typeof value === 'number' && Number.isFinite(value); }
  function textValue(value, fallback) {
    return typeof value === 'string' && value.trim() ? value.trim().slice(0, 180) : fallback;
  }
  function percentText(value) {
    if (value > 0 && value < 0.1) return '<0.1%';
    if (value < 100 && value > 99.9) return '>99.9%';
    return String(Math.round(value * 10) / 10) + '%';
  }
  function resetText(seconds, now) {
    if (!seconds) return '重置时间未提供';
    var minutes = Math.max(1, Math.ceil((seconds * 1000 - now) / 60000));
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor((minutes % 1440) / 60);
    if (days) return days + '天' + (hours ? hours + '小时' : '') + '后重置';
    if (minutes >= 60) return Math.floor(minutes / 60) + '小时后重置';
    return minutes + '分钟后重置';
  }

  function mount(container, options) {
    if (!container || container.nodeType !== 1 || typeof container.appendChild !== 'function') {
      throw new TypeError('CodexUsageBar.mount requires a DOM element.');
    }
    options = options || {};
    var document = container.ownerDocument;
    var view = document.defaultView || global;
    var disposed = false;
    var cleanups = [];
    var deadlineTimer = null;
    var nodTimer = null;
    var blurTimer = null;
    var snapshot = {};
    var compact = false;
    var open = false;
    var pinned = false;
    var hovering = false;
    var theme = 'auto';
    var panelId = 'cbu-source-' + (++nextId);
    var root = element(document, 'section', 'cbu-bar');
    root.setAttribute('aria-label', 'Codex 用量');
    root.dataset.mode = 'expanded';
    var row = element(document, 'div', 'cbu-row');
    var pet = element(document, 'button', 'cbu-pet');
    pet.type = 'button';
    pet.setAttribute('aria-label', '和 Milo 打个招呼');
    pet.title = '你好，我是 Milo';
    pet.innerHTML = robot;
    var metrics = element(document, 'div', 'cbu-metrics');
    var cells = {};
    [['secondary', '每周已用'], ['primary', '5 小时已用'], ['cache', 'Cache']].forEach(function (item) {
      var metric = element(document, 'div', 'cbu-metric');
      metric.dataset.metric = item[0];
      var label = element(document, 'div', 'cbu-label', item[1]);
      var value = element(document, 'div', 'cbu-value', '—');
      var detail = element(document, 'div', 'cbu-detail', '当前来源未提供');
      var track = element(document, 'div', 'cbu-track');
      track.setAttribute('aria-hidden', 'true');
      var fill = element(document, 'span', 'cbu-fill');
      track.appendChild(fill);
      metric.append(label, value, track, detail);
      metrics.appendChild(metric);
      cells[item[0]] = { node: metric, value: value, detail: detail, fill: fill, label: item[1] };
    });
    var controls = element(document, 'div', 'cbu-controls');
    var info = element(document, 'button', 'cbu-info', 'i');
    info.type = 'button';
    info.setAttribute('aria-label', '查看数据来源与状态');
    info.setAttribute('aria-expanded', 'false');
    info.setAttribute('aria-controls', panelId);
    var toggle = element(document, 'button', 'cbu-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-label', '收起用量条');
    toggle.setAttribute('aria-expanded', 'true');
    toggle.innerHTML = '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 10 4-4 4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    controls.append(info, toggle);
    row.append(pet, metrics, controls);
    var panel = element(document, 'div', 'cbu-source-panel');
    panel.id = panelId;
    panel.hidden = true;
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-label', '数据来源与状态');
    var heading = element(document, 'div', 'cbu-panel-title', '账户配额');
    var list = element(document, 'dl', 'cbu-source-list');
    var fields = {};
    [['source', '来源'], ['updated', '更新'], ['quota', '配额'], ['working', '运行']].forEach(function (item) {
      var line = element(document, 'div', 'cbu-source-line');
      var term = element(document, 'dt', '', item[1]);
      var value = element(document, 'dd', '');
      line.append(term, value);
      list.appendChild(line);
      fields[item[0]] = value;
    });
    panel.append(heading, list);
    root.append(row, panel);

    function listen(target, event, handler) {
      target.addEventListener(event, handler);
      cleanups.push(function () { target.removeEventListener(event, handler); });
    }
    function layoutChanged() {
      if (!disposed) root.dispatchEvent(new view.CustomEvent('codex-usage-bar:layoutchange', {
        bubbles: true, composed: true, detail: { compact: compact, infoOpen: open }
      }));
    }
    function setOpen(next) {
      if (disposed || open === next) return;
      open = next;
      panel.hidden = !open;
      root.dataset.infoOpen = String(open);
      info.setAttribute('aria-expanded', String(open));
      layoutChanged();
    }
    function hasInfoFocus() {
      // In the injected bar, document.activeElement is the shadow host.
      var scope = root.getRootNode();
      var focused = scope.activeElement || document.activeElement;
      return focused === info || panel.contains(focused);
    }
    function dismissIfOutside() {
      if (!pinned && !hovering && !hasInfoFocus()) setOpen(false);
    }
    function statusLabel(value) { return STATUS_LABELS[value] || '状态未知'; }
    function readWindow(key, now) {
      var window = snapshot.limits && snapshot.limits[key];
      var status = typeof snapshot.status === 'string' ? snapshot.status : 'unknown';
      var stale = snapshot.stale === true || status === 'stale';
      var permits = ['fresh', 'partial', 'stale', 'reset_pending'].indexOf(status) !== -1 ||
        (stale && BLOCKED.indexOf(status) === -1);
      if (!permits) return { available: false, detail: statusLabel(status) };
      if (!window || typeof window !== 'object') return { available: false, detail: '当前来源未提供' };
      var reset = window.resetsAt;
      if (reset !== null && reset !== undefined && (!finiteNumber(reset) || reset <= 0 || reset > 8640000000000)) {
        return { available: false, detail: '重置时间无效' };
      }
      if (finiteNumber(reset) && reset * 1000 <= now) return { available: false, detail: '等待重置后数据' };
      if (window.status !== 'fresh' && window.status !== 'stale') {
        return { available: false, detail: statusLabel(window.status) };
      }
      if (window.windowMinutes !== (key === 'primary' ? 300 : 10080)) {
        return { available: false, detail: '窗口数据不可用' };
      }
      if (!finiteNumber(window.usedPercent) || window.usedPercent < 0 || window.usedPercent > 100) {
        return { available: false, detail: '用量数据不可用' };
      }
      stale = stale || window.status === 'stale';
      return { available: true, value: window.usedPercent, reset: reset, stale: stale,
        detail: (stale ? '旧数据 · ' : '') + resetText(reset, now) };
    }
    function paintCell(key, state) {
      var cell = cells[key];
      cell.node.dataset.available = String(state.available);
      cell.node.dataset.unavailable = String(!state.available);
      cell.node.dataset.stale = String(state.stale === true);
      cell.value.textContent = state.available ? percentText(state.value) : '—';
      cell.detail.textContent = state.detail;
      cell.detail.title = state.detail;
      cell.fill.style.width = state.available ? state.value + '%' : '0%';
      cell.node.setAttribute('aria-label', cell.label + '，' +
        (state.available ? percentText(state.value) : '数据不可用') + '，' + state.detail);
    }
    function render() {
      if (disposed) return;
      if (deadlineTimer !== null) view.clearTimeout(deadlineTimer);
      deadlineTimer = null;
      var now = Date.now();
      var weekly = readWindow('secondary', now);
      var primary = readWindow('primary', now);
      paintCell('secondary', weekly);
      paintCell('primary', primary);
      paintCell('cache', { available: false, detail: '当前来源未提供' });
      root.dataset.stale = String(snapshot.stale === true || weekly.stale === true || primary.stale === true);
      fields.source.textContent = textValue(snapshot.sourceLabel, '当前来源未提供');
      var date = typeof snapshot.updatedAt === 'string' && snapshot.updatedAt.trim() ? new Date(snapshot.updatedAt) : null;
      fields.updated.textContent = date && Number.isFinite(date.getTime()) ?
        date.toLocaleString('zh-CN', { hour12: false }) : '更新时间未提供';
      fields.quota.textContent = statusLabel(snapshot.status) + '。每周：' + (weekly.available ? (weekly.stale ? '旧数据' : '可用') : weekly.detail) +
        '；5 小时：' + (primary.available ? (primary.stale ? '旧数据' : '可用') : primary.detail);
      fields.working.textContent = '运行状态未接通';
      var deadlines = [weekly, primary].filter(function (state) {
        return state.available && finiteNumber(state.reset) && state.reset * 1000 > now;
      }).map(function (state) { return state.reset * 1000; });
      if (deadlines.length) {
        deadlineTimer = view.setTimeout(render, Math.min(MAX_TIMEOUT, Math.max(1, Math.min.apply(null, deadlines) - now + 1)));
      }
    }
    function update(next) {
      if (disposed) return;
      // Copy only this renderer's contract; unrelated account or session fields never enter its DOM.
      var input = next && typeof next === 'object' ? next : {};
      var limits = input.limits && typeof input.limits === 'object' ? input.limits : {};
      snapshot = { status: input.status, stale: input.stale, sourceLabel: input.sourceLabel,
        updatedAt: input.updatedAt, limits: {} };
      ['primary', 'secondary'].forEach(function (key) {
        var item = limits[key];
        snapshot.limits[key] = item && typeof item === 'object' ? {
          usedPercent: item.usedPercent, windowMinutes: item.windowMinutes,
          resetsAt: item.resetsAt, status: item.status
        } : null;
      });
      render();
    }
    function setTheme(next) {
      if (disposed) return;
      theme = next === 'light' || next === 'dark' ? next : 'auto';
      root.dataset.theme = theme;
    }
    function destroy() {
      if (disposed) return;
      disposed = true;
      [deadlineTimer, nodTimer, blurTimer].forEach(function (timer) {
        if (timer !== null) view.clearTimeout(timer);
      });
      cleanups.reverse().forEach(function (cleanup) { cleanup(); });
      cleanups.length = 0;
      root.remove();
      snapshot = {};
    }
    try {
      listen(pet, 'click', function () {
        if (nodTimer !== null) view.clearTimeout(nodTimer);
        pet.classList.remove('is-nodding', 'is-burst');
        // Restart only this finite greeting animation; no background animation timer is needed.
        void pet.getBoundingClientRect();
        pet.classList.add('is-nodding', 'is-burst');
        nodTimer = view.setTimeout(function () {
          if (!disposed) pet.classList.remove('is-nodding', 'is-burst');
          nodTimer = null;
        }, 650);
      });
      listen(toggle, 'click', function () {
        compact = !compact;
        root.dataset.mode = compact ? 'compact' : 'expanded';
        toggle.setAttribute('aria-label', compact ? '展开用量条' : '收起用量条');
        toggle.setAttribute('aria-expanded', String(!compact));
        layoutChanged();
      });
      listen(info, 'click', function () { pinned = !pinned; setOpen(pinned); });
      listen(info, 'pointerenter', function () { hovering = true; setOpen(true); });
      listen(info, 'pointerleave', function () { hovering = false; });
      listen(panel, 'pointerenter', function () { hovering = true; });
      listen(panel, 'pointerleave', function () { hovering = false; dismissIfOutside(); });
      listen(root, 'pointerleave', function () { hovering = false; dismissIfOutside(); });
      listen(info, 'focus', function () { setOpen(true); });
      listen(root, 'focusout', function () {
        if (blurTimer !== null) view.clearTimeout(blurTimer);
        blurTimer = view.setTimeout(function () { blurTimer = null; dismissIfOutside(); }, 0);
      });
      listen(root, 'keydown', function (event) {
        if (event.key === 'Escape' && open) {
          pinned = false; hovering = false; info.focus();
          setOpen(false); event.stopPropagation();
        }
      });
      setTheme(options.theme);
      render();
      container.appendChild(root);
    } catch (error) {
      destroy();
      throw error;
    }
    return { update: update, element: root, destroy: destroy, setTheme: setTheme };
  }

  global.CodexUsageBar = Object.freeze({ mount: mount });
})(window);
