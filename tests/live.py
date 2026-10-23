#!/usr/bin/env python3
"""Live test against a real VM: boot, wake, screenshot, open Safari by tapping its icon, stop. Takes ~15 min."""
import base64, json, os, subprocess, sys, time
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
p = subprocess.Popen([sys.executable, '-I', os.path.join(os.path.dirname(__file__), '..', 'server.py')],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
n = 0
def call(name, **args):
    global n; n += 1
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': n, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}) + '\n'); p.stdin.flush()
    r = json.loads(p.stdout.readline())['result']
    for c in r['content']:
        if c['type'] == 'image':
            f = os.path.join(OUT, f'{n:02d}-{name}.png'); open(f, 'wb').write(base64.b64decode(c['data'])); print(time.strftime('%T'), name, '->', f)
        else:
            print(time.strftime('%T'), name, ('ERROR ' if r.get('isError') else '') + c['text'][:300].replace('\n', ' | '))
    return r
p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}}) + '\n'); p.stdin.flush(); p.stdout.readline()
t0 = time.time()
call('vm_start', seconds=1500)
r = None
for _ in range(5):
    r = call('wait_for', phase='springboard_owns_screen', timeout=280)
    if 'springboard_owns_screen' in r['content'][0]['text'] and not r.get('isError') and '"phase": "springboard_owns_screen"' in r['content'][0]['text']:
        break
    if r.get('isError'):
        break
print('boot to springboard:', round(time.time() - t0), 's')
if not r.get('isError'):
    call('screenshot', scale=3)
    call('wake'); time.sleep(8)
    call('screenshot', scale=3)
    call('tap', x=0.38, y=0.93); time.sleep(45)      # the Safari icon in the dock
    call('screenshot', scale=3)
call('log_tail', lines=5, pattern='MobileSafari')
call('vm_stop')
p.stdin.close()
