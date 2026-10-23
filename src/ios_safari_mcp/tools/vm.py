"""VM tools: boot, stop, snapshots, status, waiting for a boot phase."""
import json

from .. import vm
from ..registry import tool
from ..results import text_result


@tool('vm_start', 'Boot HackiOS in QEMU in the background. Takes 9 to 14 minutes under software emulation; restoring a '
      'snapshot takes about 30 s. Then call safari_ready.',
      {'seconds': {'type': 'integer', 'description': 'session length in guest seconds, default 28800 (8 h); a snapshot cannot live longer than this minus its age when saved'},
       'cpus': {'type': 'integer', 'description': 'guest CPUs, default 4'},
       'spinlock_timeout_us': {'type': 'integer', 'description': 'kernel spinlock timeout, default 60000000'},
       'snapshot': {'type': 'string', 'description': 'restore this snapshot instead of booting'},
       'saveable': {'type': 'boolean', 'description': 'default true: read-only ESP, no sound device, so vm_save works'}})
def vm_start(args):
    return text_result(vm.start(
        seconds=args.get('seconds', 28800), cpus=args.get('cpus', 4),
        spinlock_timeout_us=args.get('spinlock_timeout_us', 60000000), snapshot=args.get('snapshot'),
        saveable=args.get('saveable', True)))


@tool('vm_save', 'Save the whole machine state to a named snapshot (takes a minute or more, files are GB). '
      'Restore with vm_start(snapshot=name). Only works for a VM started with saveable=true.',
      {'name': {'type': 'string'}, 'resume': {'type': 'boolean'}, 'timeout': {'type': 'integer'}}, ['name'])
def vm_save(args):
    return text_result(vm.save(args['name'], args.get('resume', True), int(args.get('timeout', 240))))


@tool('vm_snapshots', 'List saved snapshots.')
def vm_snapshots(args):
    return text_result(json.dumps(vm.snapshots(), indent=1))


@tool('vm_stop', 'Stop QEMU.')
def vm_stop(args):
    return text_result(json.dumps(vm.stop()))


@tool('vm_status', 'Is QEMU running, did it panic, did the guest accept the touchscreen, and which boot phase was reached.')
def vm_status(args):
    return text_result(json.dumps(vm.status(), indent=1))


@tool('wait_for', 'Block until a boot phase is reached (kernel, launchd, backboardd_ready, springboard_owns_screen, '
      'or running for a restored snapshot) or the timeout (max 280 s) ends.',
      {'phase': {'type': 'string'}, 'timeout': {'type': 'integer'}})
def wait_for(args):
    reached, st = vm.wait_for(args.get('phase', 'springboard_owns_screen'), args.get('timeout', 240))
    return text_result(json.dumps(st) if reached else f'Timed out.\n{json.dumps(st)}')
