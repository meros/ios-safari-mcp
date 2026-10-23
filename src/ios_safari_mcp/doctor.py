"""`ios-safari-mcp doctor`: what is set up, what is missing, and the command that fixes it."""
import os
import shutil
import socket
import sys
from pathlib import Path

from . import config

OK, WARN, FAIL, INFO = 'ok', 'warn', 'FAIL', 'info'


def check(results, level, name, detail='', fix=''):
    results.append((level, name, detail, fix))


def port_open(port):
    try:
        socket.create_connection(('127.0.0.1', port), timeout=1).close()
        return True
    except OSError:
        return False


def file_contains(path, text):
    try:
        return text in Path(path).read_text(errors='replace')
    except OSError:
        return False


def mem_gb():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return None


def run():
    r = []
    root, tree = config.ROOT, config.HACKIOS

    # tools
    nix = shutil.which('nix')
    check(r, OK if nix else FAIL, 'nix', nix or 'not found', 'install Nix (https://nixos.org/download) with flakes enabled: it provides QEMU, the compiler for the guest relay and the build shell')
    check(r, OK if shutil.which('gzip') else FAIL, 'gzip', fix='needed to save and restore snapshots')

    # the hackios tree
    have_tree = os.path.isfile(os.path.join(tree, 'qemu', 'touch.py')) and os.path.isfile(os.path.join(tree, 'common', 'run.sh'))
    check(r, OK if have_tree else FAIL, 'hackios tree', tree,
          'clone oosexgang/hackios-xnu-kernel and hackios-xnu-userland into one tree (see its README), then set HACKIOS_DIR to it')
    if have_tree:
        ios = os.path.join(tree, 'ios165')
        have_ios = os.path.isdir(os.path.join(ios, 'rootfs', 'usr', 'lib')) and os.path.isdir(os.path.join(ios, 'cryptex-app'))
        caches = [f for d in os.listdir(ios) if os.path.isdir(os.path.join(ios, d)) for f in os.listdir(os.path.join(ios, d))
                  if f.startswith('dyld_shared_cache_arm64')] if os.path.isdir(ios) else []
        check(r, OK if have_ios and len(caches) >= 43 else FAIL, 'iOS 16.5 userland files',
              f'{"present" if have_ios else "missing"}, {len(caches)} dyld cache files',
              f'cd {ios} && ./fetch.sh   (downloads about 6 GB from Apple\'s servers; needs 7-Zip 23 or newer, which the FHS shell provides)')
        ud = os.path.join(tree, 'userland')
        patched = (file_contains(os.path.join(ud, 'build.sh'), 'obj/wi_relay') and
                   file_contains(os.path.join(ud, 'mkjobs.py'), "'webinspectord',"))
        check(r, OK if patched else WARN, 'relay hooks in the hackios build', 'present' if patched else 'not applied',
              'ios-safari-mcp build-image   (applies them, builds the relay and the image)')
        staged = (os.path.isfile(os.path.join(ud, 'root', 'LaunchDaemons', 'com.redwood.wirelay.plist')) and
                  not os.path.exists(os.path.join(ud, 'root', 'LaunchDaemons', 'com.apple.webinspectord.plist')))
        check(r, OK if staged else WARN, 'image staged with the relay', 'yes' if staged else 'no',
              'ios-safari-mcp build-image')
    check(r, OK if os.path.isfile(os.path.join(root, 'guest', 'wi_relay')) else WARN, 'guest relay binary',
          os.path.join(root, 'guest', 'wi_relay'), 'ios-safari-mcp build-image   (or scripts/build-guest.sh for the relay alone)')

    # QEMU and the build shell
    q = shutil.which('qemu-system-aarch64')
    check(r, INFO, 'qemu', q or 'will be fetched with nix on the first start')
    fhs = os.path.join(config.STATE, 'fhs', 'bin', 'hfhs')
    check(r, OK if os.path.exists(fhs) else INFO, 'build shell (FHS)', 'built' if os.path.exists(fhs) else 'will be built on the first start (a few seconds)')

    # host resources
    gb = mem_gb()
    if gb is not None:
        check(r, OK if gb >= 12 else WARN, 'memory', f'{gb:.0f} GB (the guest takes 7 GB, QEMU about 2.5 GB more)',
              'close other programs or start fewer VMs')
    cpus = os.cpu_count() or 1
    check(r, OK if cpus >= 4 else WARN, 'cpu cores', str(cpus), 'the guest uses 4 vCPU threads; with fewer cores it is slow and may panic with "Ticket spinlock timeout"')
    try:
        os.makedirs(config.STATE, exist_ok=True)
        free = shutil.disk_usage(config.STATE).free / 1e9
        check(r, OK if free >= 10 else WARN, 'disk for snapshots', f'{free:.0f} GB free in {config.STATE} (a snapshot is about 3 GB)')
    except OSError as e:
        check(r, FAIL, 'state directory', f'{config.STATE}: {e}')
    try:
        if Path('/sys/devices/system/cpu/intel_pstate/no_turbo').read_text().strip() == '1':
            check(r, WARN, 'cpu turbo', 'off (on battery?): the boot takes about 40 % longer', 'plug in the charger')
    except OSError:
        pass

    # a running VM
    if port_open(config.HMP_PORT):
        from . import vm
        st = vm.status()
        check(r, OK if st['touchscreen'] == 'ok' and not st['panicked'] else FAIL, 'VM', f'running, phase {st["phase"]}, touchscreen {st["touchscreen"]}, panicked {st["panicked"]}',
              'vm_stop, then vm_start (a boot where the guest ignores touches has to be restarted)')
        check(r, OK if port_open(config.WI_PORT) else WARN, 'relay port', f'127.0.0.1:{config.WI_PORT}', 'the VM was not started by ios-safari-mcp, or the image has no relay: build-image, vm_stop, vm_start')
    else:
        check(r, INFO, 'VM', f'not running (monitor port {config.HMP_PORT} is free)')
    return r


def main():
    results = run()
    width = max(len(n) for _, n, _, _ in results)
    failed = False
    for level, name, detail, fix in results:
        print(f'[{level:>4}] {name:<{width}}  {detail}')
        if level in (FAIL, WARN) and fix:
            print(f'       {"":<{width}}  fix: {fix}')
        failed |= level == FAIL
    print()
    print('Everything needed is in place.' if not failed and not any(level == WARN for level, *_ in results)
          else ('Fix the FAIL lines first.' if failed else 'Usable; the warnings above are worth a look.'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
