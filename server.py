#!/usr/bin/env python3
"""MCP server (stdio, no dependencies) that drives HackiOS (iOS 16.5 on XNU) running under QEMU.

The tools work on pixels and touches: boot the VM, take a screenshot, tap, swipe, press a button.
They wrap what the hackios-xnu-kernel tree already has: the QEMU monitor (screendump, port 4444 + 10n),
QMP (input events, port 4445 + 10n, through qemu/touch.py) and the console log.

Environment:
  HACKIOS_DIR         the checked-out tree (default ~/git/personal/hackios)
  HACKIOS_MCP_STATE   where the log, pid file and screenshots go (default ~/.local/state/hackios-mcp)
  QEMU_INSTANCE       QEMU instance number (default 0)
"""
import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
HACKIOS = os.environ.get('HACKIOS_DIR', os.path.expanduser('~/git/personal/hackios'))
STATE = os.environ.get('HACKIOS_MCP_STATE', os.path.expanduser('~/.local/state/hackios-mcp'))
INSTANCE = int(os.environ.get('QEMU_INSTANCE', '0'))
HMP_PORT = 4444 + 10 * INSTANCE
LOG = os.path.join(STATE, 'run.log')
PIDFILE = os.path.join(STATE, 'run.pid')
SHOTS = os.path.join('/tmp', f'hackios-mcp-{os.getuid()}')
os.makedirs(STATE, exist_ok=True)
os.makedirs(SHOTS, exist_ok=True)

# phases in boot order: (name, regex that the log shows when the phase is reached)
PHASES = [
    ('kernel', r'Security policy loaded'),
    ('launchd', r'Successfully spawned'),
    ('backboardd_ready', r'Bootstrap system shells ready'),
    ('springboard_owns_screen', r'screen owner is now pid:\d+ \(com\.apple\.springboard\)'),
]
PANIC = re.compile(r'^panic\(cpu', re.M)


def log_text():
    try:
        with open(LOG, 'rb') as f:
            return f.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def vm_pid():
    try:
        pid = int(open(PIDFILE).read().split()[0])
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, IndexError):
        return None


def vm_started_at():
    try:
        return float(open(PIDFILE).read().split()[1])
    except (OSError, ValueError, IndexError):
        return None


def qemu_alive():
    try:
        socket.create_connection(('127.0.0.1', HMP_PORT), timeout=1).close()
        return True
    except OSError:
        return False


def qemu_state():
    """QEMU's own run state over QMP: running, inmigrate (loading a snapshot), paused, postmigrate ..."""
    try:
        return Qmp().command('query-status')['status']
    except (OSError, ValueError, KeyError):
        return None


def status():
    text = log_text()
    reached = [name for name, rx in PHASES if re.search(rx, text)]
    started = vm_started_at()
    state = qemu_state() if qemu_alive() else None
    return {
        'running': qemu_alive(),
        'qemu_state': state,
        'launcher_alive': vm_pid() is not None,
        'panicked': bool(PANIC.search(text)),
        'phase': ('restoring' if state == 'inmigrate' else reached[-1] if reached else
                  ('starting' if qemu_alive() else 'stopped')),
        'seconds_since_start': round(time.time() - started) if started else None,
        'log': LOG,
    }


SNAPDIR = os.path.join(STATE, 'snapshots')
os.makedirs(SNAPDIR, exist_ok=True)


def snap_path(name):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name):
        raise ValueError('snapshot name: letters, digits, _ . - only')
    return os.path.join(SNAPDIR, name + '.gz')


