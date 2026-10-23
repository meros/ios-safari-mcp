#!/usr/bin/env python3
"""Does the guest's Safari answer a listing request, in bplist15 and in bplist00? (Safari must be idle: an app also
sends listings by itself when a page changes.)  usage: wi_encoding.py [PORT]"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from webinspector import Inspector  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 27753
for fmt in ('15', '00', '15', '00'):
    insp = Inspector(port, fmt=fmt)
    time.sleep(3)            # let the relay settle; nothing is sent yet
    before = len(insp.log)
    insp.request_listing()
    time.sleep(12)
    got = insp.log[before:]
    print(f'fmt {fmt}: {len(got)} messages after the request:', sorted({(p, n) for _, p, n, _ in got}))
    insp.sock.close()
    time.sleep(2)
