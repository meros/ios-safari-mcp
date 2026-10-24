"""Web Inspector tools: pages, JavaScript, DOM, navigation, console, network, raw protocol, and getting Safari ready."""
import time

from .. import qemu, vm
from ..inspector import InspectorError, NavigationError, manager, page
from ..registry import tool
from ..results import ToolError, json_result, text_result
from . import input as input_tools

PAGE_PROP = {'page': {'type': 'integer', 'description': 'index in inspector_pages; default the first page with a real address'},
             'wait': {'type': 'number', 'description': 'seconds to wait for the page list, default 60'}}


@tool('inspector_pages', 'List the pages that Safari offers to the Web Inspector (title, url). Safari must be running with a '
      'page open; safari_ready makes sure of that.',
      {'all': {'type': 'boolean', 'description': 'also JavaScript contexts (they have no index: page= takes web pages only)'},
       'wait': {'type': 'number'}})
def inspector_pages(args):
    insp = manager.connection()
    web = insp.wait_for_pages(float(args.get('wait', 60)))
    out = [{'index': i, **p} for i, p in enumerate(web)]        # the index is what page= takes: a position among web pages
    if args.get('all'):
        out += [{'index': None, **p} for p in insp.pages(None) if p['type'] != 'WIRTypeWebPage']
    return json_result(out)


@tool('js', 'Run a JavaScript expression in the page and return its value as JSON. Milliseconds, no screenshot or typing.',
      {'expression': {'type': 'string'}, 'await_promise': {'type': 'boolean'}, **PAGE_PROP}, ['expression'])
def js(args):
    s = manager.session(args)
    extra = {'awaitPromise': True} if args.get('await_promise') else {}
    return json_result(s.evaluate(args['expression'], **extra))


@tool('dom', 'Query the DOM by CSS selector. what=info (default: tag, text, attributes, rect in CSS px, visible), html '
      '(outerHTML) or text.',
      {'selector': {'type': 'string'}, 'what': {'type': 'string', 'enum': ['info', 'html', 'text']},
       'limit': {'type': 'integer'}, **PAGE_PROP})
def dom(args):
    s = manager.session(args)
    return json_result(manager.evaluate_json(s, page.DOM, args.get('selector', 'html'), args.get('what', 'info'),
                                             int(args.get('limit', 20))))


@tool('open_url', 'Open an address in Safari: through the inspector when a page exists (about 3 s), else by asking '
      'SpringBoard (starts Safari too). No typing. typing=true types it on the keyboard instead (minutes).',
      {'url': {'type': 'string'}, 'typing': {'type': 'boolean'}, 'timeout': {'type': 'number'}, 'delay_s': {'type': 'number'},
       **{k: v for k, v in PAGE_PROP.items() if k == 'page'}}, ['url'])
def open_url(args):
    if args.get('typing'):
        return _open_by_typing(args)
    try:
        return text_result(manager.navigate(args['url'].strip(), float(args.get('timeout', 60)), args))
    except NavigationError:
        raise
    except (InspectorError, OSError) as e:
        note = f'inspector not usable ({e}); typing the address instead'
    result = _open_by_typing(args)
    result['content'].append({'type': 'text', 'text': note})
    return result


def _open_by_typing(args):
    """Safari must be in the foreground: tap the address bar, wait for the keyboard, type, press go, wait for the screen."""
    from .. import keyboard, screen
    url = args['url'].strip()
    if url.startswith('http://'):
        raise ToolError('typing an address opens it as https, so an http:// address cannot be typed: use open_url without typing')
    if url.startswith('https://'):
        url = url[len('https://'):]
    keyboard.plan_keys(url)                                  # refuse an address with unknown keys before touching anything
    baseline = screen.grab_frame()
    qemu.touch('tap', 0.5, 0.875)
    r = screen.settle({'timeout': 150, 'stable_for': 4, 'wait_change': 100}, baseline)
    if not (r.get('changed') and r.get('stable')):
        raise ToolError(f'The keyboard did not appear after tapping the address bar. Is Safari in the foreground? {r}')
    baseline = screen.grab_frame()
    keyboard.type_keys(url, True, float(args.get('delay_s', 2.5)))
    r = screen.settle({'timeout': 240, 'stable_for': 15, 'wait_change': 90}, baseline)
    return text_result(f'typed {url} and pressed go; page: {r}')


