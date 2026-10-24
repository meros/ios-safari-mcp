"""The software keyboard, which is the hardest part of web on iOS.

What happens on iOS Safari when a text field gets the keyboard: the layout viewport keeps its size (window.innerHeight does
not change), the visual viewport shrinks (visualViewport.height) and may scroll (offsetTop), position:fixed elements stay
glued to the layout viewport and so can end up behind the keyboard, and script cannot open the keyboard (element.focus()
outside a user gesture focuses the field without it). These tools show that state, open and close the keyboard with a real
tap, record the events around it, and report what the keyboard covers.
"""
import base64
import json
import time

from .. import calibration, qemu, screen
from ..inspector import manager, page
from ..registry import tool
from ..results import ToolError, json_result, text_result
from .inspector import PAGE_PROP

DEFAULT_FIELD = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]),textarea'
# the Done button of Safari's form accessory bar, as fractions of the screen (measured on a screenshot with the keyboard up)
DONE_BUTTON = (0.909, 0.560)
SELECTOR_PROPS = {'selector': {'type': 'string', 'description': 'CSS selector of the element'},
                  'index': {'type': 'integer', 'description': 'which match, default 0'}}


def state(s):
    return manager.evaluate_json(s, page.STATE)


def _wait_keyboard(s, visible, timeout):
    """Poll until the keyboard is up (or down). The guest animates slowly: 5 to 30 s are normal."""
    t0 = time.time()
    st = state(s)
    while st['keyboardVisible'] != visible and time.time() - t0 < timeout:
        time.sleep(1.5)
        st = state(s)
    return st, time.time() - t0


def _rect(s, selector, index):
    r = manager.evaluate_json(s, page.RECT, selector, index)
    if r is None:
        raise ToolError(f'no element for {selector!r} (index {index})')
    return r


def _tap_css(s, x, y):
    cal = calibration.calibrate(s)
    fx, fy = cal.to_fractions(x, y)
    ok, out = qemu.touch('tap', fx, fy)
    if not ok:
        raise ToolError(out)
    return {'css': {'x': round(x, 1), 'y': round(y, 1)}, 'screen_fraction': {'x': fx, 'y': fy},
            'pixels_per_css_pixel': round(cal.scale, 3)}


def _summary(st):
    """What matters in a state, without the long lists."""
    return {k: st[k] for k in ('keyboardVisible', 'keyboardHeight', 'layoutViewport', 'visualViewport', 'scroll', 'active', 'activeVisible')}


# --- state, show, hide -------------------------------------------------------------------------------------------------
@tool('keyboard_state', 'Is the keyboard up, how tall is it, and what does it cover? Returns the layout and visual viewport, '
      'the focused field and whether it is visible above the keyboard, the interactive elements and popups that reach under '
      'the keyboard, and the fixed or sticky elements (with behindKeyboard set when a bar is hidden by it). Heights are CSS px.',
      {**PAGE_PROP})
def keyboard_state(args):
    return json_result(state(manager.session(args)))


@tool('keyboard_show', 'Open the keyboard on a field with a real tap (script focus() does not open it on iOS): scrolls the '
      'field into view, finds where it is on the screen, taps it, and waits until the visual viewport has shrunk. '
      'Returns the state with the keyboard up.',
      {**SELECTOR_PROPS, 'scroll': {'type': 'boolean', 'description': 'scroll the field to the middle first, default true'},
       'timeout': {'type': 'number', 'description': 'seconds to wait for the keyboard, default 120'}, **PAGE_PROP}, ['selector'])
def keyboard_show(args):
    s = manager.session(args)
    selector, index = args['selector'], int(args.get('index', 0))
    st = state(s)
    if st['keyboardVisible'] and st['active'] and st['activeVisible']:
        return json_result({'already_visible': True, **st})
    if args.get('scroll', True) and not st['keyboardVisible']:
        if manager.evaluate_json(s, page.SCROLL_INTO_VIEW, selector, index, 'center') is None:
            raise ToolError(f'no element for {selector!r} (index {index})')
        time.sleep(2)
    tapped = _tap_css(s, *(lambda r: (r['x'], r['y']))(_rect(s, selector, index)))
    st, took = _wait_keyboard(s, True, float(args.get('timeout', 120)))
    if not st['keyboardVisible']:
        raise ToolError(f'the keyboard did not appear within {args.get("timeout", 120)} s after the tap '
                        f'(is {selector!r} a text field?). {json.dumps(_summary(st))}')
    return json_result({'opened_in_s': round(took, 1), 'tapped': tapped, **st})


