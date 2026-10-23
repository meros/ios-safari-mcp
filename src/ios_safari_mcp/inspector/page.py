"""JavaScript that the tools run inside the page. Each snippet is an expression that returns JSON-serialisable data."""
import json


def call(fn, *args):
    """`(fn)(args...)` with the arguments as JSON literals."""
    return f'({fn})({", ".join(json.dumps(a) for a in args)})'


# --- DOM ---------------------------------------------------------------------------------------------------------------
DOM = r"""function(sel, what, limit) {
  var out = [];
  var els = document.querySelectorAll(sel);
  for (var i = 0; i < els.length && i < limit; i++) {
    var e = els[i], r = e.getBoundingClientRect(), item = {tag: e.tagName.toLowerCase()};
    if (what === 'html') { item.html = e.outerHTML; }
    else if (what === 'text') { item.text = e.innerText; }
    else {
      item.text = (e.innerText || e.value || '').slice(0, 200);
      item.attrs = {}; for (var a = 0; a < e.attributes.length; a++) { item.attrs[e.attributes[a].name] = e.attributes[a].value; }
      item.rect = {x: r.x, y: r.y, width: r.width, height: r.height};
      item.visible = !!(r.width && r.height) && getComputedStyle(e).visibility !== 'hidden';
    }
    out.push(item);
  }
  return {count: els.length, items: out, viewport: {width: innerWidth, height: innerHeight, scrollX: scrollX, scrollY: scrollY}};
}"""

# --- keyboard and viewport ----------------------------------------------------------------------------------------------
# On iOS the software keyboard does not resize the layout viewport (innerHeight stays); it shrinks the visual viewport
# (visualViewport.height) and may scroll it (offsetTop). So the keyboard height is innerHeight - visualViewport.height.
STATE = r"""function() {
  var vv = window.visualViewport, bottom = vv.offsetTop + vv.height;
  function d(e) {
    var r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return {tag: e.tagName.toLowerCase(), id: e.id || undefined, type: e.type || undefined,
            text: (e.innerText || e.placeholder || e.value || '').trim().replace(/\s+/g, ' ').slice(0, 40),
            top: Math.round(r.top), bottom: Math.round(r.bottom), position: cs.position};
  }
  var a = document.activeElement;
  var editable = !!(a && a !== document.body && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)));
  var shown = [].slice.call(document.querySelectorAll(
    'a,button,input,select,textarea,summary,[role=button],[role=link],[role=option],[role=menuitem],[tabindex]')).filter(function(e) {
      var r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top < vv.offsetTop + innerHeight && r.bottom > 0; });
  var covered = shown.filter(function(e) { var r = e.getBoundingClientRect(); return r.top < bottom && r.bottom > bottom || r.top >= bottom && r.top < innerHeight; });
  var fixed = [].slice.call(document.querySelectorAll('body *')).filter(function(e) {
    var p = getComputedStyle(e).position, r = e.getBoundingClientRect();
    return (p === 'fixed' || p === 'sticky') && r.height > 0 && r.width > 0 && r.top < innerHeight && r.bottom > 0; }).map(function(e) {
      var x = d(e); x.behindKeyboard = e.getBoundingClientRect().bottom > bottom && e.getBoundingClientRect().height < innerHeight * 0.6; return x; });
  var popups = [].slice.call(document.querySelectorAll(
    '[role=listbox],[role=option],[role=menu],[class*=uggest],[class*=utocomplete],[class*=ropdown],[class*=opover],dialog[open]')).filter(function(e) {
      var r = e.getBoundingClientRect(), cs = getComputedStyle(e);
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && r.top < innerHeight && r.bottom > 0; }).map(function(e) {
      var x = d(e); x.behindKeyboard = e.getBoundingClientRect().bottom > bottom; return x; });
  return {
    keyboardVisible: innerHeight - vv.height * vv.scale > 120 && vv.scale <= 1.01,
    keyboardHeight: Math.max(0, Math.round(innerHeight - vv.height * vv.scale)),
    layoutViewport: {width: innerWidth, height: innerHeight},
    visualViewport: {width: Math.round(vv.width), height: Math.round(vv.height), offsetTop: Math.round(vv.offsetTop),
                     offsetLeft: Math.round(vv.offsetLeft), scale: vv.scale},
    scroll: {x: Math.round(scrollX), y: Math.round(scrollY)},
    active: editable ? d(a) : null,
    activeVisible: editable ? (a.getBoundingClientRect().top >= vv.offsetTop && a.getBoundingClientRect().bottom <= bottom) : null,
    coveredByKeyboard: covered.slice(0, 25).map(d),
    fixed: fixed.slice(0, 10),
    popups: popups.slice(0, 10)
  };
}"""

