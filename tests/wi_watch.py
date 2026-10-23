#!/usr/bin/env python3
"""Print every message the guest's apps send through the relay for SECONDS, asking for listings every 10 s.
usage: wi_watch.py [SECONDS] [PORT]"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from webinspector import Inspector  # noqa: E402

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 120
insp = Inspector(int(sys.argv[2]) if len(sys.argv) > 2 else 27753, debug=True)
end = time.time() + seconds
while time.time() < end:
    insp.request_listing()
    time.sleep(10)
print('pages:', insp.pages(None))
