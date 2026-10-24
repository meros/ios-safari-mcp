"""The on-screen keyboard of Safari's address bar, as taps.

Key centres were measured on a 562x1218 screenshot (scale 2): letters verified. Digits and symbols follow the standard
iPhone layout and are UNVERIFIED until a live run has typed them. Typing through the inspector (open_url, navigate) is
faster and does not need any of this; the keyboard is the fallback.
"""
import time

from . import qemu

KB_W, KB_H = 562, 1218
KB_ROWS_Y = (825, 905, 985, 1067)
KB_LETTERS = ('qwertyuiop', 'asdfghjkl', 'zxcvbnm')
KB_ROW_X0 = (28, 56, 112)
KB_STEP = 56.2
KB_SPECIAL = {'space': (270, 1067), '.': (429, 1067), 'go': (510, 1067),
              'shift': (30, 985), 'delete': (532, 985), '123': (50, 1067)}          # shift, delete, 123: UNVERIFIED
KB_NUMBERS = {**{c: (28 + KB_STEP * i, 825) for i, c in enumerate('1234567890')},    # UNVERIFIED (standard layout)
              **{c: (28 + KB_STEP * i, 905) for i, c in enumerate('-/:;()$&@"')}}


def kb_pos(key):
    """(page, x, y) in screenshot pixels of the reference size for one key, page 'abc' or '123'."""
    if key in KB_SPECIAL:
        return 'abc', *KB_SPECIAL[key]
    for r, row in enumerate(KB_LETTERS):
        if key in row:
            return 'abc', KB_ROW_X0[r] + KB_STEP * row.index(key), KB_ROWS_Y[r]
    if key in KB_NUMBERS:
        return '123', *KB_NUMBERS[key]
    raise ValueError(f'no key for {key!r}: letters a-z (lower case), digits, space and . - / : ( ) ; $ & @ " are known')


def plan_keys(text):
    """The taps that type `text`: a list of (page, key) with the page the keyboard must show."""
    out = []
    if text != text.lower():
        raise ValueError('upper case letters cannot be typed (the shift key is not mapped); use lower case or fill')
    for ch in text:
        key = 'space' if ch == ' ' else ch
        out.append((kb_pos(key)[0], key))
    return out


def frac(px, py):
    return round(px / KB_W, 4), round(py / KB_H, 4)


def tap_ref(x, y, delay):
    """Tap a point given in pixels of the 562x1218 reference screenshot."""
    fx, fy = frac(x, y)
    ok, out = qemu.touch('tap', fx, fy)
    if not ok:
        raise OSError(out)
    time.sleep(delay)


def type_keys(text, submit=False, delay=2.5):
    page = 'abc'                                           # the 123 key also switches back to abc, at the same place
    for want, key in plan_keys(text):
        if want != page:
            tap_ref(*KB_SPECIAL['123'], delay)
            page = want
        tap_ref(*kb_pos(key)[1:], delay)
    if submit:
        if page != 'abc':
            tap_ref(*KB_SPECIAL['123'], delay)
        tap_ref(*KB_SPECIAL['go'], delay)
