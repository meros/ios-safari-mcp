"""The one connection to the guest's relay that the tools share, and navigation on top of it."""
import contextlib
import json
import re
import time
from urllib.parse import urlparse

from .. import config
from . import page
from .client import Inspector, InspectorError

_inspector = None


def _stale_ids():
    try:
        return list(json.load(open(config.WI_STALE)))
    except (OSError, ValueError):
        return []


def _save_ids(ids):
    with open(config.WI_STALE, 'w') as f:
        json.dump(ids, f)


def connection():
    """The connection to the relay: made on first use, and again after it was lost."""
    global _inspector
    if _inspector is not None and _inspector.dead is None:
        return _inspector
    _inspector = None
    try:
        insp = Inspector(config.WI_PORT, connect_timeout=15)
    except OSError as e:
        raise OSError(f'cannot reach the Web Inspector relay on 127.0.0.1:{config.WI_PORT} ({e}); is the VM running an image '
                      'built by `ios-safari-mcp build-image`, and has the guest finished booting?') from e
    # a page takes one frontend at a time: end the connections that an earlier run left behind
    for stale in _stale_ids():
        insp.forget_connection(stale)
    _save_ids([insp.connection_id])
    _inspector = insp
    return insp


def close():
    global _inspector
    if _inspector is not None:
        with contextlib.suppress(OSError):
            _inspector.close()
        _inspector = None
    _save_ids([])


def session(args=None):
    """The Session for the page named by the tool arguments (`page`: index in the list; `wait`: seconds to wait)."""
    args = args or {}
    index = args.get('page')
    return connection().session(None if index is None else int(index), wait=float(args.get('wait', 60)))


# --- navigation --------------------------------------------------------------------------------------------------------
def full_url(url):
    """Page.navigate needs a scheme: a bare name gets https, except localhost and addresses, which get http."""
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', url) and not re.match(r'^[^/]*:\d+(/|$)', url):
        return url
    http = re.match(r'^(localhost|\d+\.\d+\.\d+\.\d+)(:\d+)?(/|$)', url)
    return ('http://' if http else 'https://') + url


def wait_loaded(s, timeout, expect=None):
    """Until document.readyState is complete and, if `expect` is given, the address contains it (a page that was just
    started is about:blank and complete for a moment; a navigation makes a new target, so a call can fail on the way)."""
    end = time.time() + timeout
    state = None
    while time.time() < end:
        try:
            state = s.evaluate('document.readyState + " " + location.href', timeout=20)
            if str(state).startswith('complete') and (not expect or expect in str(state)):
                return state
        except InspectorError:
            pass
        time.sleep(0.5)
    return state


def navigate(url, timeout=60, page_args=None):
    """Open an address. With a web page: Page.navigate (a few seconds). Without one (Safari on its start page, or not
    running): SpringBoard opens it. Returns a sentence that says which way was used and how long it took."""
    t = time.time()
    target = full_url(url)
    expect = '' if target.startswith('about:') else (urlparse(target).hostname or '')
    insp = connection()
    index = None if (page_args or {}).get('page') is None else int(page_args['page'])
    try:
        s = insp.session(index, wait=3)
    except InspectorError:
        s = None
    if s is not None:
        since = s.event_count
        s.send('Page.navigate', {'url': target})
        via = 'the inspector'
        # the old page stays until the navigation commits: wait for that, then for the load to finish
        nav = s.wait_event('Page.frameNavigated', since, min(timeout, 45), match=lambda p: not p.get('frame', {}).get('parentId'))
        if nav is None:
            return f'asked {via} for {target}, but the page did not navigate within {min(timeout, 45)} s'
    else:
        insp.open_url(target, timeout=30)
        via = 'SpringBoard (there was no web page)'
        s = insp.session(wait=max(10.0, timeout - (time.time() - t)))
    state = wait_loaded(s, max(5.0, timeout - (time.time() - t)), expect)
    return f'opened {state.split(" ", 1)[1] if state and " " in str(state) else target} through {via} in {time.time() - t:.1f} s ({state})'


def evaluate_json(s, fn, *args, timeout=60):
    """Run a snippet from page.py with JSON arguments and return its value."""
    return s.evaluate(page.call(fn, *args), timeout=timeout)
