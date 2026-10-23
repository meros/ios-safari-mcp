"""Screen tools: screenshots and waiting for the screen to settle."""
import base64
import json

from .. import qemu, screen
from ..registry import tool
from ..results import ToolError, image_result, text_result


@tool('screenshot', 'PNG of the phone screen (1125x2436, scaled down by `scale`, default 2).',
      {'scale': {'type': 'integer', 'minimum': 1, 'maximum': 6}})
def screenshot(args):
    if not qemu.alive():
        raise ToolError('QEMU is not running.')
    scale = max(1, min(int(args.get('scale', 2)), 6))
    w, h, rgb, data = screen.screenshot(scale)
    caption = f'{w}x{h} (scale 1/{scale} of 1125x2436)'
    if not any(rgb):
        caption += '. All black: the display is probably off, call wake.'
    return image_result(base64.b64encode(data).decode(), caption)


@tool('settle', 'Wait until the screen stops changing (two frames equal for stable_for seconds) or the timeout ends.',
      {'timeout': {'type': 'number'}, 'stable_for': {'type': 'number'}})
def settle(args):
    r = screen.settle(args)
    return text_result(json.dumps(r), error=r.get('reason') == 'QEMU is not running')
