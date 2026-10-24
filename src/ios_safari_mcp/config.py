"""Paths, ports and settings. Read from the environment once, at import.

IOS_SAFARI_MCP_ROOT    the directory with launch.sh, build-image.sh, guest/ (default: the checkout this package is in)
HACKIOS_DIR            the hackios tree (default: ../hackios next to the checkout, then ~/hackios)
IOS_SAFARI_MCP_STATE   log, pid file, snapshots, screenshots (default: $XDG_STATE_HOME/ios-safari-mcp)
QEMU_INSTANCE          the monitor, QMP and relay ports shift by 10 per instance. Two VMs of one hackios tree cannot run at the
                       same time: the parent keeps one vars.fd and one esp/ per tree (use two trees)
WI_PORT                host port that QEMU forwards to the guest's Web Inspector relay (default 27753 + 10 per instance)
IOS_SAFARI_MCP_RECORD  a file: every frame received from the relay is appended to it as a line of JSON (for test fixtures)
"""
import os

ROOT = os.environ.get('IOS_SAFARI_MCP_ROOT') or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _hackios_dir():
    explicit = os.environ.get('HACKIOS_DIR')
    if explicit:
        return os.path.expanduser(explicit)
    for candidate in (os.path.join(ROOT, '..', 'hackios'), os.path.expanduser('~/hackios')):
        if os.path.isfile(os.path.join(candidate, 'qemu', 'touch.py')):
            return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(ROOT, '..', 'hackios'))


HACKIOS = _hackios_dir()
STATE = os.environ.get('IOS_SAFARI_MCP_STATE') or os.path.join(
    os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'ios-safari-mcp')
INSTANCE = int(os.environ.get('QEMU_INSTANCE', '0'))

HMP_PORT = 4444 + 10 * INSTANCE                      # QEMU human monitor (screendump)
QMP_PORT = 4445 + 10 * INSTANCE                      # QEMU machine protocol (input events, snapshots)
WI_GUEST_PORT = 27753                                # where wi_relay listens inside the guest
WI_PORT = int(os.environ.get('WI_PORT', 27753 + 10 * INSTANCE))

LOG = os.path.join(STATE, 'run.log')
PIDFILE = os.path.join(STATE, 'run.pid')
SNAPDIR = os.path.join(STATE, 'snapshots')
WI_STALE = os.path.join(STATE, 'wi-connections.json')
SHOTS = os.path.join('/tmp', f'ios-safari-mcp-{os.getuid()}')


def ensure_dirs():
    for d in (STATE, SNAPDIR):
        os.makedirs(d, exist_ok=True)
    os.makedirs(SHOTS, mode=0o700, exist_ok=True)
    if os.stat(SHOTS).st_uid != os.getuid():
        raise OSError(f'{SHOTS} belongs to another user')
