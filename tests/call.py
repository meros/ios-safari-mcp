#!/usr/bin/env python3
"""One-shot MCP client: call.py TOOL ['{"arg": 1}'] [OUT.png]. Starts server.py, calls the tool, prints the text, saves an image."""
import base64, json, os, subprocess, sys
tool = sys.argv[1]
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
out = sys.argv[3] if len(sys.argv) > 3 else '/tmp/hackios-call.png'
p = subprocess.Popen([sys.executable, '-I', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'server.py')],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def rpc(i, method, params):
    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': i, 'method': method, 'params': params}) + '\n'); p.stdin.flush()
    return json.loads(p.stdout.readline())
rpc(0, 'initialize', {})
r = rpc(1, 'tools/call', {'name': tool, 'arguments': args})['result']
for c in r['content']:
    if c['type'] == 'image':
        open(out, 'wb').write(base64.b64decode(c['data'])); print('image ->', out)
    else:
        print(('ERROR: ' if r.get('isError') else '') + c['text'])
p.stdin.close()
