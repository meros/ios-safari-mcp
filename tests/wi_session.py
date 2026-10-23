#!/usr/bin/env python3
"""List the guest Safari's inspectable pages through guest/wi_relay and evaluate JavaScript in the first web page.

usage: wi_session.py [PORT]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from webinspector import Inspector  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 27753
insp = Inspector(port, debug=True)
for stale in sys.argv[2:]:
    insp.forget_connection(stale)
pages = insp.wait_for_pages(90)
print('pages:', pages)
if not pages:
    sys.exit('no web page listed')
s = insp.attach(pages[0])
print('target:', s.target)
print('title ->', s.evaluate('document.title'))
print('url   ->', s.evaluate('location.href'))
print('html  ->', (s.evaluate('document.documentElement.outerHTML') or '')[:300])
insp.close()
