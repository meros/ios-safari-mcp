#!/usr/bin/env python3
"""Continue against a RUNNING saveable VM: wait for Safari to draw, vm_save, vm_stop, restore, time it."""
import base64, json, os, subprocess, sys, time
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
p = subprocess.Popen([sys.executable, '-I', os.path.join(os.path.dirname(__file__), '..', 'server.py')],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
n = 100
def say(*a): print(time.strftime('%T'), *a, flush=True)
def call(tool, **args):
    global n; n += 1
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': n, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}) + '\n'); p.stdin.flush()
    r = json.loads(p.stdout.readline())['result']
    for c in r['content']:
        if c['type'] == 'image':
            f = os.path.join(OUT, f'{n:03d}-{tool}.png'); open(f, 'wb').write(base64.b64decode(c['data'])); say(tool, '->', f)
        else:
            say(tool, ('ERROR ' if r.get('isError') else '') + c['text'][:300].replace('\n', ' | '))
    return r
def text(r): return r['content'][0]['text']
p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}}) + '\n'); p.stdin.flush(); p.stdout.readline()
for i in range(10):                      # Safari draws slowly under software rendering
    time.sleep(30)
    r = call('screenshot', scale=4)
    if 'All black' not in r['content'][1]['text']:
        # a non-black frame; keep looking until the home screen is gone (Safari up) or 10 rounds pass
        pass
t1 = time.time(); r = call('vm_save', name='safari', timeout=280); say('save took', round(time.time() - t1), 's')
if not r.get('isError'):
    call('vm_stop'); time.sleep(5)
    t2 = time.time(); call('vm_start', snapshot='safari', seconds=900)
    first = None
    for _ in range(80):
        time.sleep(5)
        st = json.loads(text(call('vm_status')))
        if st['panicked']: say('PANIC after restore'); break
        if st['running']:
            r = call('screenshot', scale=4)
            if 'All black' not in r['content'][1]['text']:
                first = round(time.time() - t2); break
    say('restore to first non-black screenshot:', first, 's')
    time.sleep(30); call('screenshot', scale=4)
call('vm_stop')
p.stdin.close()
