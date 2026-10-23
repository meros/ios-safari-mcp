"""The VM: start, stop, status, snapshots, boot phases, the console log, and asking the guest about a process."""
import contextlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from . import config, qemu
from .results import ToolError

# phases in boot order: (name, regex that the log shows when the phase is reached)
PHASES = [
    ('kernel', r'Security policy loaded'),
    ('launchd', r'Successfully spawned'),
    ('backboardd_ready', r'Bootstrap system shells ready'),
    ('springboard_owns_screen', r'screen owner is now pid:\d+ \(com\.apple\.springboard\)'),
]
PANIC = re.compile(r'^panic\(cpu', re.M)
# backboardd could not use the virtual touchscreen in this boot (a start-up race in the guest): every tap is dropped
TOUCH_BAD = re.compile(r'ignoring digitizer event for display')


def log_text():
    try:
        return Path(config.LOG).read_bytes().decode('utf-8', 'replace')
    except OSError:
        return ''


def launcher_pid():
    try:
        pid = int(Path(config.PIDFILE).read_text().split()[0])
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, IndexError):
        return None


def started_at():
    try:
        return float(Path(config.PIDFILE).read_text().split()[1])
    except (OSError, ValueError, IndexError):
        return None


def status():
    text = log_text()
    reached = [name for name, rx in PHASES if re.search(rx, text)]
    started = started_at()
    alive = qemu.alive()
    state = qemu.run_state() if alive else None
    return {
        'running': alive,
        'qemu_state': state,
        'launcher_alive': launcher_pid() is not None,
        'panicked': bool(PANIC.search(text)),
        'touchscreen': 'not recognized: restart the VM (vm_stop, vm_start)' if TOUCH_BAD.search(text) else 'ok',
        # the log of an earlier boot stays on disk: a phase counts only while QEMU runs
        'phase': ('restoring' if state == 'inmigrate' else reached[-1] if alive and reached else
                  'starting' if alive or launcher_pid() is not None else 'stopped'),
        'seconds_since_start': round(time.time() - started) if started else None,
        'log': config.LOG,
    }


# --- snapshots ---------------------------------------------------------------------------------------------------------
def snap_path(name):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name):
        raise ToolError('snapshot name: letters, digits, _ . - only')
    return os.path.join(config.SNAPDIR, name + '.gz')


def snapshots():
    out = []
    for f in sorted(os.listdir(config.SNAPDIR)):
        if f.endswith('.gz'):
            st = os.stat(os.path.join(config.SNAPDIR, f))
            out.append({'name': f[:-3], 'mb': st.st_size >> 20, 'saved': time.strftime('%F %T', time.localtime(st.st_mtime))})
    return out


def save(name, resume=True, timeout=240):
    """Write the whole machine state (RAM, CPUs, devices) to a file. Needs a VM started with saveable=true."""
    if not qemu.alive():
        raise ToolError('QEMU is not running.')
    path = snap_path(name)
    q = qemu.Qmp()
    blockers = q.command('query-migrate').get('blocked-reasons')
    if blockers:
        raise ToolError('This VM cannot be saved: ' + '; '.join(blockers) + '. Start it with vm_start (saveable=true).')
    tmp = path + '.part'
    t0 = time.time()
    q.command('migrate', uri=f"exec:gzip -1 > '{tmp}'")
    while time.time() - t0 < min(timeout, 280):
        info = q.command('query-migrate')
        if info.get('status') == 'completed':
            os.replace(tmp, path)
            if resume:
                q.command('cont')
            mb = os.path.getsize(path) // (1 << 20)
            return f'saved {name}: {mb} MB in {round(time.time() - t0)} s' + ('' if resume else '; the VM is paused')
        if info.get('status') in ('failed', 'cancelled'):
            raise ToolError('save failed: ' + json.dumps(info))
        time.sleep(2)
    raise ToolError('save did not finish in time: ' + json.dumps(q.command('query-migrate')))


