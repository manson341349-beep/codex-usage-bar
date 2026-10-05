/* MIT License — Copyright (c) 2026 codex-usage-bar contributors */
(function (global) {
  'use strict';

  var nextId = 0;
  var MAX_TIMEOUT = 2147483647;
  var CACHE_STATUSES = ['fresh', 'stale', 'waiting', 'unavailable', 'disconnected'];
  var BLOCKED = ['waiting', 'unavailable', 'disconnected', 'expired', 'unknown', 'missing', 'identity_unknown', 'account_changed', 'api_key_unsupported'];
  var COPY = {
    'zh-CN': {
      usage: 'Codex 用量', petLabel: '和 Sprig 芽团打个招呼', petTitle: '你好，我是 Sprig 芽团',
      labels: { secondary: '每周订阅剩余', primary: '5 小时已用', cache: '缓存命中' },
      shortLabels: { secondary: '周余', primary: '5h', cache: '缓存' },
      infoLabel: '查看数据来源与状态', infoTitle: '点击查看数据来源与状态', panel: '数据来源与状态',
      collapseLabel: '收起用量条', expandLabel: '展开用量条', collapseTitle: '折叠额度条', expandTitle: '展开额度条',
      heading: '账户配额', terms: { source: '来源', updated: '额度更新', quota: '配额', cache: '缓存', working: '运行' },
      missing: '当前来源未提供', resetMissing: '重置时间未提供', resetInvalid: '重置时间无效',
      windowUnavailable: '窗口数据不可用', usageUnavailable: '用量数据不可用', dataUnavailable: '数据不可用',
      oldPrefix: '旧数据 · ', old: '旧数据', available: '可用', cacheWaiting: '等待本会话统计',
      cacheTotal: '当前会话累计', cacheOld: '上次统计 · 当前会话累计', updatedMissing: '更新时间未提供',
      officialSource: 'Codex 订阅额度 · 官方 account/rateLimits/read',
      quotaExplanation: '。每周剩余 = 100% − 已用比例。每周：', primaryExplanation: '；5 小时：',
      cacheExplanation: '。来源：Codex 当前会话统计；命中率 = 累计缓存输入 ÷ 累计输入。',
      received: '收到时间：', cachePending: '初次接入或切换会话后，等待新的真实统计。',
      working: '运行状态未接通', separator: '，', period: '。',
      statuses: {
        fresh: '数据已更新', partial: '部分窗口可用', stale: '保留的旧数据',
        waiting: '等待账户数据', unavailable: '配额数据不可用', disconnected: '数据连接已断开',
        expired: '数据已过期', reset_pending: '等待重置后数据', unknown: '状态未知',
        missing: '当前来源未提供', error: '更新失败', auth_error: '账户验证失败',
        identity_unknown: '账户身份未确认', account_changed: '账户已切换，等待新数据',
        api_key_unsupported: '当前账户来源不支持配额', cli_missing: 'Codex CLI 不可用',
        launch_failed: '额度读取启动失败', timeout: '额度读取超时',
        protocol_error: '额度响应无效', read_failed: '额度读取失败'
      }
    },
    en: {
      usage: 'Codex usage', petLabel: 'Say hello to Sprig', petTitle: "Hi, I'm Sprig",
      labels: { secondary: 'Weekly left', primary: '5h used', cache: 'Cache hit' },
      ariaLabels: { secondary: 'Weekly subscription remaining', primary: '5-hour usage', cache: 'Cache hit rate' },
      shortLabels: { secondary: 'Wk', primary: '5h', cache: 'Cache' },
      infoLabel: 'View data source and status', infoTitle: 'View data source and status', panel: 'Data source and status',
      collapseLabel: 'Collapse usage bar', expandLabel: 'Expand usage bar', collapseTitle: 'Collapse usage bar', expandTitle: 'Expand usage bar',
      heading: 'Account quota', terms: { source: 'Source', updated: 'Updated', quota: 'Quota', cache: 'Cache', working: 'Activity' },
      missing: 'Not provided by this source', resetMissing: 'Reset time not provided', resetInvalid: 'Invalid reset time',
      windowUnavailable: 'Window data unavailable', usageUnavailable: 'Usage data unavailable', dataUnavailable: 'Data unavailable',
      oldPrefix: 'Stale data · ', old: 'Stale data', available: 'Available', cacheWaiting: 'Waiting for session statistics',
      cacheTotal: 'This session total', cacheOld: 'Previous statistics · This session total', updatedMissing: 'Update time not provided',
      officialSource: 'Codex subscription quota · official account/rateLimits/read',
      quotaExplanation: '. Weekly remaining = 100% − used. Weekly: ', primaryExplanation: '; 5 hours: ',
      cacheExplanation: '. Source: Codex session statistics; hit rate = total cached input ÷ total input. ',
      received: 'Received: ', cachePending: 'Waiting for new statistics after connecting or switching sessions.',
      working: 'Activity status is not connected', separator: ', ', period: '.',
      statuses: {
        fresh: 'Data updated', partial: 'Some windows available', stale: 'Saved stale data',
        waiting: 'Waiting for account data', unavailable: 'Quota data unavailable', disconnected: 'Data connection lost',
        expired: 'Data expired', reset_pending: 'Waiting for data after reset', unknown: 'Unknown status',
        missing: 'Not provided by this source', error: 'Update failed', auth_error: 'Account verification failed',
        identity_unknown: 'Account identity unconfirmed', account_changed: 'Account changed; waiting for new data',
        api_key_unsupported: 'Quota is not supported by this account source', cli_missing: 'Codex CLI unavailable',
        launch_failed: 'Could not start quota reader', timeout: 'Quota request timed out',
        protocol_error: 'Invalid quota response', read_failed: 'Could not read quota'
      }
    }
  };
  function normalizeLocale(value) {
    return typeof value === 'string' && /^zh(?:[-_]|$)/i.test(value.trim()) ? 'zh-CN' : 'en';
  }
  function defaultLocale(document, view) {
    var html = document.documentElement;
    var declared = html && (html.getAttribute('lang') || html.lang);
    if (typeof declared === 'string' && declared.trim()) return normalizeLocale(declared);
    var navigator = view.navigator || {};
    var preferred = typeof navigator.language === 'string' && navigator.language.trim() ? navigator.language : null;
    if (!preferred && Array.isArray(navigator.languages)) {
      preferred = navigator.languages.find(function (value) { return typeof value === 'string' && value.trim(); });
    }
    return normalizeLocale(preferred);
  }
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
  function resetText(seconds, now, locale) {
    if (!seconds) return COPY[locale].resetMissing;
    var minutes = Math.max(1, Math.ceil((seconds * 1000 - now) / 60000));
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor((minutes % 1440) / 60);
    if (locale === 'en') {
      if (days) return 'Resets in ' + days + 'd' + (hours ? ' ' + hours + 'h' : '');
      if (minutes >= 60) return 'Resets in ' + Math.floor(minutes / 60) + 'h';
      return 'Resets in ' + minutes + 'm';
    }
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
    var locale = options.locale === undefined ? defaultLocale(document, view) : normalizeLocale(options.locale);
    var copy = COPY[locale];
    var panelId = 'cbu-source-' + (++nextId);
    var root = element(document, 'section', 'cbu-bar');
    root.dataset.mode = 'expanded';
    var row = element(document, 'div', 'cbu-row');
    var pet = element(document, 'button', 'cbu-pet');
    pet.type = 'button';
    pet.innerHTML = sprigFallback;
    var metrics = element(document, 'div', 'cbu-metrics');
    var cells = {};
    ['secondary', 'primary', 'cache'].forEach(function (key) {
      var metric = element(document, 'div', 'cbu-metric');
      metric.dataset.metric = key;
      var label = element(document, 'div', 'cbu-label');
      var shortLabel = element(document, 'span', 'cbu-compact-label');
      var value = element(document, 'div', 'cbu-value', '—');
      var detail = element(document, 'div', 'cbu-detail');
      var track = element(document, 'div', 'cbu-track');
      track.setAttribute('aria-hidden', 'true');
      var fill = element(document, 'span', 'cbu-fill');
      track.appendChild(fill);
      metric.append(label, shortLabel, value, track, detail);
      metrics.appendChild(metric);
      cells[key] = { node: metric, value: value, detail: detail, fill: fill, label: label, shortLabel: shortLabel };
    });
    var controls = element(document, 'div', 'cbu-controls');
    var info = element(document, 'button', 'cbu-info', 'i');
    info.type = 'button';
    info.setAttribute('aria-expanded', 'false');
    info.setAttribute('aria-controls', panelId);
    var toggle = element(document, 'button', 'cbu-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'true');
    toggle.innerHTML = '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 10 4-4 4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    controls.append(info, toggle);
    row.append(pet, metrics, controls);
    var panel = element(document, 'div', 'cbu-source-panel');
    panel.id = panelId;
    panel.hidden = true;
    panel.setAttribute('role', 'region');
    var heading = element(document, 'div', 'cbu-panel-title');
    var list = element(document, 'dl', 'cbu-source-list');
    var fields = {}, terms = {};
    ['source', 'updated', 'quota', 'cache', 'working'].forEach(function (key) {
      var line = element(document, 'div', 'cbu-source-line');
      var term = element(document, 'dt');
      var value = element(document, 'dd', '');
      line.append(term, value);
      list.appendChild(line);
      fields[key] = value; terms[key] = term;
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
    function statusLabel(value) {
      return typeof copy.statuses[value] === 'string' ? copy.statuses[value] : copy.statuses.unknown;
    }
    function readWindow(key, now) {
      var window = snapshot.limits && snapshot.limits[key];
      var status = typeof snapshot.status === 'string' ? snapshot.status : 'unknown';
      var stale = snapshot.stale === true || status === 'stale';
      var permits = ['fresh', 'partial', 'stale', 'reset_pending'].indexOf(status) !== -1 ||
        (stale && BLOCKED.indexOf(status) === -1);
      if (!permits) return { available: false, detail: statusLabel(status) };
      if (!window || typeof window !== 'object') return { available: false, detail: copy.missing };
      var reset = window.resetsAt;
      if (reset !== null && reset !== undefined && (!finiteNumber(reset) || reset <= 0 || reset > 8640000000000)) {
        return { available: false, detail: copy.resetInvalid };
      }
      if (finiteNumber(reset) && reset * 1000 <= now) return { available: false, detail: copy.statuses.reset_pending };
      if (window.status !== 'fresh' && window.status !== 'stale') {
        return { available: false, detail: statusLabel(window.status) };
      }
      if (window.windowMinutes !== (key === 'primary' ? 300 : 10080)) {
        return { available: false, detail: copy.windowUnavailable };
      }
      if (!finiteNumber(window.usedPercent) || window.usedPercent < 0 || window.usedPercent > 100) {
        return { available: false, detail: copy.usageUnavailable };
      }
      stale = stale || window.status === 'stale';
      return { available: true, value: key === 'secondary' ? 100 - window.usedPercent : window.usedPercent, reset: reset, stale: stale,
        detail: (stale ? copy.oldPrefix : '') + resetText(reset, now, locale) };
    }
    function readCache() {
      var cache = snapshot.cache;
      if (!cache || cache.status === 'unavailable') return { available: false, detail: copy.missing };
      if (cache.status === 'waiting') return { available: false, detail: copy.cacheWaiting };
      if (cache.status === 'disconnected') return { available: false, detail: copy.statuses.disconnected };
      if ((cache.status !== 'fresh' && cache.status !== 'stale') ||
          !nonnegativeInteger(cache.inputTokens) || cache.inputTokens === 0 ||
          !nonnegativeInteger(cache.cachedInputTokens) || cache.cachedInputTokens > cache.inputTokens) {
        return { available: false, detail: copy.missing };
      }
      return { available: true, value: cache.cachedInputTokens / cache.inputTokens * 100,
        stale: cache.status === 'stale', detail: cache.status === 'stale' ?
          copy.cacheOld : copy.cacheTotal };
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
      cell.node.setAttribute('aria-label', (copy.ariaLabels || copy.labels)[key] + copy.separator +
        (state.available ? percentText(state.value) : copy.dataUnavailable) + copy.separator + state.detail);
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
      var source = textValue(snapshot.sourceLabel, copy.missing);
      fields.source.textContent = source === COPY['zh-CN'].officialSource || source === COPY.en.officialSource ? copy.officialSource : source;
      var date = typeof snapshot.updatedAt === 'string' && snapshot.updatedAt.trim() ? new Date(snapshot.updatedAt) : null;
      fields.updated.textContent = date && Number.isFinite(date.getTime()) ?
        date.toLocaleString(locale === 'zh-CN' ? 'zh-CN' : 'en-US', { hour12: false }) : copy.updatedMissing;
      fields.quota.textContent = statusLabel(snapshot.status) + copy.quotaExplanation + (weekly.available ? (weekly.stale ? copy.old : copy.available) : weekly.detail) +
        copy.primaryExplanation + (primary.available ? (primary.stale ? copy.old : copy.available) : primary.detail);
      var received = cache.available && snapshot.cache.observedAt ? new Date(snapshot.cache.observedAt) : null;
      fields.cache.textContent = cache.detail + copy.cacheExplanation +
        (received && Number.isFinite(received.getTime()) ? copy.received +
          received.toLocaleTimeString(locale === 'zh-CN' ? 'zh-CN' : 'en-US', { hour12: false }) + copy.period :
          copy.cachePending);
      fields.working.textContent = copy.working;
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
    function localizeControls() {
      root.setAttribute('lang', locale); root.setAttribute('dir', 'ltr'); root.dataset.locale = locale;
      root.setAttribute('aria-label', copy.usage);
      pet.setAttribute('aria-label', copy.petLabel); pet.title = copy.petTitle;
      info.setAttribute('aria-label', copy.infoLabel); info.title = copy.infoTitle;
      panel.setAttribute('aria-label', copy.panel); heading.textContent = copy.heading;
      Object.keys(cells).forEach(function (key) {
        cells[key].label.textContent = copy.labels[key];
        cells[key].label.title = copy.labels[key];
        cells[key].shortLabel.textContent = copy.shortLabels[key];
      });
      Object.keys(terms).forEach(function (key) { terms[key].textContent = copy.terms[key]; });
      toggle.setAttribute('aria-label', compact ? copy.expandLabel : copy.collapseLabel);
      toggle.title = compact ? copy.expandTitle : copy.collapseTitle;
    }
    function setLocale(next) {
      if (disposed) return;
      var normalized = next === undefined ? defaultLocale(document, view) : normalizeLocale(next);
      if (normalized === locale) return;
      locale = normalized; copy = COPY[locale];
      localizeControls(); render(); layoutChanged();
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
        toggle.setAttribute('aria-label', compact ? copy.expandLabel : copy.collapseLabel);
        toggle.setAttribute('aria-expanded', String(!compact));
        toggle.title = compact ? copy.expandTitle : copy.collapseTitle;
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
      localizeControls();
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
    return { update: update, element: root, destroy: destroy, setTheme: setTheme, setLocale: setLocale,
      setVisible: setVisible, inspectCompanion: inspectCompanion,
      closeInfo: function () { var wasOpen = open; setOpen(false); return wasOpen; } };
  }

  global.CodexUsageBar = Object.freeze({ mount: mount });
})(window);
