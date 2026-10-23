#!/usr/bin/env python3
"""Live test of save and restore: boot, wake, open Safari, vm_save, vm_stop, vm_start(snapshot), screenshot, timings."""
import base64, json, os, subprocess, sys, time
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
p = subprocess.Popen([sys.executable, '-I', os.path.join(os.path.dirname(__file__), '..', 'server.py')],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
n = 0
def say(*a): print(time.strftime('%T'), *a, flush=True)
def call(tool, **args):
    global n; n += 1
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': n, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}) + '\n'); p.stdin.flush()
    r = json.loads(p.stdout.readline())['result']
    for c in r['content']:
        if c['type'] == 'image':
            f = os.path.join(OUT, f'{n:02d}-{tool}.png'); open(f, 'wb').write(base64.b64decode(c['data'])); say(tool, '->', f)
        else:
            say(tool, ('ERROR ' if r.get('isError') else '') + c['text'][:300].replace('\n', ' | '))
    return r
def text(r): return r['content'][0]['text']
p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}}) + '\n'); p.stdin.flush(); p.stdout.readline()
call('vm_stop')
t0 = time.time(); call('vm_start', seconds=1700)
ok = False
for _ in range(6):
    r = call('wait_for', phase='springboard_owns_screen', timeout=280)
    if r.get('isError'): break
    if '"phase": "springboard_owns_screen"' in text(r): ok = True; break
say('boot to springboard:', round(time.time() - t0), 's', 'OK' if ok else 'FAILED')
if ok:
    call('wake'); time.sleep(6); call('screenshot', scale=4)
    call('tap', x=0.38, y=0.93); time.sleep(40); call('screenshot', scale=4)
    t1 = time.time(); r = call('vm_save', name='safari', timeout=280); say('save took', round(time.time() - t1), 's')
    if not r.get('isError'):
        call('vm_stop'); time.sleep(5)
        t2 = time.time(); call('vm_start', snapshot='safari', seconds=900)
        for _ in range(60):
            time.sleep(5)
            st = json.loads(text(call('vm_status')))
            if st['running'] and not st['panicked']:
                r = call('screenshot', scale=4)
                if 'All black' not in r['content'][1]['text']: break
        say('restore to first non-black screenshot:', round(time.time() - t2), 's')
        time.sleep(20); call('screenshot', scale=4)
call('vm_stop')
p.stdin.close()
