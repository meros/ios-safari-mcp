"""Tools that ask the guest: processes, the console log, and a pause."""
import json
import time

from .. import vm
from ..registry import tool
from ..results import text_result


@tool('guest_process', 'Is a process running in the guest (by name, e.g. MobileSafari, wi_relay)? Returns pid and thread count.',
      {'name': {'type': 'string'}, 'timeout': {'type': 'number'}}, ['name'])
def guest_process(args):
    return text_result(json.dumps(vm.guest_process(args['name'], args.get('timeout', 20))))


@tool('log_tail', 'Last lines of the guest console log, optionally filtered by a regex.',
      {'lines': {'type': 'integer'}, 'pattern': {'type': 'string'}})
def log_tail(args):
    return text_result(vm.log_tail(args.get('lines', 40), args.get('pattern')))


@tool('wait', 'Sleep up to 60 s (the guest is slow; animations take time).', {'seconds': {'type': 'number'}})
def wait(args):
    time.sleep(min(float(args.get('seconds', 1)), 60))
    return text_result('done')
