#!/usr/bin/env python3
"""Put the Web Inspector relay into a hackios tree (idempotent): usage: apply-to-tree.py [HACKIOS_DIR]

 - guest/wi_relay (built by build-guest.sh) as userland/obj/wi_relay, and its launchd job guest/com.redwood.wirelay.plist
   as userland/com.redwood.wirelay.plist
 - userland/build.sh: copy both into the image
 - userland/mkjobs.py: leave the job of webinspectord out ("webinspectord" in DENY), because the relay takes its mach service
Then build the image with build-image.sh.
"""
import os
import pathlib
import shutil
import sys

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the checkout
tree = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(here, '..', 'hackios')) if 'HACKIOS_DIR' not in os.environ else os.path.expanduser(os.environ['HACKIOS_DIR'])
ud = os.path.join(tree, 'userland')
binary = os.path.join(here, 'guest', 'wi_relay')
if not os.path.exists(binary):
    sys.exit('guest/wi_relay is not built: run scripts/build-guest.sh first')

os.makedirs(os.path.join(ud, 'obj'), exist_ok=True)
shutil.copy(binary, os.path.join(ud, 'obj', 'wi_relay'))
os.chmod(os.path.join(ud, 'obj', 'wi_relay'), 0o755)
shutil.copy(os.path.join(here, 'guest', 'com.redwood.wirelay.plist'), os.path.join(ud, 'com.redwood.wirelay.plist'))


def patch(path, old, new, marker):
    s = pathlib.Path(path).read_text()
    if marker in s:
        print(f'{os.path.basename(path)}: already patched ({marker})')
        return
    if s.count(old) != 1:
        sys.exit(f'{path}: cannot find the line to patch once: {old!r}')
    pathlib.Path(path).write_text(s.replace(old, new))
    print(f'{os.path.basename(path)}: patched ({marker})')


patch(os.path.join(ud, 'build.sh'),
      '\tcp com.redwood.demo.plist com.redwood.wifi.plist com.redwood.thermald.plist root/LaunchDaemons/\n',
      '\tcp com.redwood.demo.plist com.redwood.wifi.plist com.redwood.thermald.plist root/LaunchDaemons/\n'
      '\t[ -f com.redwood.wirelay.plist ] && cp com.redwood.wirelay.plist root/LaunchDaemons/\n',
      'com.redwood.wirelay.plist root/LaunchDaemons')
patch(os.path.join(ud, 'build.sh'),
      '\t[ -f obj/net_test ] && cp obj/net_test root/bin/\n',
      '\t[ -f obj/net_test ] && cp obj/net_test root/bin/\n\t[ -f obj/wi_relay ] && cp obj/wi_relay root/bin/\n',
      'obj/wi_relay')
patch(os.path.join(ud, 'mkjobs.py'),
      "'osanalytics', 'lockdown', 'audio', 'jetsamproperties',",
      "'osanalytics', 'lockdown', 'audio', 'jetsamproperties', 'webinspectord',",
      "'webinspectord',")
