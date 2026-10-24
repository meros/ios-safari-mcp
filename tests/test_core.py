"""The JSON-RPC surface, the screen code and the VM helpers: no VM needed."""
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from fakes import FakeMonitor
from ios_safari_mcp import config, keyboard, mcp, registry, screen, vm

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, '..', 'bin', 'ios-safari-mcp')


def rpc(proc, mid, method, params=None):
    proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params or {}}) + '\n')
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def decode_png(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    i, idat, w, h = 8, b'', 0, 0
    while i < len(data):
        n, tag = struct.unpack('>I4s', data[i:i + 8])
        body = data[i + 8:i + 8 + n]
        assert struct.unpack('>I', data[i + 8 + n:i + 12 + n])[0] == zlib.crc32(tag + body) & 0xffffffff, tag
        if tag == b'IHDR':
            w, h = struct.unpack('>II', body[:8])
        if tag == b'IDAT':
            idat += body
        i += 12 + n
    raw = zlib.decompress(idat)
    return w, h, [raw[y * (w * 3 + 1) + 1:(y + 1) * (w * 3 + 1)] for y in range(h)]


def test_mcp_over_stdio():
    p = subprocess.Popen([sys.executable, '-I', CLI, 'serve'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env=dict(os.environ))
    try:
        r = rpc(p, 1, 'initialize', {'protocolVersion': '2025-03-26'})
        assert r['result']['protocolVersion'] == '2025-03-26' and 'tools' in r['result']['capabilities']
        assert r['result']['serverInfo']['name'] == 'ios-safari-mcp'
        p.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        p.stdin.flush()
        tools = {t['name']: t for t in rpc(p, 2, 'tools/list')['result']['tools']}
        assert {'vm_start', 'screenshot', 'tap', 'js', 'dom', 'keyboard_state', 'keyboard_show', 'keyboard_hide',
                'keyboard_check', 'safari_ready', 'open_url'} <= set(tools)
        r = rpc(p, 3, 'tools/call', {'name': 'screenshot', 'arguments': {}})['result']
        assert r['isError'] and 'not running' in r['content'][0]['text']
        r = rpc(p, 4, 'tools/call', {'name': 'tap', 'arguments': {'x': 2, 'y': 0.5}})['result']
        assert r['isError'] and 'fraction' in r['content'][0]['text']
        r = rpc(p, 5, 'tools/call', {'name': 'key', 'arguments': {'name': 'power'}})['result']
        assert r['isError']
        st = json.loads(rpc(p, 6, 'tools/call', {'name': 'vm_status'})['result']['content'][0]['text'])
        assert st['running'] is False and st['phase'] == 'stopped' and st['touchscreen'] == 'ok', st
        assert 'settle' in tools['tap']['inputSchema']['properties'] and 'settle' not in tools['screenshot']['inputSchema']['properties']
        assert 'error' in rpc(p, 7, 'nope')
        assert 'error' in rpc(p, 8, 'tools/call', {'name': 'no_such_tool', 'arguments': {}})
    finally:
        p.stdin.close()
        p.wait(timeout=5)


def test_every_tool_has_a_description_and_a_schema():
    assert len(registry.TOOLS) >= 40
    for t in registry.listing():
        assert t['description'] and t['inputSchema']['type'] == 'object', t['name']
        for req in t['inputSchema']['required']:
            assert req in t['inputSchema']['properties'], (t['name'], req)


def test_ppm_to_png():
    w, h = 1125, 2436
    stride = (w * 3 + 3) // 4 * 4            # QEMU pads rows to 4 bytes: 3376 for 3375
    pix = bytearray(stride * h)
    for y in range(h):
        for x in (0, 7, 1124):               # marker pixels with a value per row and column
            pix[y * stride + x * 3:y * stride + x * 3 + 3] = bytes((y % 251 + 1, x % 251 + 1, 9))
    path = os.path.join(config.STATE, 't.ppm')
    Path(path).write_bytes(f'P6\n{w} {h}\n255\n'.encode() + bytes(pix))
    ow, oh, rgb = screen.ppm_to_rgb(path, 1)
    assert (ow, oh) == (1125, 2436)
    w2, h2, rows = decode_png(screen.png(ow, oh, rgb))
    assert (w2, h2) == (1125, 2436)
    assert rows[100][7 * 3:7 * 3 + 3] == bytes((101, 8, 9))
    assert rows[2435][1124 * 3:1124 * 3 + 3] == bytes((2435 % 251 + 1, 1124 % 251 + 1, 9))
    ow, oh, rgb = screen.ppm_to_rgb(path, 2)            # every second pixel and row
    assert (ow, oh) == (562, 1218)
    _, _, rows = decode_png(screen.png(ow, oh, rgb))
    assert rows[50][0:3] == bytes((100 % 251 + 1, 1, 9))


@pytest.fixture
def monitor():
    config.ensure_dirs()
    made = []

    def make(frames):
        m = FakeMonitor(config.HMP_PORT, frames)
        made.append(m)
        return m
    yield make
    for m in made:
        m.close()


def test_hmp_and_settle(monitor):
    from ios_safari_mcp import qemu
    # frames: 5 5 5 5 | 9 9 9 ...: the screen changes late, then stays the same
    mon = monitor([5, 5, 5, 5, 9, 9, 9, 9, 9, 9, 9, 9])
    assert qemu.hmp('info status').endswith('(qemu) ')
    before = screen.grab_frame()
    assert isinstance(before, int)
    r = screen.settle({'timeout': 30, 'stable_for': 2, 'wait_change': 20}, baseline=before)
    assert r['stable'] and r['changed'], r                 # saw the change from 5 to 9, then 2 s of 9
    mon.index = 99                                          # the screen never changes now
    r = screen.settle({'timeout': 30, 'stable_for': 1, 'wait_change': 3})
    assert r['stable'] and r['changed'] is False, r         # gave up waiting for a change after 3 s


def test_keyboard_layout():
    # verified positions (562x1218 reference screenshot): n o r b a n . s e
    for key, (px, py) in {'n': (394, 985), 'o': (478, 825), 'r': (196, 825), 'b': (338, 985), 'a': (56, 905),
                          '.': (429, 1067), 's': (112, 905), 'e': (140, 825), 'go': (510, 1067)}.items():
        page, x, y = keyboard.kb_pos(key)
        assert page == 'abc' and abs(x - px) <= 3 and abs(y - py) <= 3, (key, x, y, px, py)
    assert keyboard.plan_keys('example.com 1') == [('abc', c) for c in 'example.com'] + [('abc', 'space'), ('123', '1')]
    with pytest.raises(ValueError, match='upper case'):
        keyboard.plan_keys('Example.com')
    for ch in 'abcdefghijklmnopqrstuvwxyz0123456789 .-/:':
        _, x, y = keyboard.kb_pos('space' if ch == ' ' else ch)
        assert 0 < x < keyboard.KB_W and 0 < y < keyboard.KB_H, ch
    with pytest.raises(ValueError):
        keyboard.kb_pos('~')
    assert keyboard.frac(281, 609) == (0.5, 0.5)


def write_log(text):
    os.makedirs(config.STATE, exist_ok=True)
    Path(config.LOG).write_text(text)


def test_status_reads_the_log():
    write_log('Security policy loaded\ncom.apple.xpc.launchd Successfully spawned x\n')
    st = vm.status()
    assert st['phase'] == 'stopped' and st['running'] is False and st['touchscreen'] == 'ok' and not st['panicked']
    write_log('panic(cpu 1 caller 0x1): Ticket spinlock timeout\n')
    assert vm.status()['panicked']
    write_log('[TouchEvents] ignoring digitizer event for display <main> from unsupported service\n')
    assert vm.status()['touchscreen'].startswith('not recognized')


def test_a_restored_guest_is_running_not_starting(monkeypatch):
    from ios_safari_mcp import qemu
    write_log('')
    Path(config.PIDFILE).write_text(f'{os.getpid()} 1000.0 safari-ready\n')
    monkeypatch.setattr(qemu, 'alive', lambda: True)
    monkeypatch.setattr(qemu, 'run_state', lambda: 'running')
    st = vm.status()
    assert st['phase'] == 'running' and st['restored_from'] == 'safari-ready'
    Path(config.PIDFILE).write_text(f'{os.getpid()} 1000.0 \n')
    assert vm.status()['phase'] == 'starting'


def test_log_tail_filters_and_truncates():
    write_log('\n'.join(f'line {i}' for i in range(100)) + '\n' + 'x' * 500 + '\n')
    assert vm.log_tail(3).splitlines()[-2:] == ['line 99', 'x' * 300]
    assert vm.log_tail(50, r'line 4\d').splitlines() == [f'line {i}' for i in range(40, 50)]


def test_snapshot_names_are_checked():
    with pytest.raises(Exception, match='snapshot name'):
        vm.snap_path('../evil')
    assert vm.snap_path('safari-ready').endswith('safari-ready.gz')


def test_mcp_handle_unknown_method():
    with pytest.raises(LookupError):
        mcp.handle({'id': 1, 'method': 'nope'})
    assert mcp.handle({'method': 'notifications/initialized'}) is None
