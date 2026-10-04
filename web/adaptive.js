/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Codex Usage Bar contributors
 * Original adapter: only structural composer inspection, never chat text.
 */
(function () {
  'use strict';

  const ROOT = '[data-codex-composer-root]';
  const PORTAL = '[data-above-composer-portal]';
  const EDITOR = '.ProseMirror[contenteditable="true"]';
  const PERSISTENT_EDITOR = EDITOR + ',.ProseMirror[contenteditable="false"]';
  const TURNS = '[data-content-search-turn-key],[data-turn-key]';
  const MARKER = '[data-codex-usage-bar]';
  const HEARTBEAT_MS = 15000;
  const MAX_ANCESTORS = 64;
  const EPSILON = 0.5;
  const SOURCE = 'Codex 订阅额度 · 官方 account/rateLimits/read';
  const GLOBAL_STATES = new Set([
    'waiting', 'fresh', 'partial', 'unavailable', 'stale', 'expired',
    'reset_pending', 'identity_unknown', 'account_changed',
    'api_key_unsupported', 'cli_missing', 'launch_failed', 'timeout',
    'protocol_error', 'read_failed', 'disconnected'
  ]);
  const WINDOW_STATES = new Set([
    'fresh', 'stale', 'reset_pending', 'expired', 'unknown', 'missing',
    'disconnected'
  ]);
  let active = null;

  function finite(value, minimum, maximum) {
    return typeof value === 'number' && Number.isFinite(value) &&
      value >= minimum && value <= maximum ? value : null;
  }

  function emptySnapshot(status) {
    return {
      limits: {
        primary: { usedPercent: null, windowMinutes: 300, resetsAt: null, status: 'missing' },
        secondary: { usedPercent: null, windowMinutes: 10080, resetsAt: null, status: 'missing' }
      },
      status: status,
      stale: true,
      sourceLabel: SOURCE,
      updatedAt: null,
      working: null,
      accountOnly: true
    };
  }

  function sanitize(input) {
    if (!input || typeof input !== 'object' || Array.isArray(input)) return null;
    const output = emptySnapshot(GLOBAL_STATES.has(input.status) ? input.status : 'unavailable');
    output.stale = input.stale === true;
    if (typeof input.sourceLabel === 'string' && input.sourceLabel.length <= 160 &&
        !/[\u0000-\u001f\u007f]/.test(input.sourceLabel)) {
      output.sourceLabel = input.sourceLabel;
    }
    if (typeof input.updatedAt === 'string' &&
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(input.updatedAt) &&
        Number.isFinite(Date.parse(input.updatedAt))) {
      output.updatedAt = input.updatedAt;
    }
    for (const key of ['primary', 'secondary']) {
      const value = input.limits && input.limits[key];
      if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
      const target = output.limits[key];
      if (value.windowMinutes !== target.windowMinutes) continue;
      target.status = WINDOW_STATES.has(value.status) ? value.status : 'unknown';
      target.resetsAt = finite(value.resetsAt, 0, 253402300799);
      if (target.status === 'fresh' || target.status === 'stale') {
        target.usedPercent = finite(value.usedPercent, 0, 100);
      }
    }
    return output;
  }

  function rect(element) {
    const box = element.getBoundingClientRect();
    return { x: box.x, y: box.y, top: box.top, left: box.left,
      right: box.right, bottom: box.bottom, width: box.width, height: box.height };
  }

  function validRect(box) {
    return Object.values(box).every(Number.isFinite) && box.width > 0 && box.height > 0;
  }

  function contained(inner, outer, x, y) {
    return (!x || (inner.left >= outer.left - EPSILON && inner.right <= outer.right + EPSILON)) &&
      (!y || (inner.top >= outer.top - EPSILON && inner.bottom <= outer.bottom + EPSILON));
  }

  function overlap(a, b) {
    return a.left < b.right - EPSILON && a.right > b.left + EPSILON &&
      a.top < b.bottom - EPSILON && a.bottom > b.top + EPSILON;
  }

  function supportedURL(homeOnly) {
    const url = new URL(window.location.href);
    return url.protocol === 'app:' && url.hostname === '-' && !url.port &&
      !url.username && !url.password && (!homeOnly ||
        ((url.pathname === '/' || url.pathname === '/index.html') && !url.search && !url.hash));
  }

  function ancestorChain(element) {
    const nodes = [];
    for (let node = element; node; node = node.parentElement) {
      if (nodes.length === MAX_ANCESTORS) return null;
      nodes.push(node);
    }
    return nodes;
  }

  function shown(element) {
    const ancestors = ancestorChain(element);
    if (!ancestors || !element.isConnected || !validRect(rect(element))) return false;
    for (const node of ancestors) {
      const style = window.getComputedStyle(node);
      if (node.hidden || node.inert || node.getAttribute('aria-hidden') === 'true' ||
          style.display === 'none' || style.visibility !== 'visible' ||
          Number(style.opacity) === 0 || style.contentVisibility === 'hidden') return false;
    }
    return true;
  }

  function emptyEditor(editor) {
    // Even an empty/whitespace text node is rejected; its contents are never read.
    for (const node of editor.childNodes) {
      if (node.nodeType !== 1 || (node.tagName !== 'P' && node.tagName !== 'BR')) return false;
      if (node.tagName === 'BR' && node.childNodes.length) return false;
      if (node.tagName === 'P') {
        for (const child of node.childNodes) {
          if (child.nodeType !== 1 || child.tagName !== 'BR' || child.childNodes.length) return false;
        }
      }
    }
    return true;
  }

  function install(options) {
    if (!options || typeof options.homeOnly !== 'boolean' || typeof options.css !== 'string' ||
        options.css.length > 262144) throw new Error('usage-bar-invalid-options');
    if (!window.CodexUsageBar || typeof window.CodexUsageBar.mount !== 'function' ||
        !window.MutationObserver || !window.ResizeObserver || !window.requestAnimationFrame ||
        !window.cancelAnimationFrame || !window.performance || !window.performance.now ||
        !window.matchMedia || !document.documentElement ||
        typeof Element.prototype.attachShadow !== 'function') {
      throw new Error('usage-bar-unsupported-environment');
    }
    if (active) active.dispose();

    let disposed = false;
    let host = null;
    let mounted = null;
    let target = null;
    let composingEditor = null;
    let layout = null;
    let state = 'unmounted';
    const homeOnly = options.homeOnly;
    let reason = homeOnly ? 'waiting-for-home' : 'waiting-for-composer';
    let frame = null;
    let timer = null;
    let mutation = null;
    let resize = null;
    let reconciling = false;
    let latest = emptySnapshot('waiting');
    let lastHeartbeat = window.performance.now();
    let effectiveKey = '';
    let effective = latest;
    let contentRevision = 0;
    let layoutRevision = 0;
    let hiddenFingerprint = null;
    let naturalHeight = 0;
    let currentTheme = null;
    const knownHosts = new WeakSet();
    const listeners = [];
    const observed = new Set();
    const media = window.matchMedia('(prefers-color-scheme: dark)');

    function listen(node, name, handler, capture) {
      node.addEventListener(name, handler, capture || false);
      listeners.push([node, name, handler, capture || false]);
    }

    function theme() {
      for (const node of [document.documentElement, document.body]) {
        if (!node) continue;
        const value = node.getAttribute('data-theme');
        if (value === 'dark' || value === 'light') return value;
        if (node.classList.contains('dark')) return 'dark';
        if (node.classList.contains('light')) return 'light';
      }
      return media.matches ? 'dark' : 'light';
    }

    function candidate() {
      if (!supportedURL(homeOnly)) return { reason: 'unsupported-route' };
      if (homeOnly && document.querySelector(TURNS)) return { reason: 'conversation-present' };
      const roots = Array.from(document.querySelectorAll(ROOT)).filter(shown);
      if (roots.length !== 1) return { reason: homeOnly ? 'ambiguous-home' : 'ambiguous-composer' };
      const root = roots[0];
      const placement = root.getAttribute('data-composer-placement');
      if (placement !== 'home' && (homeOnly || placement !== 'thread')) {
        return { reason: 'unsupported-composer' };
      }
      const portals = Array.from(root.children).filter(node => node.matches(PORTAL));
      if (portals.length !== 1) return { reason: 'ambiguous-portal' };
      const portal = portals[0];
      if (['absolute', 'fixed'].includes(window.getComputedStyle(portal).position)) {
        return { reason: 'unsupported-portal-flow' };
      }
      // The isolated home-only mode rejects an association using a structural
      // selector. Persistent mode never inspects its value or any conversation ID.
      if (homeOnly && portal.hasAttribute('data-above-composer-conversation-id') &&
          !portal.matches('[data-above-composer-conversation-id=""]')) {
        return { reason: 'conversation-associated' };
      }
      const editors = Array.from(root.querySelectorAll(homeOnly ? EDITOR : PERSISTENT_EDITOR))
        .filter(node => homeOnly || shown(node));
      if (editors.length !== 1 || !shown(editors[0])) return { reason: 'ambiguous-editor' };
      if (homeOnly && editors[0] === composingEditor) return { reason: 'editor-composing' };
      if (homeOnly && !emptyEditor(editors[0])) return { reason: 'nonempty-editor' };
      if (!ancestorChain(portal)) return { reason: 'unsupported-ancestry' };
      const foreign = Array.from(document.querySelectorAll(MARKER)).some(node => node !== host);
      if (foreign) return { reason: 'ownership-conflict' };
      return { root: root, portal: portal, editor: editors[0], placement: placement };
    }

    function snapshot() {
      if (window.performance.now() - lastHeartbeat >= HEARTBEAT_MS) {
        return emptySnapshot('disconnected');
      }
      const output = sanitize(latest);
      const now = Date.now() / 1000;
      for (const key of ['primary', 'secondary']) {
        const limit = output.limits[key];
        if (limit.resetsAt !== null && limit.resetsAt <= now) {
          limit.usedPercent = null;
          limit.status = 'reset_pending';
          output.stale = true;
          if (['fresh', 'partial', 'stale', 'reset_pending'].includes(output.status)) {
            output.status = 'reset_pending';
          }
        }
      }
      return output;
    }

    function refreshSnapshot() {
      effective = snapshot();
      const key = JSON.stringify(effective);
      if (key !== effectiveKey) {
        effectiveKey = key;
        contentRevision += 1;
      }
      if (mounted) mounted.update(effective);
    }

    function viewport() {
      const visual = window.visualViewport;
      const left = visual ? visual.offsetLeft : 0;
      const top = visual ? visual.offsetTop : 0;
      const width = Math.min(document.documentElement.clientWidth, visual ? visual.width : window.innerWidth);
      const height = Math.min(document.documentElement.clientHeight, visual ? visual.height : window.innerHeight);
      return { x: left, y: top, left: left, top: top, right: left + width,
        bottom: top + height, width: width, height: height };
    }

    function clientBox(node) {
      const box = rect(node);
      // DOMRects include CSS zoom/transforms; client/offset metrics are CSS pixels.
      const sx = node.offsetWidth > 0 ? box.width / node.offsetWidth : 0;
      const sy = node.offsetHeight > 0 ? box.height / node.offsetHeight : 0;
      return { left: box.left + node.clientLeft * sx,
        top: box.top + node.clientTop * sy,
        right: box.left + (node.clientLeft + node.clientWidth) * sx,
        bottom: box.top + (node.clientTop + node.clientHeight) * sy,
        width: node.clientWidth * sx, height: node.clientHeight * sy };
    }

    function fingerprint() {
      const values = [contentRevision, layoutRevision, currentTheme, viewport()];
      const nativePortalChildren = Array.from(target.portal.children).filter(node => node !== host);
      for (const node of [target.root, target.editor].concat(
          ancestorChain(target.portal) || [], nativePortalChildren)) {
        const style = window.getComputedStyle(node);
        values.push(rect(node), node.clientWidth, node.clientHeight, style.overflowX,
          style.overflowY, style.display, style.visibility, style.transform, style.zoom);
      }
      return JSON.stringify(values);
    }

    function measure() {
      const bar = rect(host);
      const content = rect(mounted.element);
      const editor = rect(target.editor);
      const view = viewport();
      let clipped = false;
      let unsupportedTransform = false;
      let clipCount = 0;
      for (const ancestor of ancestorChain(target.portal) || []) {
        const style = window.getComputedStyle(ancestor);
        // An axis-aligned client rectangle cannot prove a rotated/skewed clip safe.
        if (style.transform !== 'none') {
          const transform = style.transform.match(/^matrix\(([^)]+)\)$/);
          if (!transform) unsupportedTransform = true;
          else {
            const terms = transform[1].split(',').map(Number);
            if (terms.length !== 6 || terms.some(value => !Number.isFinite(value)) ||
                terms[0] <= 0 || terms[3] <= 0 || terms[1] !== 0 || terms[2] !== 0) {
              unsupportedTransform = true;
            }
          }
        }
        const clipX = /^(hidden|clip|scroll|auto)$/.test(style.overflowX);
        const clipY = /^(hidden|clip|scroll|auto)$/.test(style.overflowY);
        if (style.display !== 'contents' && (clipX || clipY)) {
          clipCount += 1;
          const clip = clientBox(ancestor);
          if (!contained(bar, clip, clipX, clipY) || !contained(content, clip, clipX, clipY)) clipped = true;
        }
      }
      const overlaps = overlap(bar, editor) || overlap(content, editor);
      const portalOverlap = Array.from(target.portal.children).some(node =>
        node !== host && shown(node) && (overlap(bar, rect(node)) || overlap(content, rect(node))));
      const inside = contained(bar, view, true, true) && contained(content, view, true, true);
      const overflow = !contained(content, bar, true, true) ||
        mounted.element.scrollWidth > mounted.element.clientWidth + 1;
      let failure = null;
      if (!validRect(bar) || !validRect(content) || !validRect(editor) || !validRect(view)) failure = 'unmeasurable';
      else if (unsupportedTransform) failure = 'unsupported-transform';
      else if (overflow) failure = 'content-overflow';
      else if (overlaps) failure = 'native-overlap';
      else if (portalOverlap) failure = 'native-portal-overlap';
      else if (bar.bottom > editor.top + EPSILON) failure = 'unsupported-order';
      else if (clipped) failure = 'ancestor-clipped';
      else if (!inside) failure = 'outside-viewport';
      return {
        mode: 'native-flow', visible: failure === null, hiddenReason: failure,
        root: rect(target.root), portal: rect(target.portal), bar: bar, editor: editor,
        gapToNativeContent: editor.top - Math.max(bar.bottom, content.bottom),
        overlapsNativeContent: overlaps || portalOverlap, clippedByAncestor: clipped, barWithinViewport: inside,
        reservedHeight: host.offsetHeight + 10, naturalHeight: mounted.element.offsetHeight,
        safety: { checked: true, safe: failure === null, reason: failure, clipCount: clipCount }
      };
    }

    function hide(measured) {
      host.style.setProperty('display', 'none', 'important');
      host.style.setProperty('visibility', 'hidden', 'important');
      hiddenFingerprint = fingerprint();
      naturalHeight = measured.naturalHeight;
      layout = {
        mode: 'native-flow', visible: false, hiddenReason: measured.hiddenReason,
        root: rect(target.root), portal: rect(target.portal), bar: rect(host), editor: rect(target.editor),
        gapToNativeContent: null, overlapsNativeContent: false, clippedByAncestor: false,
        barWithinViewport: false, reservedHeight: 0, naturalHeight: naturalHeight,
        safety: measured.safety
      };
      state = 'hidden';
      reason = measured.hiddenReason;
    }

    function checkLayout() {
      if (hiddenFingerprint !== null && fingerprint() === hiddenFingerprint) return;
      // Probe and collapse happen in one JS task; the unsafe bar is never painted.
      host.style.setProperty('visibility', 'hidden', 'important');
      host.style.setProperty('display', 'block', 'important');
      let measured = measure();
      // Details must never make the entire bar disappear with no close control.
      if (!measured.visible && mounted.closeInfo()) measured = measure();
      naturalHeight = measured.naturalHeight;
      if (!measured.visible) {
        hide(measured);
        return;
      }
      hiddenFingerprint = null;
      host.style.setProperty('visibility', 'visible', 'important');
      layout = measured;
      state = 'mounted';
      reason = homeOnly ? 'home-ready' : 'composer-ready';
    }

    function observeGeometry() {
      const wanted = new Set([document.documentElement]);
      if (target) {
        wanted.add(target.editor);
        for (const ancestor of ancestorChain(target.portal) || []) wanted.add(ancestor);
        for (const node of target.portal.children) if (node !== host) wanted.add(node);
      }
      for (const node of observed) {
        if (!wanted.has(node)) { resize.unobserve(node); observed.delete(node); }
      }
      for (const node of wanted) {
        if (!observed.has(node)) { resize.observe(node); observed.add(node); }
      }
    }

    function unmount(nextReason) {
      const previous = mounted;
      const previousHost = host;
      mounted = null;
      host = null;
      target = null;
      layout = null;
      currentTheme = null;
      hiddenFingerprint = null;
      naturalHeight = 0;
      state = 'unmounted';
      reason = nextReason;
      try { if (previous) previous.destroy(); }
      finally { if (previousHost) previousHost.remove(); }
      if (resize && !disposed) observeGeometry();
    }

    function mount(next) {
      target = next;
      host = document.createElement('div');
      knownHosts.add(host);
      host.setAttribute('data-codex-usage-bar', '');
      host.style.cssText = 'display:block!important;visibility:hidden!important;position:relative!important;' +
        'box-sizing:border-box!important;width:100%!important;max-width:100%!important;min-width:0!important;' +
        'height:auto!important;margin:0 0 10px!important;padding:0!important;border:0!important;' +
        'flex:none!important;float:none!important;transform:none!important;' +
        'grid-column:1 / -1!important;grid-row:auto!important;align-self:stretch!important;justify-self:stretch!important;';
      const shadow = host.attachShadow({ mode: 'open' });
      const stylesheet = document.createElement('style');
      stylesheet.textContent = options.css;
      const container = document.createElement('div');
      container.style.cssText = 'display:block;box-sizing:border-box;width:100%;min-width:0;margin:0;padding:0;';
      shadow.append(stylesheet, container);
      target.portal.append(host);
      currentTheme = theme();
      const result = window.CodexUsageBar.mount(container, { accountOnly: true, theme: currentTheme });
      // Retain even an incomplete return value so its destroy method can run on failure.
      mounted = result;
      if (!mounted || typeof mounted.update !== 'function' || typeof mounted.destroy !== 'function' ||
          typeof mounted.setTheme !== 'function' || !(mounted.element instanceof Element) ||
          !container.contains(mounted.element)) throw new Error('usage-bar-invalid-renderer');
      mounted.update(effective);
      observeGeometry();
    }

    function reconcile() {
      if (disposed || reconciling) return;
      reconciling = true;
      try {
        const next = candidate();
        if (!next.root) { unmount(next.reason); return; }
        if (host && (!host.isConnected || host.parentElement !== next.portal || !target ||
            target.root !== next.root || target.portal !== next.portal || target.editor !== next.editor)) {
          unmount(homeOnly ? 'home-replaced' : 'composer-replaced');
        }
        refreshSnapshot();
        if (!host) mount(next);
        const nextTheme = theme();
        if (nextTheme !== currentTheme) {
          currentTheme = nextTheme;
          mounted.setTheme(nextTheme);
        }
        checkLayout();
      } catch (_) {
        // A runtime compatibility failure removes every owned resource, not just pixels.
        dispose();
        state = 'unsupported';
        reason = 'runtime-incompatible';
      } finally {
        reconciling = false;
      }
    }

    function schedule() {
      if (disposed || frame !== null) return;
      frame = window.requestAnimationFrame(function () { frame = null; reconcile(); });
    }

    function flush() {
      if (frame !== null) { window.cancelAnimationFrame(frame); frame = null; }
      reconcile();
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      if (frame !== null) window.cancelAnimationFrame(frame);
      if (timer !== null) window.clearInterval(timer);
      frame = null;
      timer = null;
      if (mutation) mutation.disconnect();
      if (resize) resize.disconnect();
      observed.clear();
      for (const item of listeners.splice(0)) item[0].removeEventListener(item[1], item[2], item[3]);
      try { unmount('disposed'); } catch (_) { /* host removal is in unmount's finally */ }
      latest = emptySnapshot('disconnected');
      composingEditor = null;
      effective = latest;
      effectiveKey = '';
      state = 'disposed';
      reason = 'disposed';
      if (active === api) active = null;
    }

    const api = Object.freeze({
      setAccountSnapshot: function (input) {
        if (disposed) return false;
        let clean;
        try { clean = sanitize(input); } catch (_) { return false; }
        if (!clean) return false;
        latest = clean;
        lastHeartbeat = window.performance.now();
        flush();
        return !disposed;
      },
      inspect: function () {
        // Fixed state labels and numeric geometry only: no DOM nodes, identifiers or text.
        return { status: state, reason: reason, homeOnly: homeOnly,
          layout: layout === null ? null : JSON.parse(JSON.stringify(layout)) };
      },
      dispose: dispose
    });

    try {
      resize = new ResizeObserver(schedule);
      mutation = new MutationObserver(function (records) {
        if (host && !host.isConnected) { schedule(); return; }
        for (const record of records) {
          if (knownHosts.has(record.target)) continue;
          if (record.type === 'childList') {
            const changed = Array.from(record.addedNodes).concat(Array.from(record.removedNodes));
            if (changed.length && changed.every(node => knownHosts.has(node))) continue;
          }
          schedule();
          return;
        }
      });
      mutation.observe(document.documentElement, {
        subtree: true, childList: true, attributes: true,
        attributeFilter: ['class', 'style', 'hidden', 'inert', 'aria-hidden', 'contenteditable',
          'data-theme', 'data-codex-composer-root', 'data-composer-placement',
          'data-above-composer-portal', 'data-above-composer-conversation-id',
          'data-content-search-turn-key', 'data-turn-key']
      });
      listen(window, 'resize', schedule);
      listen(window, 'popstate', schedule);
      listen(window, 'hashchange', schedule);
      listen(window, 'pageshow', schedule);
      listen(document, 'visibilitychange', schedule);
      listen(document, 'scroll', schedule, true);
      listen(media, 'change', schedule);
      if (window.visualViewport) {
        listen(window.visualViewport, 'resize', schedule);
        listen(window.visualViewport, 'scroll', schedule);
      }
      if (window.navigation) listen(window.navigation, 'currententrychange', schedule);
      listen(document, 'codex-usage-bar:layoutchange', function (event) {
        if (host && event.target === host) { layoutRevision += 1; schedule(); }
      });
      const editing = function (event) {
        if (target && (event.target === target.editor || target.editor.contains(event.target))) {
          if (homeOnly) {
            if (event.type === 'compositionstart') composingEditor = target.editor;
            unmount('editor-input');
          }
          schedule();
        }
      };
      listen(document, 'beforeinput', editing, true);
      listen(document, 'input', editing, true);
      listen(document, 'compositionstart', editing, true);
      listen(document, 'compositionend', function () {
        if (composingEditor) { composingEditor = null; schedule(); }
        else if (!homeOnly) schedule();
      }, true);
      observeGeometry();
      timer = window.setInterval(function () {
        if (disposed) return;
        try { refreshSnapshot(); } catch (_) { dispose(); return; }
        schedule();
      }, 1000);
      active = api;
      flush();
      if (disposed) throw new Error('usage-bar-install-failed');
      return api;
    } catch (_) {
      dispose();
      throw new Error('usage-bar-install-failed');
    }
  }

  window.CodexUsageBarAdapter = Object.freeze({ install: install });
})();