def tool_vm_start(args):
    if qemu_alive():
        return text_result('QEMU is already running. Call vm_stop first for a fresh boot.\n' + json.dumps(status()))
    # The guest gets loader_return_after=SECONDS and hands control back to the loader after that much guest time since
    # boot. A saved state keeps the clock, so a short value makes every snapshot die soon after it is restored.
    seconds = int(args.get('seconds', 28800))
    env = dict(os.environ, HACKIOS_DIR=HACKIOS, HACKIOS_MCP_STATE=STATE, RUN_SECONDS=str(seconds))
    snapshot = args.get('snapshot')
    if snapshot:
        path = snap_path(snapshot)
        if not os.path.exists(path):
            return text_result(f'no snapshot {snapshot}; see vm_snapshots', error=True)
        env['HACKIOS_SNAPSHOT'] = path
    # a guest that can be saved needs a read-only ESP and no sound device; a restore needs the same command line
    if snapshot or args.get('saveable', True):
        env['HACKIOS_MIGRATABLE'] = '1'
    # slto_us raises the kernel's spinlock timeout: on a loaded host a vCPU thread can wait seconds for a core, and the
    # default timeout (about 2.5 s) then panics the guest ("Ticket spinlock timeout")
    cpus = int(args.get('cpus', 4))
    env['QEMU_SMP'] = str(cpus)
    env['CMDLINE'] = f"cpus={cpus} slto_us={int(args.get('spinlock_timeout_us', 60000000))}"
    log = open(LOG, 'wb')
    p = subprocess.Popen([os.path.join(HERE, 'launch.sh')], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    with open(PIDFILE, 'w') as f:
        f.write(f'{p.pid} {time.time()}\n')
    if snapshot:
        return text_result(f'Restoring snapshot {snapshot} (launcher pid {p.pid}). Use vm_status; the guest runs when the '
                           'restore has finished.')
    return text_result(f'Started (launcher pid {p.pid}). A boot takes several minutes under software emulation; '
                       'use vm_status or wait_for(phase).')


class Qmp:
    def __init__(self):
        self.sock = socket.create_connection(('127.0.0.1', 4445 + 10 * INSTANCE), timeout=10)
        self.file = self.sock.makefile('rw')
        self.file.readline()
        self.command('qmp_capabilities')

    def command(self, name, **arguments):
        self.file.write(json.dumps({'execute': name, 'arguments': arguments}) + '\n')
        self.file.flush()
        while True:
            reply = json.loads(self.file.readline())
            if 'return' in reply:
                return reply['return']
            if 'error' in reply:
                raise OSError(f'{name}: {reply["error"]["desc"]}')


def tool_vm_save(args):
    """Write the whole machine state (RAM, CPUs, devices) to a file. Needs a VM started with saveable=true."""
    if not qemu_alive():
        return text_result('QEMU is not running.', error=True)
    path = snap_path(args['name'])
    q = Qmp()
    blockers = q.command('query-migrate').get('blocked-reasons')
    if blockers:
        return text_result('This VM cannot be saved: ' + '; '.join(blockers) + '. Start it with vm_start (saveable=true).',
                           error=True)
    tmp = path + '.part'
    t0 = time.time()
    q.command('migrate', uri=f"exec:gzip -1 > '{tmp}'")
    timeout = min(int(args.get('timeout', 240)), 280)
    while time.time() - t0 < timeout:
        info = q.command('query-migrate')
        if info.get('status') == 'completed':
            os.replace(tmp, path)
            if args.get('resume', True):
                q.command('cont')
            mb = os.path.getsize(path) // (1 << 20)
            return text_result(f'saved {args["name"]}: {mb} MB in {round(time.time() - t0)} s'
                               + ('' if args.get('resume', True) else '; the VM is paused'))
        if info.get('status') in ('failed', 'cancelled'):
            return text_result('save failed: ' + json.dumps(info), error=True)
        time.sleep(2)
    return text_result('save did not finish in time: ' + json.dumps(q.command('query-migrate')), error=True)


def tool_vm_snapshots(args):
    out = []
    for f in sorted(os.listdir(SNAPDIR)):
        if f.endswith('.gz'):
            st = os.stat(os.path.join(SNAPDIR, f))
            out.append({'name': f[:-3], 'mb': st.st_size >> 20, 'saved': time.strftime('%F %T', time.localtime(st.st_mtime))})
    return text_result(json.dumps(out, indent=1))


def hmp(command, timeout=10):
    """Run one human monitor command and return its output. QEMU answers when it is done, so no fixed sleeps."""
    with socket.create_connection(('127.0.0.1', HMP_PORT), timeout=3) as s:
        s.settimeout(timeout)

        def until_prompt():
            buf = b''
            while not buf.endswith(b'(qemu) '):
                chunk = s.recv(4096)
                if not chunk:          # quit closes the connection
                    break
                buf += chunk
            return buf

        until_prompt()
        s.sendall(command.encode() + b'\n')
        return until_prompt().decode(errors='replace')


def tool_vm_stop(args):
    if qemu_alive():
        try:
            hmp('quit', 3)
        except OSError:
            pass
    pid = vm_pid()
    if pid:
        time.sleep(1)
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass
    return text_result(json.dumps(status()))


def tool_vm_status(args):
    return text_result(json.dumps(status(), indent=1))


def tool_wait_for(args):
    phase = args.get('phase', 'springboard_owns_screen')
    timeout = min(int(args.get('timeout', 240)), 280)
    rx = dict(PHASES).get(phase)
    if rx is None and phase != 'running':
        return text_result(f'unknown phase {phase}; choose from {[n for n, _ in PHASES] + ["running"]}', error=True)
    end = time.time() + timeout
    while time.time() < end:
        text = log_text()
        if phase == 'running':          # a restored snapshot: QEMU leaves "inmigrate" and the guest runs again
            if qemu_alive() and qemu_state() == 'running':
                return text_result(json.dumps(status()))
        elif re.search(rx, text):
            return text_result(json.dumps(status()))
        if PANIC.search(text) or (vm_started_at() and not qemu_alive() and time.time() - vm_started_at() > 30):
            return text_result('VM stopped or panicked before the phase.\n' + json.dumps(status()), error=True)
        time.sleep(3)
    return text_result(f'Timed out after {timeout} s.\n' + json.dumps(status()))


def png(width, height, rgb):
    chunk = png_chunk
    raw = b''.join(b'\0' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + b'\x08\x02\0\0\0')
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def png_chunk(tag, data):
    crc = zlib.crc32(tag + data) & 0xffffffff
    return len(data).to_bytes(4, 'big') + tag + data + crc.to_bytes(4, 'big')


def ppm_to_png(path, scale):
    """The kernel uses the top left 1125x2436 of the QEMU mode as the phone screen. QEMU pads each PPM row to 4 bytes."""
    d = open(path, 'rb').read()
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


def tool_screenshot(args):
    if not qemu_alive():
        return text_result('QEMU is not running.', error=True)
    scale = max(1, min(int(args.get('scale', 2)), 6))
    ppm = os.path.join(SHOTS, 'shot.ppm')
    if os.path.exists(ppm):
        os.unlink(ppm)
    hmp(f'screendump {ppm}')
    if not os.path.exists(ppm) or os.path.getsize(ppm) < 1000:
        return text_result('screendump produced no file', error=True)
    w, h, rgb = ppm_to_png(ppm, scale)
    os.unlink(ppm)
    data = png(w, h, rgb)
    open(os.path.join(SHOTS, 'last.png'), 'wb').write(data)
    black = not any(rgb)
    content = [{'type': 'image', 'data': base64.b64encode(data).decode(), 'mimeType': 'image/png'},
               {'type': 'text', 'text': f'{w}x{h} (scale 1/{scale} of 1125x2436)'
                + ('. All black: the display is probably off, call wake.' if black else '')}]
    return {'content': content}


def touch(*argv):
    env = dict(os.environ, QEMU_INSTANCE=str(INSTANCE))
    r = subprocess.run([sys.executable, '-I', os.path.join(HACKIOS, 'qemu', 'touch.py'), *map(str, argv)],
                       capture_output=True, text=True, env=env, timeout=60)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def unit(v, name):
    v = float(v)
    if not 0 <= v <= 1:
        raise ValueError(f'{name} must be a fraction of the screen between 0 and 1')
    return v


def tool_tap(args):
    ok, out = touch('tap', unit(args['x'], 'x'), unit(args['y'], 'y'))
    return text_result(out or 'tapped', error=not ok)


def tool_hold(args):
    ok, out = touch('hold', unit(args['x'], 'x'), unit(args['y'], 'y'), int(args.get('ms', 800)))
    return text_result(out or 'held', error=not ok)


def tool_swipe(args):
    ok, out = touch('swipe', unit(args['x0'], 'x0'), unit(args['y0'], 'y0'), unit(args['x1'], 'x1'),
                    unit(args['y1'], 'y1'), int(args.get('ms', 300)))
    return text_result(out or 'swiped', error=not ok)


KEYS = ('lock', 'home', 'volup', 'voldown')


def tool_key(args):
    name = args['name']
    if name not in KEYS:
        return text_result(f'key must be one of {KEYS}', error=True)
    ok, out = touch('key', name, int(args.get('ms', 800)))
    return text_result(out or f'pressed {name}', error=not ok)



# ---- on-screen keyboard -------------------------------------------------------------------------------------
# Key centres measured on a 562x1218 screenshot (scale 2) of Safari's URL keyboard: letters verified, the rest are
# the standard iPhone layout and are marked UNVERIFIED until a live run has typed them.
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
    for ch in text.lower():
        key = 'space' if ch == ' ' else ch
        out.append((kb_pos(key)[0], key))
    return out


def frac(px, py):
    return round(px / KB_W, 4), round(py / KB_H, 4)


def tap_ref(x, y, delay):
    """Tap a point given in pixels of the 562x1218 reference screenshot."""
    fx, fy = frac(x, y)
    ok, out = touch('tap', fx, fy)
    if not ok:
        raise OSError(out)
    time.sleep(delay)


def tool_type_text(args):
    """Tap the keys of the on-screen keyboard (it must be showing, e.g. after tapping Safari's address bar)."""
    text = args['text']
    delay = float(args.get('delay_s', 2.5))
    page = 'abc'                                           # the 123 key also switches back to abc, at the same place
    for want, key in plan_keys(text):
        if want != page:
            tap_ref(*KB_SPECIAL['123'], delay)
            page = want
        tap_ref(*kb_pos(key)[1:], delay)
    if args.get('submit'):
        if page != 'abc':
            tap_ref(*KB_SPECIAL['123'], delay)
        tap_ref(*KB_SPECIAL['go'], delay)
    return text_result(f'typed {len(text)} keys' + (' and pressed go' if args.get('submit') else ''))


def tool_open_url(args):
    """Safari must be in the foreground (tap its icon first). Taps the address bar, waits for the keyboard, types the
    address, presses go and (unless settle is false) waits until the page stops changing."""
    url = args['url'].strip()
    for prefix in ('https://', 'http://'):
        if url.startswith(prefix):
            url = url[len(prefix):]
    plan_keys(url)                                           # refuse an address with unknown keys before touching anything
    baseline = grab_frame()
    touch('tap', 0.5, 0.875)
    r = settle({'timeout': 150, 'stable_for': 4, 'wait_change': 100}, baseline)
    if not (r.get('changed') and r.get('stable')):
        return text_result('The keyboard did not appear after tapping the address bar. Is Safari in the foreground '
                           f'(tap its icon, check with a screenshot)? {json.dumps(r)}', error=True)
    baseline = grab_frame()
    result = tool_type_text({'text': url, 'submit': True, 'delay_s': args.get('delay_s', 2.5)})
    if args.get('settle', True):
        r = settle({'timeout': 240, 'stable_for': 15, 'wait_change': 90}, baseline)
        result['content'].append({'type': 'text', 'text': 'page: ' + json.dumps(r)})
    return result


def tool_tap_px(args):
    """Tap where you see something on a screenshot: x and y are pixels of that screenshot, scale as in the call."""
    scale = int(args.get('scale', 2))
    x, y = float(args['x']), float(args['y'])
    return tool_tap({'x': x * scale / 1125, 'y': y * scale / 2436})


def grab_frame(scale=6):
    """A checksum of a small copy of the screen, to tell if it changed."""
    ppm = os.path.join(SHOTS, 'settle.ppm')
    if os.path.exists(ppm):
        os.unlink(ppm)
    hmp(f'screendump {ppm}')
    if not os.path.exists(ppm) or os.path.getsize(ppm) < 1000:
        raise OSError('screendump produced no file')
    w, h, rgb = ppm_to_png(ppm, scale)
    os.unlink(ppm)
    return zlib.crc32(rgb[w * 3 * max(1, h // 25):])      # not the top 4%: the status bar clock ticks every minute


def settle(args, baseline=None):
    """Wait for the screen to change, then to stop changing. The guest answers an input only after 30-60 s
    (software drawing), so a still screen right after an input says nothing.
      wait_change   seconds to wait for the first change (default 90; 0 = do not wait for one)
      stable_for    seconds the screen must stay the same afterwards (default 6)
      timeout       the whole wait, at most 280 s
    `baseline` is a checksum taken before the input; without it the first frame is the baseline."""
    if not qemu_alive():
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


def tool_settle(args):
    r = settle(args)
    return text_result(json.dumps(r), error=r.get('reason') == 'QEMU is not running')


def tool_guest_process(args):
    """Is a process running in the guest? The kernel prints the threads of a named process into the console log
    (`ustacks: NAME[pid] N threads ...` or `ustacks: no process called NAME`); this asks and reads the answer."""
    name = args['name']
    if not re.fullmatch(r'[A-Za-z0-9_.+-]{1,40}', name):
        return text_result('name: letters, digits and _ . + - only', error=True)
    before = len(log_text())
    ok, out = touch('stacks', name)
    if not ok:
        return text_result(out, error=True)
    deadline = time.time() + float(args.get('timeout', 20))
    while time.time() < deadline:
        new = log_text()[before:]
        m = re.search(r'ustacks: (no process called %s|%s\[(\d+)\] (\d+) threads[^\n]*)' % (re.escape(name), re.escape(name)), new)
        if m:
            if m.group(2) is None:
                return text_result(json.dumps({'running': False, 'name': name}))
            return text_result(json.dumps({'running': True, 'name': name, 'pid': int(m.group(2)), 'threads': int(m.group(3))}))
        time.sleep(1)
    return text_result(f'no answer for {name} in the log (is the VM running and the log current?)', error=True)


def tool_wake(args):
    """The guest turns the display off after boot. A tap and then the home key woke it in testing."""
    touch('tap', 0.5, 0.5)
    time.sleep(3)
    ok, out = touch('key', 'home', 800)
    return text_result(out or 'sent tap + home key; take a screenshot to check', error=not ok)


def tool_wait(args):
    time.sleep(min(float(args.get('seconds', 1)), 60))
    return text_result('done')


def tool_log_tail(args):
    lines = log_text().splitlines()
    pat = args.get('pattern')
    if pat:
        rx = re.compile(pat)
        lines = [l for l in lines if rx.search(l)]
    n = min(int(args.get('lines', 40)), 400)
    return text_result('\n'.join(l[:300] for l in lines[-n:]))


def text_result(text, error=False):
    r = {'content': [{'type': 'text', 'text': text}]}
    if error:
        r['isError'] = True
    return r


def num(desc):
    return {'type': 'number', 'minimum': 0, 'maximum': 1, 'description': desc}



def tool_scroll(args):
    """Scroll the page: direction down moves the content up."""
    d = args.get('direction', 'down')
    amount = max(0.05, min(float(args.get('amount', 0.4)), 0.8))
    c = 0.5
    moves = {'down': (c, c + amount / 2, c, c - amount / 2), 'up': (c, c - amount / 2, c, c + amount / 2),
             'right': (c + amount / 2, c, c - amount / 2, c), 'left': (c - amount / 2, c, c + amount / 2, c)}
    if d not in moves:
        return text_result('direction: up, down, left or right', error=True)
    x0, y0, x1, y1 = moves[d]
    ok, out = touch('swipe', round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3), int(args.get('ms', 400)))
    return text_result(out or f'scrolled {d}', error=not ok)


def tool_back(args):
    """Back. method=button (default) taps Safari's toolbar back arrow; that only works while the toolbar is showing:
    after scrolling it collapses to the address bar and the first tap just brings it back. method=swipe is the left-edge
    gesture, which did not navigate in testing (it only revealed the toolbar)."""
    if args.get('method', 'button') == 'swipe':
        ok, out = touch('swipe', 0.0, 0.5, 0.65, 0.5, int(args.get('ms', 500)))
    else:
        ok, out = touch('tap', 0.075, 0.932)
    return text_result(out or 'back', error=not ok)


def tool_app_switcher_home(args):
    """Leave the app: swipe up from the bottom edge to the home screen."""
    ok, out = touch('swipe', 0.5, 0.985, 0.5, 0.4, 250)
    return text_result(out or 'swiped up from the bottom edge', error=not ok)


def acting(fn):
    """An input tool that takes settle=true: wait for the screen to change and settle (timeout, stable_for,
    wait_change as in the settle tool) and report how long that took."""
    def wrapper(args):
        use = bool(args.get('settle')) and qemu_alive()
        baseline = grab_frame() if use else None
        result = fn(args)
        if use and not result.get('isError'):
            result['content'].append({'type': 'text', 'text': 'settle: ' + json.dumps(settle(args, baseline))})
        return result
    wrapper.acting = True
    return wrapper


SETTLE_PROPS = {'settle': {'type': 'boolean', 'description': 'wait for the screen to change and settle before returning'},
                'timeout': {'type': 'number'}, 'stable_for': {'type': 'number'}, 'wait_change': {'type': 'number'}}


TOOLS = {
    'vm_start': (tool_vm_start, 'Boot HackiOS in QEMU in the background. Takes about 10 minutes under software emulation.',
                 {'seconds': {'type': 'integer', 'description': 'session length in guest seconds, default 28800 (8 h); a snapshot cannot live longer than this minus its age when saved'},
                  'cpus': {'type': 'integer', 'description': 'guest CPUs, default 4'},
                  'spinlock_timeout_us': {'type': 'integer', 'description': 'kernel spinlock timeout, default 60000000'},
                  'snapshot': {'type': 'string', 'description': 'restore this snapshot instead of booting'},
                  'saveable': {'type': 'boolean', 'description': 'default true: read-only ESP, no sound device, so vm_save works'}}, []),
    'vm_save': (tool_vm_save, 'Save the whole machine state to a named snapshot (takes a minute or more, files are GB). '
                'Restore with vm_start(snapshot=name). Only works for a VM started with saveable=true.',
                {'name': {'type': 'string'}, 'resume': {'type': 'boolean'}, 'timeout': {'type': 'integer'}}, ['name']),
    'vm_snapshots': (tool_vm_snapshots, 'List saved snapshots.', {}, []),
    'vm_stop': (tool_vm_stop, 'Stop QEMU.', {}, []),
    'vm_status': (tool_vm_status, 'Is QEMU running, did it panic, and which boot phase was reached.', {}, []),
    'wait_for': (tool_wait_for, 'Block until a boot phase is reached (kernel, launchd, backboardd_ready, '
                 'springboard_owns_screen) or the timeout (max 280 s) ends.',
                 {'phase': {'type': 'string', 'description': 'also: running (a restored snapshot is live)'},
                  'timeout': {'type': 'integer'}}, []),
    'screenshot': (tool_screenshot, 'PNG of the phone screen (1125x2436, scaled down by `scale`, default 2).',
                   {'scale': {'type': 'integer', 'minimum': 1, 'maximum': 6}}, []),
    'tap': (acting(tool_tap), 'Tap. x and y are fractions of the screen, 0,0 top left.', {'x': num('x'), 'y': num('y')}, ['x', 'y']),
    'hold': (acting(tool_hold), 'Touch and hold.', {'x': num('x'), 'y': num('y'), 'ms': {'type': 'integer'}}, ['x', 'y']),
    'swipe': (acting(tool_swipe), 'Swipe. Swipe up from y=0.985 to open or leave an app; from the left edge for back.',
              {'x0': num('x0'), 'y0': num('y0'), 'x1': num('x1'), 'y1': num('y1'), 'ms': {'type': 'integer'}},
              ['x0', 'y0', 'x1', 'y1']),
    'key': (acting(tool_key), 'Press a hardware button: lock, home, volup, voldown.',
            {'name': {'type': 'string', 'enum': list(KEYS)}, 'ms': {'type': 'integer'}}, ['name']),
    'tap_px': (acting(tool_tap_px), 'Tap a point given in pixels of a screenshot you took (set scale to the same value as the screenshot).',
               {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'scale': {'type': 'integer', 'minimum': 1, 'maximum': 6}}, ['x', 'y']),
    'type_text': (acting(tool_type_text), 'Type by tapping the on-screen keyboard (must be showing). Lower-case letters, digits, space and . - / : ( ) ; $ & @ " '
                  'are known; set submit to press go. Keys are tapped slowly (delay_s, default 2.5).',
                  {'text': {'type': 'string'}, 'submit': {'type': 'boolean'}, 'delay_s': {'type': 'number'}}, ['text']),
    'scroll': (acting(tool_scroll), 'Scroll the page (direction up/down/left/right, amount as a fraction of the screen, default 0.4).',
               {'direction': {'type': 'string', 'enum': ['up', 'down', 'left', 'right']}, 'amount': {'type': 'number'}}, []),
    'back': (acting(tool_back), 'Back in Safari: taps the toolbar back arrow (the toolbar must be showing; scroll up a little or tap the bottom bar first). '
                'method=swipe uses the left-edge gesture instead.',
                {'method': {'type': 'string', 'enum': ['button', 'swipe']}, 'ms': {'type': 'integer'}}, []),
    'home_screen': (acting(tool_app_switcher_home), 'Leave the app: swipe up from the bottom edge.', {}, []),
    'open_url': (tool_open_url, 'In Safari: tap the address bar, type the address, press go. Slow: about 3 s per character.',
                 {'url': {'type': 'string'}, 'delay_s': {'type': 'number'}}, ['url']),
    'settle': (tool_settle, 'Wait until the screen stops changing (two frames equal for stable_for seconds) or the timeout ends.',
               {'timeout': {'type': 'number'}, 'stable_for': {'type': 'number'}}, []),
    'guest_process': (tool_guest_process, 'Is a process running in the guest (by name, e.g. MobileSafari, webinspectord)? Returns pid and thread count.',
                      {'name': {'type': 'string'}, 'timeout': {'type': 'number'}}, ['name']),
    'wake': (acting(tool_wake), 'Wake the display (it turns off after boot): a tap and then the home key.', {}, []),
    'wait': (tool_wait, 'Sleep up to 60 s (the guest is slow; animations take time).', {'seconds': {'type': 'number'}}, []),
    'log_tail': (tool_log_tail, 'Last lines of the guest console log, optionally filtered by a regex.',
                 {'lines': {'type': 'integer'}, 'pattern': {'type': 'string'}}, []),
}


def handle(msg):
    method = msg.get('method')
    mid = msg.get('id')
    if method == 'initialize':
        return {'protocolVersion': msg['params'].get('protocolVersion', '2024-11-05'),
                'capabilities': {'tools': {}}, 'serverInfo': {'name': 'hackios-safari', 'version': '0.1.0'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [{'name': n, 'description': d,
                           'inputSchema': {'type': 'object',
                                           'properties': {**p, **(SETTLE_PROPS if getattr(f, 'acting', False) else {})},
                                           'required': r}}
                          for n, (f, d, p, r) in TOOLS.items()]}
    if method == 'tools/call':
        name = msg['params']['name']
        if name not in TOOLS:
            raise KeyError(name)
        try:
            return TOOLS[name][0](msg['params'].get('arguments') or {})
        except (KeyError, ValueError, TypeError, OSError, subprocess.SubprocessError) as e:
            return text_result(f'{name}: {type(e).__name__}: {e}', error=True)
    if mid is None:
        return None  # notification
    raise LookupError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        try:
            result = handle(msg)
        except LookupError as e:
            err = {'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': -32601, 'message': f'unknown method {e}'}}
            sys.stdout.write(json.dumps(err) + '\n')
            sys.stdout.flush()
            continue
        if msg.get('id') is not None and result is not None:
            sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
