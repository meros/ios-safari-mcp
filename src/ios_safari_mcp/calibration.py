"""Where a point of the page is on the phone screen.

Taps are given in screen pixels, the page in CSS pixels. The two differ by an offset (the status bar, Safari's address bar
and toolbar, the keyboard) and a scale (3 on this phone, more with a pinch zoom), and both change while Safari's bars
collapse or the keyboard opens. So the mapping is measured: a 14 pixel magenta square is drawn at a known place of the
layout viewport (position: fixed), a screenshot shows where it landed, and the offset and scale follow. The result is
cached until the layout (viewport size, keyboard, zoom) changes, so the square shows only when the layout is new.
"""
import time
from dataclasses import dataclass

from . import qemu, screen
from .inspector import page
from .results import ToolError

SCREEN_W, SCREEN_H = 1125, 2436     # pixels of the phone screen


@dataclass
class Calibration:
    origin_x: float      # screen pixel of the layout viewport's top left corner
    origin_y: float
    scale: float         # screen pixels per CSS pixel
    key: tuple           # the layout it is valid for

    def to_screen(self, css_x, css_y):
        return self.origin_x + css_x * self.scale, self.origin_y + css_y * self.scale

    def to_fractions(self, css_x, css_y):
        x, y = self.to_screen(css_x, css_y)
        return round(x / SCREEN_W, 4), round(y / SCREEN_H, 4)


_cache = None


def find_marker(rgb, w, h, scale):
    """Bounding box, in screen pixels, of the magenta pixels of a screenshot made at 1/scale; None if there are none."""
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for y in range(h):
        row = rgb[y * w * 3:(y + 1) * w * 3]
        # cheap pre-test: a magenta pixel has a high red and blue byte and a low green byte; scan only rows that may have one
        for x in range(w):
            r, g, b = row[x * 3], row[x * 3 + 1], row[x * 3 + 2]
            if r > 200 and g < 70 and b > 200:
                x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
    if x1 < 0:
        return None
    return x0 * scale, y0 * scale, (x1 + 1) * scale, (y1 + 1) * scale


def _measure(s, timeout):
    x, y = page.MARKER_AT
    s.evaluate(page.call(page.MARKER_ADD, page.MARKER_ID, page.MARKER_SIZE, x, y))
    box = None
    try:
        end = time.time() + timeout
        while time.time() < end and box is None:     # the guest paints slowly: poll until the square shows
            time.sleep(1.5)
            w, h, rgb, _ = screen.screenshot(2)
            box = find_marker(rgb, w, h, 2)
    finally:
        s.evaluate(page.call(page.MARKER_REMOVE, page.MARKER_ID))
    if box is None:
        raise ToolError('the calibration marker did not show on the screen: is Safari in the foreground and the display on?')
    scale = (box[2] - box[0]) / page.MARKER_SIZE
    return box[0] - x * scale, box[1] - y * scale, scale


def calibrate(s, force=False, timeout=60):
    """The Calibration for the page's current layout (measured when the layout is new)."""
    global _cache
    if not qemu.alive():
        raise ToolError('QEMU is not running.')
    key = tuple(s.evaluate(page.call(page.LAYOUT_KEY)))
    if not force and _cache is not None and _cache.key == key:
        return _cache
    ox, oy, scale = _measure(s, timeout)
    _cache = Calibration(ox, oy, scale, key)
    return _cache