SCROLL_INTO_VIEW = r"""function(sel, index, block) {
  var e = document.querySelectorAll(sel)[index];
  if (!e) return null;
  e.scrollIntoView({block: block, inline: 'nearest'});
  return true;
}"""

RECT = r"""function(sel, index) {
  var e = document.querySelectorAll(sel)[index];
  if (!e) return null;
  var r = e.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width, height: r.height,
          tag: e.tagName.toLowerCase(), text: (e.innerText || e.placeholder || e.value || '').trim().replace(/\s+/g, ' ').slice(0, 40)};
}"""

# a value set the way a framework notices: through the native setter, then input and change events
FILL = r"""function(sel, index, value, change) {
  var e = document.querySelectorAll(sel)[index];
  if (!e) return null;
  var proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  var d = Object.getOwnPropertyDescriptor(proto, 'value');
  if (d && d.set) { d.set.call(e, value); } else { e.value = value; }
  e.dispatchEvent(new Event('input', {bubbles: true}));
  if (change) e.dispatchEvent(new Event('change', {bubbles: true}));
  return e.value;
}"""

BLUR = "function() { var a = document.activeElement; if (a && a !== document.body) { a.blur(); return true; } return false; }"

# --- the event recorder -------------------------------------------------------------------------------------------------
# Records what matters for keyboard bugs: focus changes, key and composition events, and every change of the visual
# viewport, each with the viewport numbers at that moment. It lives in the page, so a navigation removes it.
RECORDER_INSTALL = r"""function() {
  if (window.__iosSafariMcp) return {installed: false, events: window.__iosSafariMcp.events.length};
  var E = window.__iosSafariMcp = {events: [], t0: Date.now()};
  function rec(type, e, extra) {
    var t = e && e.target, vv = window.visualViewport;
    var o = {t: Date.now() - E.t0, type: type,
             target: t && t.tagName ? t.tagName.toLowerCase() + (t.id ? '#' + t.id : '') + (t.name ? '[name=' + t.name + ']' : '') : undefined,
             vv: vv ? {h: Math.round(vv.height), top: Math.round(vv.offsetTop), scale: vv.scale} : undefined,
             inner: innerHeight, scrollY: Math.round(scrollY)};
    for (var k in extra) { if (extra[k] !== undefined && extra[k] !== null) o[k] = extra[k]; }
    E.events.push(o);
    if (E.events.length > 3000) E.events.shift();
  }
  ['focusin', 'focusout'].forEach(function(n) { document.addEventListener(n, function(e) { rec(n, e); }, true); });
  ['keydown', 'keyup', 'beforeinput', 'input', 'compositionstart', 'compositionupdate', 'compositionend'].forEach(function(n) {
    document.addEventListener(n, function(e) {
      rec(n, e, {key: e.key, code: e.code, data: e.data, inputType: e.inputType, composing: e.isComposing});
    }, true);
  });
  window.addEventListener('resize', function(e) { rec('window.resize', e); });
  window.addEventListener('scroll', function(e) { rec('scroll', e); }, {passive: true});
  if (window.visualViewport) {
    visualViewport.addEventListener('resize', function(e) { rec('visualViewport.resize', e); });
    visualViewport.addEventListener('scroll', function(e) { rec('visualViewport.scroll', e); });
  }
  return {installed: true, events: 0};
}"""

RECORDER_READ = r"""function(since, clear) {
  var E = window.__iosSafariMcp;
  if (!E) return null;
  var out = E.events.slice(since);
  var total = E.events.length;
  if (clear) E.events.length = 0;
  return {total: total, events: out};
}"""

RECORDER_REMOVE = "function() { var had = !!window.__iosSafariMcp; delete window.__iosSafariMcp; return had; }"

# --- calibration marker -------------------------------------------------------------------------------------------------
MARKER_ID = 'ios-safari-mcp-marker'
MARKER_SIZE = 14      # CSS pixels
MARKER_AT = (6, 96)   # CSS pixels from the top left of the layout viewport, below the status bar

MARKER_ADD = ("function(id, size, x, y) { var d = document.getElementById(id) || document.createElement('div'); d.id = id;"
              "d.style.cssText = 'position:fixed;left:' + x + 'px;top:' + y + 'px;width:' + size + 'px;height:' + size + 'px;"
              "background:#f0f;z-index:2147483647;pointer-events:none'; document.documentElement.appendChild(d); return true; }")
MARKER_REMOVE = "function(id) { var d = document.getElementById(id); if (d) d.remove(); return true; }"
LAYOUT_KEY = ("function() { var v = window.visualViewport; return [innerWidth, innerHeight, Math.round(v.offsetTop),"
              "Math.round(v.offsetLeft), v.scale, Math.round(v.height)]; }")
