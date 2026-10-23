#!/usr/bin/env python3
"""Time the inspector against the live guest: attach, evaluate, navigate, console, network.  usage: wi_latency.py [PORT]"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import server  # noqa: E402

t0 = time.time()
if len(sys.argv) > 1:
    server.WI_PORT = int(sys.argv[1])
insp = server.inspector()   # also ends the connections that an earlier run left behind
t = time.time()
s = insp.session()
print(f'connect + list + attach (3 domains): {time.time() - t:.1f} s')
for i in range(5):
    t = time.time()
    v = s.evaluate('1 + 1')
    print(f'evaluate 1 + 1 -> {v}: {(time.time() - t) * 1000:.0f} ms')
t = time.time()
s.evaluate('console.log("hello from the inspector test"); console.error("an error line")')
time.sleep(1)
msgs = [m['params']['message']['text'] for m in s.events if m.get('method') == 'Console.messageAdded']
print('console:', msgs[-3:], f'({(time.time() - t) * 1000:.0f} ms)')
t = time.time()
s.send('Page.navigate', {'url': 'http://example.org/'})
for _ in range(60):
    time.sleep(1)
    try:
        url = s.evaluate('location.href + " " + document.readyState', timeout=20)
    except Exception as e:  # noqa: BLE001
        url = f'({e})'
    if 'example.org' in str(url) and 'complete' in str(url):
        break
print(f'navigate to example.org, until loaded: {time.time() - t:.1f} s ->', url)
net = [m['params']['request']['url'] for m in s.events if m.get('method') == 'Network.requestWillBeSent']
print('network requests seen:', net[-5:])
insp.close()
print(f'total {time.time() - t0:.1f} s')