# --- start, stop, wait --------------------------------------------------------------------------------------------------
def start(seconds=28800, cpus=4, spinlock_timeout_us=60000000, snapshot=None, saveable=True):
    if qemu.alive():
        raise ToolError('QEMU is already running. Call vm_stop first for a fresh boot.\n' + json.dumps(status()))
    # The guest gets loader_return_after=SECONDS and hands control back to the loader after that much guest time since
    # boot. A saved state keeps the clock, so a short value makes every snapshot die soon after it is restored.
    env = dict(os.environ, HACKIOS_DIR=config.HACKIOS, IOS_SAFARI_MCP_STATE=config.STATE, IOS_SAFARI_MCP_ROOT=config.ROOT,
               RUN_SECONDS=str(int(seconds)), QEMU_INSTANCE=str(config.INSTANCE), WI_PORT=str(config.WI_PORT))
    if snapshot:
        path = snap_path(snapshot)
        if not os.path.exists(path):
            raise ToolError(f'no snapshot {snapshot}; see vm_snapshots')
        env['HACKIOS_SNAPSHOT'] = path
    # a guest that can be saved needs a read-only ESP and no sound device; a restore needs the same command line
    if snapshot or saveable:
        env['HACKIOS_MIGRATABLE'] = '1'
    # slto_us raises the kernel's spinlock timeout: on a loaded host a vCPU thread can wait seconds for a core, and the
    # default timeout (about 2.5 s) then panics the guest ("Ticket spinlock timeout")
    env['QEMU_SMP'] = str(int(cpus))
    env['CMDLINE'] = f'cpus={int(cpus)} slto_us={int(spinlock_timeout_us)}'
    launcher = os.path.join(config.ROOT, 'scripts', 'launch.sh')
    with open(config.LOG, 'wb') as log:
        p = subprocess.Popen([launcher], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                             start_new_session=True)
    with open(config.PIDFILE, 'w') as f:
        f.write(f'{p.pid} {time.time()}\n')
    if snapshot:
        return (f'Restoring snapshot {snapshot} (launcher pid {p.pid}). Use vm_status; the guest runs when the restore '
                'has finished.')
    return f'Started (launcher pid {p.pid}). A boot takes several minutes under software emulation; use vm_status or wait_for(phase).'


def stop():
    if qemu.alive():
        with contextlib.suppress(OSError):
            qemu.hmp('quit', 3)
    pid = launcher_pid()
    if pid:
        time.sleep(1)
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    return status()


def wait_for(phase='springboard_owns_screen', timeout=240):
    """Block until a boot phase is reached (or `running`: a restored snapshot is live). Returns (reached, status)."""
    timeout = min(int(timeout), 280)
    rx = dict(PHASES).get(phase)
    if rx is None and phase != 'running':
        raise ToolError(f'unknown phase {phase}; choose from {[n for n, _ in PHASES] + ["running"]}')
    end = time.time() + timeout
    while time.time() < end:
        text = log_text()
        if phase == 'running':
            if qemu.alive() and qemu.run_state() == 'running':
                return True, status()
        elif re.search(rx, text):
            return True, status()
        if PANIC.search(text) or (started_at() and not qemu.alive() and time.time() - started_at() > 30):
            raise ToolError('VM stopped or panicked before the phase.\n' + json.dumps(status()))
        time.sleep(3)
    return False, status()


# --- the console log and the guest's processes --------------------------------------------------------------------------
def log_tail(lines=40, pattern=None):
    rows = log_text().splitlines()
    if pattern:
        rx = re.compile(pattern)
        rows = [r for r in rows if rx.search(r)]
    return '\n'.join(r[:300] for r in rows[-min(int(lines), 400):])


def guest_process(name, timeout=20):
    """Is a process running in the guest? The kernel prints the threads of a named process into the console log
    (`ustacks: NAME[pid] N threads ...` or `ustacks: no process called NAME`); this asks and reads the answer."""
    if not re.fullmatch(r'[A-Za-z0-9_.+-]{1,40}', name):
        raise ToolError('name: letters, digits and _ . + - only')
    before = len(log_text())
    ok, out = qemu.touch('stacks', name)
    if not ok:
        raise ToolError(out)
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        new = log_text()[before:]
        n = re.escape(name)
        m = re.search(rf'ustacks: (no process called {n}|{n}\[(\d+)\] (\d+) threads[^\n]*)', new)
        if m:
            if m.group(2) is None:
                return {'running': False, 'name': name}
            return {'running': True, 'name': name, 'pid': int(m.group(2)), 'threads': int(m.group(3))}
        time.sleep(1)
    raise ToolError(f'no answer for {name} in the log (is the VM running and the log current?)')
