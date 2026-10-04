/* MIT License — Copyright (c) 2026 codex-usage-bar contributors */
(function (global) {
  'use strict';

  var nextId = 0;
  var MAX_TIMEOUT = 2147483647;
  var CACHE_STATUSES = ['fresh', 'stale', 'waiting', 'unavailable', 'disconnected'];
  var BLOCKED = ['waiting', 'unavailable', 'disconnected', 'expired', 'unknown', 'missing', 'identity_unknown', 'account_changed', 'api_key_unsupported'];
  var STATUS_LABELS = {
    fresh: '数据已更新', partial: '部分窗口可用', stale: '保留的旧数据',
    waiting: '等待账户数据', unavailable: '配额数据不可用', disconnected: '数据连接已断开',
    expired: '数据已过期', reset_pending: '等待重置后数据', unknown: '状态未知',
    missing: '当前来源未提供', error: '更新失败', auth_error: '账户验证失败',
    identity_unknown: '账户身份未确认', account_changed: '账户已切换，等待新数据',
    api_key_unsupported: '当前账户来源不支持配额'
  };
  // Original front-facing Sprig artwork remains visible without WebGL.
  var sprigFallback = '<svg class="cbu-sprig-fallback" aria-hidden="true" focusable="false" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><ellipse cx="50" cy="91" rx="24" ry="4" fill="#abc6b8" opacity=".4"/><path d="M32 37 Q17 7 30 5 Q43 7 43 32 M61 33 Q65 12 75 17 Q87 25 69 43" fill="#8abda4" stroke="#729e88" stroke-width="1"/><ellipse cx="50" cy="72" rx="21" ry="21" fill="#8abda4"/><ellipse cx="50" cy="72" rx="13" ry="16" fill="#ffebc9"/><ellipse cx="34" cy="88" rx="11" ry="6" fill="#8abda4"/><ellipse cx="66" cy="88" rx="11" ry="6" fill="#8abda4"/><rect x="17" y="28" width="66" height="50" rx="23" fill="#8abda4"/><rect x="23" y="39" width="54" height="33" rx="15" fill="#ffebc9"/><g fill="#223c32"><rect x="34" y="48" width="8" height="12" rx="4"/><rect x="58" y="48" width="8" height="12" rx="4"/></g><g fill="#fff"><circle cx="37" cy="51" r="1.6"/><circle cx="61" cy="51" r="1.6"/></g><path d="M46 64 Q50 68 54 64" fill="none" stroke="#223c32" stroke-width="1.6" stroke-linecap="round"/><g fill="#eab095"><ellipse cx="29" cy="62" rx="4" ry="2"/><ellipse cx="71" cy="62" rx="4" ry="2"/></g><circle cx="71" cy="25" r="3" fill="#eac376"/></svg>';

  function element(document, tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function finiteNumber(value) { return typeof value === 'number' && Number.isFinite(value); }
  function nonnegativeInteger(value) { return Number.isSafeInteger(value) && value >= 0; }
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
    var companion = null;
    var companionVisible = null;
    var hostVisible = options.visible !== false;
    var blurTimer = null;
    var snapshot = {};
    var compact = false;
    var open = false;
    var theme = 'auto';
    var panelId = 'cbu-source-' + (++nextId);
    var root = element(document, 'section', 'cbu-bar');
    root.setAttribute('aria-label', 'Codex 用量');
    root.dataset.mode = 'expanded';
    var row = element(document, 'div', 'cbu-row');
    var pet = element(document, 'button', 'cbu-pet');
    pet.type = 'button';
    pet.setAttribute('aria-label', '和 Sprig 芽团打个招呼');
    pet.title = '你好，我是 Sprig 芽团';
    pet.innerHTML = sprigFallback;
    var metrics = element(document, 'div', 'cbu-metrics');
    var cells = {};
    [['secondary', '每周订阅剩余'], ['primary', '5 小时已用'], ['cache', '缓存命中']].forEach(function (item) {
      var metric = element(document, 'div', 'cbu-metric');
      metric.dataset.metric = item[0];
      var label = element(document, 'div', 'cbu-label', item[1]);
      var shortLabel = element(document, 'span', 'cbu-compact-label',
        { secondary: '周余', primary: '5h', cache: '缓存' }[item[0]]);
      var value = element(document, 'div', 'cbu-value', '—');
      var detail = element(document, 'div', 'cbu-detail', '当前来源未提供');
      var track = element(document, 'div', 'cbu-track');
      track.setAttribute('aria-hidden', 'true');
      var fill = element(document, 'span', 'cbu-fill');
      track.appendChild(fill);
      metric.append(label, shortLabel, value, track, detail);
      metrics.appendChild(metric);
      cells[item[0]] = { node: metric, value: value, detail: detail, fill: fill, label: item[1] };
    });
    var controls = element(document, 'div', 'cbu-controls');
    var info = element(document, 'button', 'cbu-info', 'i');
    info.type = 'button';
    info.setAttribute('aria-label', '查看数据来源与状态');
    info.setAttribute('aria-expanded', 'false');
    info.setAttribute('aria-controls', panelId);
    info.title = '点击查看数据来源与状态';
    var toggle = element(document, 'button', 'cbu-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-label', '收起用量条');
    toggle.setAttribute('aria-expanded', 'true');
    toggle.title = '折叠额度条';
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
    [['source', '来源'], ['updated', '额度更新'], ['quota', '配额'], ['cache', '缓存'], ['working', '运行']].forEach(function (item) {
      var line = element(document, 'div', 'cbu-source-line');
      var term = element(document, 'dt', '', item[1]);
      var value = element(document, 'dd', '');
      line.append(term, value);
      list.appendChild(line);
      fields[item[0]] = value;
    });
    panel.append(heading, list);
    // Keep the controls nearest the composer when details grow upward.
    root.append(panel, row);

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
    function hasBarFocus() {
      // In the injected bar, document.activeElement is the shadow host.
      var scope = root.getRootNode();
      var focused = scope.activeElement || document.activeElement;
      return root.contains(focused);
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
      return { available: true, value: key === 'secondary' ? 100 - window.usedPercent : window.usedPercent, reset: reset, stale: stale,
        detail: (stale ? '旧数据 · ' : '') + resetText(reset, now) };
    }
    function readCache() {
      var cache = snapshot.cache;
      if (!cache || cache.status === 'unavailable') return { available: false, detail: '当前来源未提供' };
      if (cache.status === 'waiting') return { available: false, detail: '等待本会话统计' };
      if (cache.status === 'disconnected') return { available: false, detail: '数据连接已断开' };
      if ((cache.status !== 'fresh' && cache.status !== 'stale') ||
          !nonnegativeInteger(cache.inputTokens) || cache.inputTokens === 0 ||
          !nonnegativeInteger(cache.cachedInputTokens) || cache.cachedInputTokens > cache.inputTokens) {
        return { available: false, detail: '当前来源未提供' };
      }
      return { available: true, value: cache.cachedInputTokens / cache.inputTokens * 100,
        stale: cache.status === 'stale', detail: cache.status === 'stale' ?
          '上次统计 · 当前会话累计' : '当前会话累计' };
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
      var cache = readCache();
      paintCell('secondary', weekly);
      paintCell('primary', primary);
      paintCell('cache', cache);
      root.dataset.stale = String(snapshot.stale === true || weekly.stale === true || primary.stale === true);
      fields.source.textContent = textValue(snapshot.sourceLabel, '当前来源未提供');
      var date = typeof snapshot.updatedAt === 'string' && snapshot.updatedAt.trim() ? new Date(snapshot.updatedAt) : null;
      fields.updated.textContent = date && Number.isFinite(date.getTime()) ?
        date.toLocaleString('zh-CN', { hour12: false }) : '更新时间未提供';
      fields.quota.textContent = statusLabel(snapshot.status) + '。每周剩余 = 100% − 已用比例。每周：' + (weekly.available ? (weekly.stale ? '旧数据' : '可用') : weekly.detail) +
        '；5 小时：' + (primary.available ? (primary.stale ? '旧数据' : '可用') : primary.detail);
      fields.cache.textContent = cache.detail + '。来源：Codex 当前会话统计；命中率 = 累计缓存输入 ÷ 累计输入。' +
        (cache.available && snapshot.cache.observedAt ? '收到时间：' +
          new Date(snapshot.cache.observedAt).toLocaleTimeString('zh-CN', { hour12: false }) + '。' :
          '初次接入或切换会话后，等待新的真实统计。');
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
      var cache = input.cache && typeof input.cache === 'object' ? input.cache : {};
      snapshot = { status: input.status, stale: input.stale, sourceLabel: input.sourceLabel,
        updatedAt: input.updatedAt, limits: {}, cache: {
          status: CACHE_STATUSES.indexOf(cache.status) !== -1 ? cache.status : 'unavailable',
          inputTokens: nonnegativeInteger(cache.inputTokens) ? cache.inputTokens : null,
          cachedInputTokens: nonnegativeInteger(cache.cachedInputTokens) ? cache.cachedInputTokens : null,
          observedAt: nonnegativeInteger(cache.observedAt) ? cache.observedAt : null
        } };
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
      companionCall('setTheme', theme);
    }
    function releaseCompanion() {
      var previous = companion;
      companion = null;
      companionVisible = null;
      try { if (previous && typeof previous.destroy === 'function') previous.destroy(); }
      catch (_) { /* Renderer failures cannot disable real quota or cache data. */ }
    }
    function companionCall(method, value) {
      if (!companion) return null;
      try { return companion[method](value); }
      catch (_) {
        releaseCompanion();
        pet.innerHTML = sprigFallback;
        return null;
      }
    }
    function syncCompanionVisibility() {
      var visible = hostVisible && !compact;
      if (!companion || companionVisible === visible) return;
      companionVisible = visible;
      companionCall('setVisible', visible);
    }
    function setVisible(next) {
      if (disposed) return;
      hostVisible = next === true;
      syncCompanionVisibility();
    }
    function inspectCompanion() {
      if (disposed || !companion) return null;
      var source = companionCall('inspect');
      if (!source || typeof source !== 'object') return null;
      // Only this renderer's known diagnostics can leave the component.
      var result = {};
      ['visible', 'paused', 'reducedMotion', 'running', 'disposed', 'contextLost',
        'pointerListening', 'gpuTimerSupported'].forEach(function (key) {
        if (typeof source[key] === 'boolean') result[key] = source[key];
      });
      ['frames', 'drawCalls', 'triangles', 'width', 'height', 'pixelRatio', 'initCount',
        'rafMedianMs', 'rafP95Ms', 'cpuP95Ms', 'gpuP95Ms', 'rafSamples', 'gpuSamples'].forEach(function (key) {
        if (source[key] === null || (finiteNumber(source[key]) && source[key] >= 0)) result[key] = source[key];
      });
      if (['webgl', 'fallback'].indexOf(source.mode) !== -1) result.mode = source.mode;
      if (['idle', 'hello', 'play', 'work', 'done'].indexOf(source.state) !== -1) result.state = source.state;
      return result;
    }
    function destroy() {
      if (disposed) return;
      disposed = true;
      [deadlineTimer, blurTimer].forEach(function (timer) {
        if (timer !== null) view.clearTimeout(timer);
      });
      releaseCompanion();
      cleanups.reverse().forEach(function (cleanup) { cleanup(); });
      cleanups.length = 0;
      root.remove();
      snapshot = {};
    }
    try {
      listen(toggle, 'click', function () {
        setOpen(false);
        compact = !compact;
        root.dataset.mode = compact ? 'compact' : 'expanded';
        syncCompanionVisibility();
        toggle.setAttribute('aria-label', compact ? '展开用量条' : '收起用量条');
        toggle.setAttribute('aria-expanded', String(!compact));
        toggle.title = compact ? '展开额度条' : '折叠额度条';
        layoutChanged();
      });
      listen(info, 'click', function () { setOpen(!open); });
      listen(document, 'pointerdown', function (event) {
        var path = typeof event.composedPath === 'function' ? event.composedPath() : [];
        if (path.indexOf(root) === -1 && !root.contains(event.target)) setOpen(false);
      });
      listen(root, 'focusout', function () {
        if (blurTimer !== null) view.clearTimeout(blurTimer);
        blurTimer = view.setTimeout(function () { blurTimer = null; if (!hasBarFocus()) setOpen(false); }, 0);
      });
      listen(root, 'keydown', function (event) {
        if (event.key === 'Escape' && open) {
          info.focus();
          setOpen(false); event.stopPropagation();
        }
      });
      setTheme(options.theme);
      render();
      container.appendChild(root);
      // Enhancement is optional; the small static Sprig and all data work alone.
      if (global.CodexUsageBarSprig && typeof global.CodexUsageBarSprig.mount === 'function') {
        try {
          companion = global.CodexUsageBarSprig.mount(pet, { root: root });
          if (!companion || ['setVisible', 'setTheme', 'destroy', 'inspect'].some(function (method) {
            return typeof companion[method] !== 'function';
          })) throw new Error('invalid-sprig-renderer');
          companionCall('setTheme', theme);
          syncCompanionVisibility();
        } catch (_) {
          releaseCompanion();
          pet.innerHTML = sprigFallback;
        }
      }
    } catch (error) {
      destroy();
      throw error;
    }
    return { update: update, element: root, destroy: destroy, setTheme: setTheme,
      setVisible: setVisible, inspectCompanion: inspectCompanion,
      closeInfo: function () { var wasOpen = open; setOpen(false); return wasOpen; } };
  }

  global.CodexUsageBar = Object.freeze({ mount: mount });
})(window);
