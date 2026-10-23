#!/usr/bin/env python3
"""Talk to the guest's Web Inspector through guest/wi_bridge (host port 27753 by default) and list what it reports.

usage: wi_probe.py [PORT] [SECONDS]
"""
import plistlib
import socket
import struct
import sys
import time
import uuid

port = int(sys.argv[1]) if len(sys.argv) > 1 else 27753
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 20
conn_id = str(uuid.uuid4()).upper()


def send(sock, selector, argument=None):
    body = plistlib.dumps({'__selector': selector, '__argument': argument or {}}, fmt=plistlib.FMT_BINARY)
    sock.sendall(struct.pack('>I', len(body)) + body)


def recv(sock):
    head = b''
    while len(head) < 4:
        chunk = sock.recv(4 - len(head))
        if not chunk:
            raise EOFError('closed')
        head += chunk
    n = struct.unpack('>I', head)[0]
    body = b''
    while len(body) < n:
        chunk = sock.recv(n - len(body))
        if not chunk:
            raise EOFError('closed')
        body += chunk
    return plistlib.loads(body)


s = socket.create_connection(('127.0.0.1', port), timeout=seconds)
print('connected')
send(s, '_rpc_reportIdentifier:', {'WIRConnectionIdentifierKey': conn_id})
send(s, '_rpc_getConnectedApplications:', {'WIRConnectionIdentifierKey': conn_id})
end = time.time() + seconds
while time.time() < end:
    try:
        m = recv(s)
    except socket.timeout:
        print('timeout')
        break
    except EOFError as e:
        print('closed by the guest:', e)
        break
    print(m.get('__selector'), repr(m.get('__argument'))[:600])
