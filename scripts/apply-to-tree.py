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

here = os.environ.get('IOS_SAFARI_MCP_ROOT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the checkout
tree = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('HACKIOS_DIR') or os.path.join(here, '..', 'hackios')
tree = os.path.abspath(os.path.expanduser(tree))
ud = os.path.join(tree, 'userland')
binary = os.path.join(here, 'guest', 'wi_relay')

# (file, the line to change, what it becomes, the text that shows it was done already)
PATCHES = [
    (os.path.join(ud, 'build.sh'),
     '\tcp com.redwood.demo.plist com.redwood.wifi.plist com.redwood.thermald.plist root/LaunchDaemons/\n',
     '\tcp com.redwood.demo.plist com.redwood.wifi.plist com.redwood.thermald.plist root/LaunchDaemons/\n'
     '\t[ -f com.redwood.wirelay.plist ] && cp com.redwood.wirelay.plist root/LaunchDaemons/\n',
     '[ -f com.redwood.wirelay.plist ] && cp com.redwood.wirelay.plist root/LaunchDaemons/'),
    (os.path.join(ud, 'build.sh'),
     '\t[ -f obj/net_test ] && cp obj/net_test root/bin/\n',
     '\t[ -f obj/net_test ] && cp obj/net_test root/bin/\n\t[ -f obj/wi_relay ] && cp obj/wi_relay root/bin/\n',
     '[ -f obj/wi_relay ] && cp obj/wi_relay root/bin/'),
    (os.path.join(ud, 'mkjobs.py'),
     "'osanalytics', 'lockdown', 'audio', 'jetsamproperties',",
     "'osanalytics', 'lockdown', 'audio', 'jetsamproperties', 'webinspectord',",
     "'jetsamproperties', 'webinspectord',"),
]


def plan():
    """Check everything that will be touched before anything is written; returns the patches still to apply."""
    if not os.path.isfile(binary):
        sys.exit('guest/wi_relay is not built: run scripts/build-guest.sh first')
    for needed in (os.path.join(ud, 'build.sh'), os.path.join(ud, 'mkjobs.py')):
        if not os.path.isfile(needed):
            sys.exit(f'{needed} does not exist: {tree} is not a hackios tree (set HACKIOS_DIR)')
    todo = []
    for path, old, new, marker in PATCHES:
        text = pathlib.Path(path).read_text()
        if marker in text:
            print(f'{os.path.basename(path)}: already patched ({marker})')
        elif text.count(old) == 1:
            todo.append((path, old, new))
        else:
            sys.exit(f'{path}: the line to patch is not there exactly once (the parent changed?): {old.strip()!r}')
    return todo


todo = plan()
os.makedirs(os.path.join(ud, 'obj'), exist_ok=True)
shutil.copy(binary, os.path.join(ud, 'obj', 'wi_relay'))
os.chmod(os.path.join(ud, 'obj', 'wi_relay'), 0o755)
shutil.copy(os.path.join(here, 'guest', 'com.redwood.wirelay.plist'), os.path.join(ud, 'com.redwood.wirelay.plist'))
for path, old, new in todo:
    pathlib.Path(path).write_text(pathlib.Path(path).read_text().replace(old, new, 1))
    print(f'{os.path.basename(path)}: patched ({old.strip()[:50]}...)')
