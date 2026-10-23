"""Talking to the running QEMU: the human monitor, QMP, and the hackios touch helper."""
import json
import os
import socket
import subprocess
import sys

from . import config


def alive():
    try:
        socket.create_connection(('127.0.0.1', config.HMP_PORT), timeout=1).close()
        return True
    except OSError:
        return False


def hmp(command, timeout=10):
    """Run one human monitor command and return its output. QEMU answers when it is done, so no fixed sleeps."""
    with socket.create_connection(('127.0.0.1', config.HMP_PORT), timeout=3) as s:
        s.settimeout(timeout)

        def until_prompt():
            buf = b''
            while not buf.endswith(b'(qemu) '):
                chunk = s.recv(4096)
                if not chunk:          # quit closes the connection
                    break
                buf += chunk
            return buf

        until_prompt()
        s.sendall(command.encode() + b'\n')
        return until_prompt().decode(errors='replace')


class Qmp:
    def __init__(self):
        self.sock = socket.create_connection(('127.0.0.1', config.QMP_PORT), timeout=10)
        self.file = self.sock.makefile('rw')
        self.file.readline()
        self.command('qmp_capabilities')

    def command(self, name, **arguments):
        self.file.write(json.dumps({'execute': name, 'arguments': arguments}) + '\n')
        self.file.flush()
        while True:
            reply = json.loads(self.file.readline())
            if 'return' in reply:
                return reply['return']
            if 'error' in reply:
                raise OSError(f'{name}: {reply["error"]["desc"]}')


def run_state():
    """QEMU's own run state over QMP: running, inmigrate (loading a snapshot), paused, postmigrate ..."""
    try:
        return Qmp().command('query-status')['status']
    except (OSError, ValueError, KeyError):
        return None


def touch(*argv):
    """qemu/touch.py of the hackios tree: tap, hold, swipe, key, stacks. Returns (ok, output)."""
    env = dict(os.environ, QEMU_INSTANCE=str(config.INSTANCE))
    r = subprocess.run([sys.executable, '-I', os.path.join(config.HACKIOS, 'qemu', 'touch.py'), *map(str, argv)],
                       capture_output=True, text=True, env=env, timeout=60)
    return r.returncode == 0, (r.stdout + r.stderr).strip()