@tool('keyboard_hide', 'Close the keyboard and wait for the viewport to recover. method=blur (default) blurs the field, as '
      'a page does; done taps Safari\'s Done button above the keyboard (what a user presses); return taps the keyboard\'s bottom '
      'right key (it may submit a form); tap_outside taps a point of the page outside the field (x and y in CSS px; default the left edge).',
      {'method': {'type': 'string', 'enum': ['blur', 'done', 'return', 'tap_outside']}, 'x': {'type': 'number'}, 'y': {'type': 'number'},
       'timeout': {'type': 'number'}, **PAGE_PROP})
def keyboard_hide(args):
    s = manager.session(args)
    method = args.get('method', 'blur')
    before = state(s)
    if not before['keyboardVisible']:
        return json_result({'already_hidden': True, **_summary(before)})
    if method == 'blur':
        manager.evaluate_json(s, page.BLUR)
    elif method == 'done':
        ok, out = qemu.touch('tap', DONE_BUTTON[0], DONE_BUTTON[1])     # the form accessory bar of Safari: ^ v Done
        if not ok:
            raise ToolError(out)
    elif method == 'return':
        ok, out = qemu.touch('tap', 0.9, 0.875)
        if not ok:
            raise ToolError(out)
    elif method == 'tap_outside':
        vv = before['visualViewport']
        _tap_css(s, float(args.get('x', 2)), float(args.get('y', vv['offsetTop'] + vv['height'] / 2)))
    else:
        raise ToolError('method: blur, done, return or tap_outside')
    st, took = _wait_keyboard(s, False, float(args.get('timeout', 90)))
    if st['keyboardVisible']:
        raise ToolError(f'the keyboard is still up {took:.0f} s after {method}. {json.dumps(_summary(st))}')
    return json_result({'closed_in_s': round(took, 1), 'method': method, **_summary(st),
                        'viewport_recovered': st['visualViewport']['height'] == st['layoutViewport']['height']})


# --- events ------------------------------------------------------------------------------------------------------------
@tool('keyboard_events', 'Record what the page sees around the keyboard: focusin/focusout, keydown/keyup, beforeinput/input, '
      'composition events and every window and visualViewport resize or scroll, each with the viewport numbers at that '
      'moment. action=start installs the recorder (a navigation removes it), read returns the events (since = how many to skip), '
      'stop removes it.',
      {'action': {'type': 'string', 'enum': ['start', 'read', 'stop']}, 'since': {'type': 'integer'}, 'clear': {'type': 'boolean'},
       **PAGE_PROP}, ['action'])
def keyboard_events(args):
    s = manager.session(args)
    action = args['action']
    if action == 'start':
        return json_result(manager.evaluate_json(s, page.RECORDER_INSTALL))
    if action == 'stop':
        return json_result({'removed': manager.evaluate_json(s, page.RECORDER_REMOVE)})
    out = manager.evaluate_json(s, page.RECORDER_READ, int(args.get('since', 0)), bool(args.get('clear')))
    if out is None:
        return text_result('the recorder is not installed in this page (call keyboard_events action=start; a navigation removes it)', error=True)
    return json_result(out)


# --- element taps and input --------------------------------------------------------------------------------------------
@tool('tap_element', 'Tap the middle of a page element by CSS selector, with a real touch at the right screen position '
      '(measured, so it is right whatever Safari\'s bars and the keyboard are doing).',
      {**SELECTOR_PROPS, 'scroll': {'type': 'boolean', 'description': 'scroll it into view first, default true'}, **PAGE_PROP},
      ['selector'])
def tap_element(args):
    s = manager.session(args)
    selector, index = args['selector'], int(args.get('index', 0))
    if args.get('scroll', True):
        if manager.evaluate_json(s, page.SCROLL_INTO_VIEW, selector, index, 'nearest') is None:
            raise ToolError(f'no element for {selector!r} (index {index})')
        time.sleep(1.5)
    r = _rect(s, selector, index)
    return json_result({'element': {k: r[k] for k in ('tag', 'text')}, **_tap_css(s, r['x'], r['y'])})


@tool('tap_css', 'Tap a point given in CSS pixels of the layout viewport (what getBoundingClientRect returns).',
      {'x': {'type': 'number'}, 'y': {'type': 'number'}, **PAGE_PROP}, ['x', 'y'])
def tap_css(args):
    return json_result(_tap_css(manager.session(args), float(args['x']), float(args['y'])))


@tool('fill', 'Set the value of a field the way a framework notices (native setter, then input and change events). Fast, but '
      'synthetic: no key events, no keyboard, no autocorrect or composition. Use keyboard_show and type_text for those.',
      {**SELECTOR_PROPS, 'value': {'type': 'string'}, 'change': {'type': 'boolean', 'description': 'also fire change, default true'},
       **PAGE_PROP}, ['selector', 'value'])
