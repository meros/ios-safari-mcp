#!/usr/bin/env python3
"""A live check of the tools against a running VM, through one long-lived MCP session, with timings.

    1. ios-safari-mcp's VM must be running (vm_start, then wait_for springboard_owns_screen) on an image from build-image.
    2. python3 tests/live/smoke.py [URL] [FIELD_SELECTOR]

It opens URL (default https://example.com), reads the title and DOM, checks the console and network log, and, when a
FIELD_SELECTOR is given, runs keyboard_check on that field. It prints one line per step and exits non-zero on a failure.
"""
import json
import os
import subprocess
import sys
import time

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'bin', 'ios-safari-mcp')
url = sys.argv[1] if len(sys.argv) > 1 else 'https://example.com'
field = sys.argv[2] if len(sys.argv) > 2 else None

p = subprocess.Popen([sys.executable, '-I', CLI, 'serve'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
ids = iter(range(1, 10 ** 6))
failures = []


def rpc(method, params):
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': next(ids), 'method': method, 'params': params}) + '\n')
    p.stdin.flush()
    return json.loads(p.stdout.readline())['result']


def step(name, tool, args=None, check=None):
    t = time.time()
    r = rpc('tools/call', {'name': tool, 'arguments': args or {}})
    took = time.time() - t
    body = next((c['text'] for c in r['content'] if c['type'] == 'text'), '')
    ok = not r.get('isError') and (check is None or check(body))
    print(f'{"ok  " if ok else "FAIL"} {name:<28} {took:6.1f} s  {body[:110].replace(chr(10), " ")}')
    if not ok:
        failures.append(name)
    return body


rpc('initialize', {})
status = json.loads(step('vm_status', 'vm_status', check=lambda b: json.loads(b)['running']))
step('safari_ready', 'safari_ready', {'url': url, 'timeout': 240})
step('inspector_pages', 'inspector_pages', check=lambda b: 'WIRTypeWebPage' in b)
step('js title', 'js', {'expression': 'document.title'}, check=lambda b: len(b) > 2)
step('dom h1', 'dom', {'selector': 'h1', 'what': 'text'}, check=lambda b: '"count"' in b)
step('js 1 + 1', 'js', {'expression': '1 + 1'}, check=lambda b: b.strip() == '2')
step('navigate (about:blank)', 'navigate', {'url': 'about:blank'})
step('open_url', 'open_url', {'url': url})
step('network_log', 'network_log', check=lambda b: 'status' in b)
step('console_messages', 'console_messages')
step('screenshot', 'screenshot')
if field:
    step('keyboard_state', 'keyboard_state', check=lambda b: 'keyboardVisible' in b)
    step('keyboard_check', 'keyboard_check', {'selector': field}, check=lambda b: '"verdict"' in b)
p.stdin.close()
print('\nFAILED: ' + ', '.join(failures) if failures else '\nall steps passed')
sys.exit(1 if failures else 0)