@tool('navigate', 'Same as open_url (no typing): load an address.', {'url': {'type': 'string'}, 'timeout': {'type': 'number'},
                                                                      **{k: v for k, v in PAGE_PROP.items() if k == 'page'}}, ['url'])
def navigate(args):
    return text_result(manager.navigate(args['url'].strip(), float(args.get('timeout', 60)), args))


@tool('reload', 'Reload the page.', {'ignore_cache': {'type': 'boolean'}, **PAGE_PROP})
def reload(args):
    manager.session(args).send('Page.reload', {'ignoreCache': bool(args.get('ignore_cache'))})
    return text_result('reloading')


def _tail(items, limit):
    n = int(limit)
    return items[-n:] if n > 0 else []


@tool('console_messages', 'Console messages of the page since the inspector attached (level, text, url).',
      {'limit': {'type': 'integer'}, 'clear': {'type': 'boolean'}, **PAGE_PROP})
def console_messages(args):
    s = manager.session(args)
    keep = (lambda m: m.get('method') != 'Console.messageAdded') if args.get('clear') else None
    out = [m['params']['message'] for m in s.snapshot_events(keep) if m.get('method') == 'Console.messageAdded']
    return json_result(_tail(out, args.get('limit', 100)))


@tool('network_log', 'Requests of the page since the inspector attached: url, method, status, mime type, failures.',
      {'limit': {'type': 'integer'}, **PAGE_PROP})
def network_log(args):
    s = manager.session(args)
    requests = {}
    for m in s.snapshot_events():
        method, p = m.get('method', ''), m.get('params', {})
        if method == 'Network.requestWillBeSent':
            requests[p['requestId']] = {'url': p['request']['url'], 'method': p['request']['method']}
        elif method == 'Network.responseReceived' and p['requestId'] in requests:
            r = p['response']
            requests[p['requestId']].update(status=r.get('status'), mime=r.get('mimeType'), source=r.get('source'))
        elif method == 'Network.loadingFailed' and p['requestId'] in requests:
            requests[p['requestId']]['failed'] = p.get('errorText')
    return json_result(_tail(list(requests.values()), args.get('limit', 100)))


@tool('inspector_raw', 'Send any Web Inspector protocol command to the page (e.g. Page.getCookies, DOM.getDocument, '
      'Debugger.enable) and return the result.',
      {'method': {'type': 'string'}, 'params': {'type': 'object'}, 'timeout': {'type': 'number'}, **PAGE_PROP}, ['method'])
def inspector_raw(args):
    return json_result(manager.session(args).send(args['method'], args.get('params') or {}, timeout=float(args.get('timeout', 60))))


@tool('inspector_close', 'End the inspector connection (a page takes one frontend at a time). The next inspector call reconnects.')
def inspector_close(args):
    manager.close()
    return text_result('closed')


def _process_running(name):
    try:
        return bool(vm.guest_process(name, 30).get('running'))
    except ToolError:
        return False


@tool('safari_ready', 'Get Safari running with a page the inspector lists, without a tap or typing: asks SpringBoard to '
      'open `url` (default about:blank), which starts Safari. Call this after the VM has booted or a snapshot is restored.',
      {'url': {'type': 'string'}, 'timeout': {'type': 'number'}})
def safari_ready(args):
    if not qemu.alive():
        raise ToolError('the VM is not running (vm_start first)')
    status = vm.status()
    if status['touchscreen'] != 'ok':
        raise ToolError('The guest ignores touches in this boot. ' + status['touchscreen'])
    timeout = float(args.get('timeout', 240))
    url = args.get('url') or 'about:blank'
    insp = manager.connection()
    steps = []
    try:
        steps.append(manager.navigate(url, timeout))
    except (InspectorError, OSError) as e:
        # the relay cannot ask SpringBoard (an image without the entitlement, or before SpringBoard is up): use a tap
        steps.append(f'asking SpringBoard failed ({e}); tapping the Safari icon')
        input_tools.wake({})
        time.sleep(3)
        qemu.touch('tap', 0.384, 0.929)                  # Safari in the dock of the home screen
        pages = insp.wait_for_pages(timeout)
        if not pages:
            raise ToolError('Safari did not list a page. ' + '; '.join(steps)) from None
        steps.append(manager.navigate(url, timeout))
    return text_result('; '.join(steps) + '\n' + _pages_json())


def _pages_json():
    import json
    return json.dumps(manager.connection().pages(), indent=1)
