#!/usr/bin/env python3
"""Offline tests: the JSON-RPC surface and the PPM -> PNG conversion. No VM needed."""
import json, os, struct, subprocess, sys, tempfile, zlib
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, '..', 'server.py')
sys.path.insert(0, os.path.join(HERE, '..'))
os.environ['HACKIOS_MCP_STATE'] = tempfile.mkdtemp()
os.environ['QEMU_INSTANCE'] = '9'          # ports 4534/4535: never the VM of a live test
import server


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


def test_rpc():
    p = subprocess.Popen([sys.executable, '-I', SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env=dict(os.environ))
    r = rpc(p, 1, 'initialize', {'protocolVersion': '2025-03-26'})
    assert r['result']['protocolVersion'] == '2025-03-26' and 'tools' in r['result']['capabilities']
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n'); p.stdin.flush()
    names = {t['name'] for t in rpc(p, 2, 'tools/list')['result']['tools']}
    assert {'vm_start', 'screenshot', 'tap', 'swipe', 'key', 'wake', 'wait_for', 'log_tail'} <= names, names
    r = rpc(p, 3, 'tools/call', {'name': 'screenshot', 'arguments': {}})['result']
    assert r['isError'] and 'not running' in r['content'][0]['text']
    r = rpc(p, 4, 'tools/call', {'name': 'tap', 'arguments': {'x': 2, 'y': 0.5}})['result']
    assert r['isError'] and 'fraction' in r['content'][0]['text']
    r = rpc(p, 5, 'tools/call', {'name': 'key', 'arguments': {'name': 'power'}})['result']
    assert r['isError']
    r = json.loads(rpc(p, 6, 'tools/call', {'name': 'vm_status'})['result']['content'][0]['text'])
    assert r['running'] is False and r['phase'] == 'stopped', r
    tools = {t['name']: t for t in rpc(p, 8, 'tools/list')['result']['tools']}
    assert 'settle' in tools['tap']['inputSchema']['properties'] and 'settle' not in tools['screenshot']['inputSchema']['properties']
    assert 'scroll' in tools and 'back' in tools and 'home_screen' in tools
    assert 'error' in rpc(p, 7, 'nope')
    p.stdin.close(); p.wait(timeout=5)


def test_ppm_to_png():
    w, h = 1125, 2436
    stride = (w * 3 + 3) // 4 * 4            # QEMU pads rows to 4 bytes: 3376 for 3375
    pix = bytearray(stride * h)
    for y in range(h):
        for x in (0, 7, 1124):               # marker pixels with a value per row and column
            pix[y * stride + x * 3:y * stride + x * 3 + 3] = bytes((y % 251 + 1, x % 251 + 1, 9))
    path = os.path.join(os.environ['HACKIOS_MCP_STATE'], 't.ppm')
    open(path, 'wb').write(f'P6\n{w} {h}\n255\n'.encode() + bytes(pix))
    ow, oh, rgb = server.ppm_to_png(path, 1)
    assert (ow, oh) == (1125, 2436)
    w2, h2, rows = decode_png(server.png(ow, oh, rgb))
    assert (w2, h2) == (1125, 2436)
    assert rows[100][7 * 3:7 * 3 + 3] == bytes((101, 8, 9)) and rows[2435][1124 * 3:1124 * 3 + 3] == bytes((2435 % 251 + 1, 1124 % 251 + 1, 9))
    ow, oh, rgb = server.ppm_to_png(path, 2)            # every second pixel and row
    assert (ow, oh) == (562, 1218)
    _, _, rows = decode_png(server.png(ow, oh, rgb))
    assert rows[50][0:3] == bytes((100 % 251 + 1, 1, 9)), rows[50][0:3]


class FakeMonitor:
    """A stand-in for QEMU's human monitor on the test instance's port: answers `screendump FILE` with the next frame."""
    def __init__(self, frames):
        import socketserver, threading
        outer = self
        self.frames, self.index, self.dumps = frames, 0, 0

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"QEMU 11.1.1 monitor - type 'help' for more information\r\n(qemu) ")
                for line in self.rfile:
                    cmd = line.decode().strip()
                    if cmd.startswith('screendump '):
                        shade = outer.frames[min(outer.index, len(outer.frames) - 1)]
                        outer.index += 1
                        outer.dumps += 1
                        w, h = 32, 32
                        open(cmd.split(' ', 1)[1], 'wb').write(f'P6\n{w} {h}\n255\n'.encode() + bytes([shade]) * (w * h * 3 + h * 4))
                    self.wfile.write(b'\r\n(qemu) ')

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
        self.server = Server(('127.0.0.1', server.HMP_PORT), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_hmp_and_settle():
    # frames: 5 5 | 5 5 5 9 9 9 9 9 ... : the screen changes late, then stays the same
    mon = FakeMonitor([5, 5, 5, 5, 9, 9, 9, 9, 9, 9, 9, 9])
    try:
        out = server.hmp('info status')
        assert out.endswith('(qemu) '), out
        before = server.grab_frame()
        assert isinstance(before, int)
        r = server.settle({'timeout': 30, 'stable_for': 2, 'wait_change': 20}, baseline=before)
        assert r['stable'] and r['changed'], r                 # saw the change from 5 to 9, then 2 s of 9
        mon.index = 99                                          # the screen never changes now
        r = server.settle({'timeout': 30, 'stable_for': 1, 'wait_change': 3})
        assert r['stable'] and r['changed'] is False, r         # gave up waiting for a change after 3 s
    finally:
        mon.close()


def test_keyboard():
    # verified positions (562x1218 reference screenshot): n o r b a n . s e
    for key, (px, py) in {'n': (394, 985), 'o': (478, 825), 'r': (196, 825), 'b': (338, 985), 'a': (56, 905),
                          '.': (429, 1067), 's': (112, 905), 'e': (140, 825), 'go': (510, 1067)}.items():
        page, x, y = server.kb_pos(key)
        assert page == 'abc' and abs(x - px) <= 3 and abs(y - py) <= 3, (key, x, y, px, py)
    assert server.plan_keys('Example.com 1') == [('abc', c) for c in 'example.com'] + [('abc', 'space'), ('123', '1')]
    for ch in 'abcdefghijklmnopqrstuvwxyz0123456789 .-/:':
        _, x, y = server.kb_pos('space' if ch == ' ' else ch)
        assert 0 < x < server.KB_W and 0 < y < server.KB_H, ch
    try:
        server.kb_pos('~'); assert False
    except ValueError:
        pass
    assert server.frac(281, 609) == (0.5, 0.5)


if __name__ == '__main__':
    test_rpc(); print('rpc ok')
    test_ppm_to_png(); print('ppm->png ok')
    test_keyboard(); print('keyboard ok')
    test_hmp_and_settle(); print('hmp + settle ok')
