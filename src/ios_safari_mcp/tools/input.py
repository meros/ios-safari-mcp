"""Touch and button tools. Positions are fractions of the screen unless a tool says otherwise."""
import time

from .. import keyboard, qemu
from ..registry import fraction, tool, unit
from ..results import text_result

KEYS = ('lock', 'home', 'volup', 'voldown')


def _touch_result(ok, out, done):
    return text_result(out or done, error=not ok)


@tool('tap', 'Tap. x and y are fractions of the screen, 0,0 top left. For a page element use tap_element.',
      {'x': fraction('x'), 'y': fraction('y')}, ['x', 'y'], acting=True)
def tap(args):
    return _touch_result(*qemu.touch('tap', unit(args['x'], 'x'), unit(args['y'], 'y')), 'tapped')


@tool('hold', 'Touch and hold.', {'x': fraction('x'), 'y': fraction('y'), 'ms': {'type': 'integer'}}, ['x', 'y'], acting=True)
def hold(args):
    return _touch_result(*qemu.touch('hold', unit(args['x'], 'x'), unit(args['y'], 'y'), int(args.get('ms', 800))), 'held')


@tool('swipe', 'Swipe. Swipe up from y=0.985 to open or leave an app; from the left edge for back.',
      {'x0': fraction('x0'), 'y0': fraction('y0'), 'x1': fraction('x1'), 'y1': fraction('y1'), 'ms': {'type': 'integer'}},
      ['x0', 'y0', 'x1', 'y1'], acting=True)
def swipe(args):
    return _touch_result(*qemu.touch('swipe', unit(args['x0'], 'x0'), unit(args['y0'], 'y0'), unit(args['x1'], 'x1'),
                                     unit(args['y1'], 'y1'), int(args.get('ms', 300))), 'swiped')


@tool('key', 'Press a hardware button: lock, home, volup, voldown.',
      {'name': {'type': 'string', 'enum': list(KEYS)}, 'ms': {'type': 'integer'}}, ['name'], acting=True)
def key(args):
    name = args['name']
    if name not in KEYS:
        return text_result(f'key must be one of {KEYS}', error=True)
    return _touch_result(*qemu.touch('key', name, int(args.get('ms', 800))), f'pressed {name}')


@tool('tap_px', 'Tap a point given in pixels of a screenshot you took (set scale to the same value as the screenshot).',
      {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'scale': {'type': 'integer', 'minimum': 1, 'maximum': 6}},
      ['x', 'y'], acting=True)
def tap_px(args):
    scale = int(args.get('scale', 2))
    return tap({'x': float(args['x']) * scale / 1125, 'y': float(args['y']) * scale / 2436})


@tool('type_text', 'Type by tapping the keys of the on-screen keyboard, which must be showing: slow (about 2.5 s a key) '
      'but real key events. Lower-case letters, digits, space and . - / : ( ) ; $ & @ " are known; set submit to press go. '
      'For fast input use fill.',
      {'text': {'type': 'string'}, 'submit': {'type': 'boolean'}, 'delay_s': {'type': 'number'}}, ['text'], acting=True)
def type_text(args):
    keyboard.plan_keys(args['text'])        # refuse text with unknown keys before touching anything
    keyboard.type_keys(args['text'], bool(args.get('submit')), float(args.get('delay_s', 2.5)))
    return text_result(f'typed {len(args["text"])} keys' + (' and pressed go' if args.get('submit') else ''))


@tool('scroll', 'Scroll the page (direction up/down/left/right, amount as a fraction of the screen, default 0.4).',
      {'direction': {'type': 'string', 'enum': ['up', 'down', 'left', 'right']}, 'amount': {'type': 'number'}}, acting=True)
def scroll(args):
    d = args.get('direction', 'down')
    amount = max(0.05, min(float(args.get('amount', 0.4)), 0.8))
    c = 0.5
    moves = {'down': (c, c + amount / 2, c, c - amount / 2), 'up': (c, c - amount / 2, c, c + amount / 2),
             'right': (c + amount / 2, c, c - amount / 2, c), 'left': (c - amount / 2, c, c + amount / 2, c)}
    if d not in moves:
        return text_result('direction: up, down, left or right', error=True)
    x0, y0, x1, y1 = moves[d]
    return _touch_result(*qemu.touch('swipe', round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3), int(args.get('ms', 400))),
                         f'scrolled {d}')


@tool('back', "Back in Safari: taps the toolbar back arrow (the toolbar must be showing; scroll up a little or tap the bottom "
      'bar first). method=swipe uses the left-edge gesture, which did not navigate in testing.',
      {'method': {'type': 'string', 'enum': ['button', 'swipe']}, 'ms': {'type': 'integer'}}, acting=True)
def back(args):
    if args.get('method', 'button') == 'swipe':
        return _touch_result(*qemu.touch('swipe', 0.0, 0.5, 0.65, 0.5, int(args.get('ms', 500))), 'back')
    return _touch_result(*qemu.touch('tap', 0.075, 0.932), 'back')


@tool('home_screen', 'Leave the app: swipe up from the bottom edge.', acting=True)
def home_screen(args):
    return _touch_result(*qemu.touch('swipe', 0.5, 0.985, 0.5, 0.4, 250), 'swiped up from the bottom edge')


@tool('wake', 'Wake the display (it turns off after boot): a tap and then the home key.', acting=True)
def wake(args):
    qemu.touch('tap', 0.5, 0.5)
    time.sleep(3)
    return _touch_result(*qemu.touch('key', 'home', 800), 'sent tap + home key; take a screenshot to check')
