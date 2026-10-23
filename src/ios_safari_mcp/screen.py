"""Pixels: screenshots from QEMU's screendump, PNG encoding, and waiting for the screen to settle."""
import os
import time
import zlib
from pathlib import Path

from . import config, qemu


def _chunk(tag, data):
    crc = zlib.crc32(tag + data) & 0xffffffff
    return len(data).to_bytes(4, 'big') + tag + data + crc.to_bytes(4, 'big')


def png(width, height, rgb):
    raw = b''.join(b'\0' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + b'\x08\x02\0\0\0')
            + _chunk(b'IDAT', zlib.compress(raw, 6)) + _chunk(b'IEND', b''))


def ppm_to_rgb(path, scale):
    """The kernel uses the top left 1125x2436 of the QEMU mode as the phone screen. QEMU pads each PPM row to 4 bytes."""
    d = Path(path).read_bytes()
    _magic, size, _maxval, pix = d.split(b'\n', 3)
    w, h = map(int, size.split())
    cw, ch = min(w, 1125), min(h, 2436)
    stride = len(pix) // h
    ow, oh = cw // scale, ch // scale
    out = bytearray(ow * oh * 3)
    for y in range(oh):
        row = pix[y * scale * stride:y * scale * stride + cw * 3]
        o = bytearray(ow * 3)
        for c in range(3):
            o[c::3] = row[c::3][::scale][:ow]
        out[y * ow * 3:(y + 1) * ow * 3] = o
    return ow, oh, bytes(out)


def _dump(name, scale):
    ppm = os.path.join(config.SHOTS, name)
    if os.path.exists(ppm):
        os.unlink(ppm)
    qemu.hmp(f'screendump {ppm}')
    if not os.path.exists(ppm) or os.path.getsize(ppm) < 1000:
        raise OSError('screendump produced no file')
    try:
        return ppm_to_rgb(ppm, scale)
    finally:
        os.unlink(ppm)


def screenshot(scale):
    """(width, height, rgb, png bytes) of the phone screen, scaled down by `scale`."""
    w, h, rgb = _dump('shot.ppm', scale)
    data = png(w, h, rgb)
    with open(os.path.join(config.SHOTS, 'last.png'), 'wb') as f:
        f.write(data)
    return w, h, rgb, data


def grab_frame(scale=6):
    """A checksum of a small copy of the screen, to tell if it changed."""
    w, h, rgb = _dump('settle.ppm', scale)
    return zlib.crc32(rgb[w * 3 * max(1, h // 25):])      # not the top 4%: the status bar clock ticks every minute


def settle(args, baseline=None):
    """Wait for the screen to change, then to stop changing. The guest answers an input only after 30-60 s
    (software drawing), so a still screen right after an input says nothing.
      wait_change   seconds to wait for the first change (default 90; 0 = do not wait for one)
      stable_for    seconds the screen must stay the same afterwards (default 6)
      timeout       the whole wait, at most 280 s
    `baseline` is a checksum taken before the input; without it the first frame is the baseline."""
    if not qemu.alive():
        return {'stable': False, 'reason': 'QEMU is not running'}
    timeout = min(float(args.get('timeout', 120)), 280)
    stable_for = float(args.get('stable_for', 6))
    wait_change = float(args.get('wait_change', 90))
    start = time.time()
    end = start + timeout
    last = baseline if baseline is not None else grab_frame()
    changed = wait_change <= 0
    since = None
    while time.time() < end:
        crc = grab_frame()
        now = time.time()
        if not changed:
            if crc != last:
                changed, last, since = True, crc, None
            elif now - start >= wait_change:
                return {'stable': True, 'changed': False, 'waited_s': round(now - start),
                        'note': 'the screen did not change'}
        elif crc == last:
            since = since or now
            if now - since >= stable_for:
                return {'stable': True, 'changed': wait_change > 0, 'waited_s': round(now - start)}
        else:
            last, since = crc, None
        time.sleep(1)
    return {'stable': False, 'changed': changed, 'reason': 'timeout'}