def fill(args):
    s = manager.session(args)
    v = manager.evaluate_json(s, page.FILL, args['selector'], int(args.get('index', 0)), args['value'], args.get('change', True))
    if v is None:
        raise ToolError(f'no element for {args["selector"]!r}')
    return json_result({'value': v})


@tool('scroll_into_view', 'Scroll an element into view (block: start, center, end or nearest).',
      {**SELECTOR_PROPS, 'block': {'type': 'string', 'enum': ['start', 'center', 'end', 'nearest']}, **PAGE_PROP}, ['selector'])
def scroll_into_view(args):
    s = manager.session(args)
    if manager.evaluate_json(s, page.SCROLL_INTO_VIEW, args['selector'], int(args.get('index', 0)), args.get('block', 'center')) is None:
        raise ToolError(f'no element for {args["selector"]!r}')
    return text_result('scrolled')


# --- the whole check ---------------------------------------------------------------------------------------------------
def problems(st):
    out = []
    if st['active'] is None:
        out.append('no field has the focus')
    elif not st['activeVisible']:
        out.append('the focused field is not visible above the keyboard')
    for f in st['fixed']:
        if f.get('behindKeyboard'):
            out.append(f'fixed/sticky {f["tag"]} {f.get("text", "")!r} (bottom {f["bottom"]}) is behind the keyboard')
    for p in st['popups']:
        if p.get('behindKeyboard'):
            out.append(f'popup {p["tag"]} {p.get("text", "")!r} (bottom {p["bottom"]}) reaches under the keyboard')
    return out


@tool('keyboard_check', 'Open the keyboard on a field and report whether it covers anything important: the field itself, fixed '
      'bars, suggestion popups. Optionally puts a value in the field first, to bring up suggestions. Returns the report and a '
      'screenshot with the keyboard up, then closes the keyboard and checks that the viewport recovers.',
      {'selector': {'type': 'string', 'description': 'the field; default the first visible text input'},
       'index': {'type': 'integer'}, 'value': {'type': 'string', 'description': 'put this in the field (fill) after the keyboard is up, so that suggestions appear'},
       'settle_s': {'type': 'number', 'description': 'seconds to wait after the value, default 8'},
       'hide': {'type': 'boolean', 'description': 'close the keyboard afterwards, default true'}, **PAGE_PROP})
def keyboard_check(args):
    s = manager.session(args)
    selector = args.get('selector') or DEFAULT_FIELD
    index = int(args.get('index', 0))
    manager.evaluate_json(s, page.RECORDER_INSTALL)
    before = state(s)
    shown = json.loads(keyboard_show({**args, 'selector': selector, 'index': index})['content'][0]['text'])
    if args.get('value') is not None:
        manager.evaluate_json(s, page.FILL, selector, index, args['value'], False)
        time.sleep(float(args.get('settle_s', 8)))
        shown = {**shown, **state(s)}
    st = shown
    w, h, rgb, data = screen.screenshot(2)
    found = problems(st)
    report = {
        'verdict': 'ok' if not found else 'problems',
        'problems': found,
        'field': st['active'],
        'opened_in_s': shown.get('opened_in_s'),
        'keyboard_height_css_px': st['keyboardHeight'],
        'viewport_before': before['visualViewport'], 'viewport_with_keyboard': st['visualViewport'],
        'layout_viewport': st['layoutViewport'],
        'popups': st['popups'], 'fixed': st['fixed'],
        'interactive_elements_reaching_under_the_keyboard': st['coveredByKeyboard'],
    }
    if args.get('hide', True):
        hidden = json.loads(keyboard_hide({'method': 'blur', **{k: v for k, v in args.items() if k in ('page', 'wait')}})['content'][0]['text'])
        report['after_hide'] = hidden
        if not hidden.get('viewport_recovered'):
            report['problems'].append('the visual viewport did not return to the layout viewport size after the keyboard closed')
            report['verdict'] = 'problems'
    ev = manager.evaluate_json(s, page.RECORDER_READ, 0, False)
    if ev:
        counts = {}
        for e in ev['events']:
            counts[e['type']] = counts.get(e['type'], 0) + 1
        report['events_seen'] = counts
    return {'content': [{'type': 'text', 'text': json.dumps(report, ensure_ascii=False, indent=1, default=str)},
                        {'type': 'image', 'data': base64.b64encode(data).decode(), 'mimeType': 'image/png'},
                        {'type': 'text', 'text': f'screenshot {w}x{h} with the keyboard up'}]}
