"""Client for the guest's Web Inspector, through guest/wi_relay (TCP, host port 27753 by default).

The guest relay stands in for webinspectord: Safari's processes connect to it and send property lists with messageName
and userInfo (guest/wi_relay.c describes the frames). This module decodes those (bplist15.py), keeps a table of what
each process lists as inspectable, and speaks WebKit's inspector protocol to a page:

    insp = Inspector()                  # connects; the relay replays what each process listed last
    insp.pages()                        # [{'peer': 1, 'page': 1, 'type': 'WIRTypeWebPage', 'url': ..., 'title': ...}]
    s = insp.session()                  # attaches to a page (once; then kept)
    s.evaluate('document.title')        # a Runtime.evaluate through the Target domain
    insp.open_url('https://example.com')  # ask SpringBoard to open it: works with no page at all

The wire format towards the apps follows WebKit's open-source constants (WIR*Key, WIR*Message) and was checked against
the traffic of iOS 16.5. Inspector(debug=True) prints every message; Inspector(record=FILE) saves the received frames.
"""
import collections
import contextlib
import itertools
import json
import plistlib
import queue
import socket
import struct
import sys
import threading
import time
import uuid

from . import bplist15

CONTROL_PEER = 0xFFFFFFFF          # frames for the relay itself, not for a process (guest/wi_relay.c)
WRAPPER_ID_BASE = 1_000_000_000    # ids of the Target.sendMessageToTarget wrappers; their acknowledgements are dropped


class InspectorError(Exception):
    pass


class NavigationError(InspectorError):
    """The inspector works, but the page did not navigate: not a reason to type the address instead."""


def decode(blob):
    """A property list from an app: bplist15 (what the apps send) or bplist00."""
    if blob[:8] == b'bplist00':
        return plistlib.loads(blob)
    return bplist15.decode(blob)


def encode(message_name, user_info, fmt='15'):
    """A property list for an app: bplist15, the format the apps send, or bplist00."""
    message = {'messageName': message_name, 'userInfo': user_info}
    if fmt == '00':
        return plistlib.dumps(message, fmt=plistlib.FMT_BINARY)
    return bplist15.encode(message)


class Inspector:
    def __init__(self, port=27753, host='127.0.0.1', debug=False, connect_timeout=30, fmt='15', record=None):
        self.debug = debug
        self.record = record     # a file name: every received frame is appended as a line of JSON {peer, hex}
        self.fmt = fmt
        self.sock = socket.create_connection((host, port), timeout=connect_timeout)
        self.sock.settimeout(None)
        self.lock = threading.Lock()                 # one writer on the socket
        self.cond = threading.Condition()            # guards peers, sessions, control replies
        self.peers = {}          # peer number -> {'listing': {page id: info}, 'seen': time}
        self.log = collections.deque(maxlen=2000)    # (time, peer, message name, user info) of what was received
        self.sessions = {}       # (peer, page) -> Session
        self.broken = set()      # (peer, page) of pages that did not answer the setup: skipped when choosing a page
        self.control = collections.deque()           # answers of the relay to control commands
        self.connection_id = str(uuid.uuid4()).upper()
        self.dead = None
        threading.Thread(target=self._reader, daemon=True, name='inspector-reader').start()

    # --- transport -------------------------------------------------------------------------------------------------
    def _read(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise EOFError('the relay closed the connection')
            buf += chunk
        return buf

    def _reader(self):
        try:
            while True:
                length = struct.unpack('>I', self._read(4))[0]
                body = self._read(length)
                peer = struct.unpack('>I', body[:4])[0]
                data = body[4:]
                if self.record:
                    with open(self.record, 'a') as f:
                        f.write(json.dumps({'peer': peer, 'hex': data.hex()}) + '\n')
                if peer == CONTROL_PEER:
                    with self.cond:
                        self.control.append(data.decode('utf-8', 'replace'))
                        self.cond.notify_all()
                    continue
                if not data:             # a peer is gone
                    with self.cond:
                        self.peers.pop(peer, None)
                        for key in [k for k in self.sessions if k[0] == peer]:
                            self.sessions.pop(key).tasks.put(None)      # ends its worker thread
                        self.cond.notify_all()
                    continue
                try:
                    message = decode(data)
                except Exception as e:  # noqa: BLE001 - a frame that does not decode is skipped, not fatal
                    if self.debug:
                        print('undecodable from peer', peer, e, data[:40].hex(), file=sys.stderr)
                    continue
                try:
                    self._handle(peer, message)
                except Exception as e:  # noqa: BLE001 - one odd message (a missing key, a wrong type) must not end the connection
                    if self.debug:
                        print('could not handle a message from peer', peer, repr(e), file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - the connection ended: wake everybody who waits
            with self.cond:
                self.dead = e
                self.cond.notify_all()

    def _handle(self, peer, message):
        name = message.get('messageName')
        info = message.get('userInfo') or {}
        if self.debug:
            print('<-', peer, name, repr(info)[:400], file=sys.stderr)
        with self.cond:
            entry = self.peers.setdefault(peer, {'listing': {}, 'seen': time.time()})
            self.log.append((time.time(), peer, name, info))
            if name == 'WIRListingMessage':
                entry['listing'] = info.get('WIRListingKey') or {}
            for session in list(self.sessions.values()):
                if session.peer == peer:
                    session._on_message(name, info)
            self.cond.notify_all()

    def _write(self, peer, data):
        body = struct.pack('>I', peer) + data
        try:
            with self.lock:
                self.sock.sendall(struct.pack('>I', len(body)) + body)
        except OSError as e:
            with self.cond:
                self.dead = self.dead or e
                self.cond.notify_all()
            raise InspectorError(f'connection lost: {e}') from e

    def send(self, peer, message_name, user_info):
        if self.debug:
            print('->', peer, message_name, repr(user_info)[:400], file=sys.stderr)
        self._write(peer, encode(message_name, user_info, self.fmt))

    def _check(self):
        if self.dead:
            raise InspectorError(f'connection lost: {self.dead}')

    # --- relay control ---------------------------------------------------------------------------------------------
    def command(self, text, timeout=60):
        """A command for the relay itself; returns its answer ("ok ..." or "error ...")."""
        with self.cond:
            self.control.clear()
        self._write(CONTROL_PEER, text.encode())
        end = time.time() + timeout
        with self.cond:
            while not self.control and time.time() < end:
                self._check()
                self.cond.wait(0.5)
            if not self.control:
                raise InspectorError(f'the relay did not answer "{text.split()[0]}" in {timeout} s')
            return self.control.popleft()

    def open_url(self, url, timeout=60):
        """Ask SpringBoard to open an address: starts Safari if needed, and works when no web page exists."""
        answer = self.command(f'open-url {url}', timeout)
        if not answer.startswith('ok'):
            raise InspectorError(answer)
        return answer

    # --- connections -----------------------------------------------------------------------------------------------
    def forget_connection(self, connection_id):
        """Tell every process that a frontend connection is over (also one a crashed earlier run left behind): a page
        takes one frontend at a time."""
        self.send(0, 'WIRConnectionDiedMessage', {'WIRConnectionIdentifierKey': connection_id})

    def close(self):
        try:
            self.forget_connection(self.connection_id)
            time.sleep(0.5)
        except (OSError, InspectorError):
            pass
        finally:
            # close() alone does not end the connection while the reader thread waits in recv()
            with contextlib.suppress(OSError):
                self.sock.shutdown(socket.SHUT_RDWR)
            self.sock.close()

    # --- listing ---------------------------------------------------------------------------------------------------
    def pages(self, kinds=('WIRTypeWebPage',)):
        out = []
        with self.cond:
            for peer, entry in sorted(self.peers.items()):
                for key, page in entry['listing'].items():
                    if kinds is None or page.get('WIRTypeKey') in kinds:
                        out.append({'peer': peer, 'page': page.get('WIRPageIdentifierKey', int(key)),
                                    'type': page.get('WIRTypeKey'), 'url': page.get('WIRURLKey', ''),
                                    'title': page.get('WIRTitleKey', '')})
        return out

    def request_listing(self):
        """Ask every process (peer 0) for its list of inspectable pages."""
        self.send(0, 'WIRApplicationGetListingMessage', {'WIRConnectionIdentifierKey': self.connection_id})

    def wait_for_pages(self, timeout=60, kinds=('WIRTypeWebPage',)):
        end = time.time() + timeout
        asked = 0
        while time.time() < end:
            self._check()
            if time.time() - asked > 8:   # the apps list their pages when something changes, so ask
                self.request_listing()
                asked = time.time()
            with self.cond:
                found = self.pages(kinds)
                if found:
                    return found
                self.cond.wait(1)
        return self.pages(kinds)

    def wait_for_page(self, before, host, timeout):
        """The page that SpringBoard's open-url made: one that is not in `before` (a set of (peer, page)), or one whose address
        now has `host`. None if neither shows within the timeout (a slow guest lists a new page late)."""
        end = time.time() + timeout
        while time.time() < end:
            self._check()
            pages = [p for p in self.pages() if (p['peer'], p['page']) not in self.broken]
            fresh = [p for p in pages if (p['peer'], p['page']) not in before or (host and host in p['url'])]
            if fresh:
                return max(fresh, key=lambda p: p['page'])
            time.sleep(1)
        return None

    def select(self, index=None, wait=60):
        """The page to work on: an index into pages(), default the newest one that has a real address."""
        pages = self.wait_for_pages(wait)
        if not pages:
            raise InspectorError('no web page is listed: is Safari running with a page open? (safari_ready opens one)')
        if index is None:
            # the newest page that has a real address and has not refused us (ids grow as pages are opened)
            usable = [p for p in pages if (p['peer'], p['page']) not in self.broken] or pages
            real = [p for p in usable if p['url'] and p['url'] != 'about:blank']
            return max(real or usable, key=lambda p: p['page'])
        if not 0 <= index < len(pages):
            raise InspectorError(f'page {index} is not in the list of {len(pages)}')
        return pages[index]

    # --- sessions --------------------------------------------------------------------------------------------------
    def attach(self, page, timeout=30):
        s = Session(self, page['peer'], page['page'])
        with self.cond:
            self.sessions[(s.peer, s.page)] = s
        try:
            s.start(timeout)
        except InspectorError:
            with self.cond:
                self.sessions.pop((s.peer, s.page), None)
                self.broken.add((s.peer, s.page))
            s.tasks.put(None)
            raise
        return s

    def session(self, index=None, wait=60):
        """A Session attached to that page (made once, then kept while the page and its process live)."""
        page = self.select(index, wait)
        s = self.sessions.get((page['peer'], page['page']))
        if s is None or s.target is None:
            s = self.attach(page)
        return s


class Session:
    """One frontend connection to a page. Handles the Target domain wrapping of recent WebKit: commands go out as
    Target.sendMessageToTarget and answers come back as Target.dispatchMessageFromTarget."""

    def __init__(self, inspector, peer, page):
        self.insp = inspector
        self.peer = peer
        self.page = page
        self.sender = str(uuid.uuid4()).upper()
        self.ids = itertools.count(1)
        self.target = None
        self.results = {}
        self.events = collections.deque(maxlen=5000)
        self.event_count = 0      # events seen so far (the deque drops the oldest)
        self.domains = []
        self.tasks = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name='inspector-session').start()

    def start(self, timeout):
        self.insp.send(self.peer, 'WIRSocketSetupMessage', {
            'WIRConnectionIdentifierKey': self.insp.connection_id,
            'WIRPageIdentifierKey': self.page,
            'WIRSenderKey': self.sender,
            'WIRAutomaticallyPause': False,
        })
        end = time.time() + timeout
        with self.insp.cond:
            while self.target is None and time.time() < end:
                self.insp._check()
                self.insp.cond.wait(0.5)
        if self.target is None:
            raise InspectorError('the page did not answer the setup (another frontend is attached, or the page was open before a snapshot '
                                 'was restored and is no longer inspectable: open_url makes a fresh page)')
        for domain in ('Console', 'Network', 'Page'):
            try:
                self.send(f'{domain}.enable', timeout=30)
                self.domains.append(domain)
            except InspectorError:
                pass

    def _on_message(self, name, info):
        raw = info.get('WIRRawDataKey') or info.get('WIRMessageDataKey') or info.get('WIRSocketDataKey')
        if raw is None:
            return
        try:
            message = json.loads(raw)
        except ValueError:
            return
        self._dispatch(message)

    def _dispatch(self, message):
        method = message.get('method')
        if method == 'Target.targetCreated':
            info = message['params']['targetInfo']
            if info.get('type') == 'page':
                if self.target is None or not info.get('isProvisional'):
                    self.target = info['targetId']
                if self.domains:
                    self.tasks.put(info['targetId'])   # a new target has nothing enabled
        elif method == 'Target.didCommitProvisionalTarget':
            self.target = message['params']['newTargetId']
        elif method == 'Target.targetDestroyed' and message['params'].get('targetId') == self.target:
            self.target = None
        elif method == 'Target.dispatchMessageFromTarget':
            self._dispatch(json.loads(message['params']['message']))
            return
        if 'id' in message and 'method' not in message:
            if message['id'] < WRAPPER_ID_BASE:      # the acknowledgement of a wrapper says nothing
                self.results[message['id']] = message
        else:
            self.events.append(message)
            self.event_count += 1

    def _post(self, payload):
        self.insp.send(self.peer, 'WIRSocketDataMessage', {
            'WIRConnectionIdentifierKey': self.insp.connection_id,
            'WIRPageIdentifierKey': self.page,
            'WIRSenderKey': self.sender,
            'WIRSocketDataKey': json.dumps(payload).encode(),
        })

    def _worker(self):
        """Enable the domains on every page target that appears after the first (a navigation makes a new one)."""
        while True:
            target = self.tasks.get()
            if target is None:
                return
            for domain in list(self.domains):
                with contextlib.suppress(InspectorError):
                    self.send(f'{domain}.enable', target=target, timeout=30)

    def send(self, method, params=None, timeout=60, target=None):
        mid = next(self.ids)
        command = {'id': mid, 'method': method, 'params': params or {}}
        target = target or self.target
        if target:
            self._post({'id': WRAPPER_ID_BASE + mid, 'method': 'Target.sendMessageToTarget',
                        'params': {'targetId': target, 'message': json.dumps(command)}})
        else:
            self._post(command)
        end = time.time() + timeout
        with self.insp.cond:
            while mid not in self.results and time.time() < end:
                self.insp._check()
                self.insp.cond.wait(0.5)
        reply = self.results.pop(mid, None)
        if reply is None:
            raise InspectorError(f'no answer to {method} in {timeout} s')
        if 'error' in reply:
            raise InspectorError(f'{method}: {reply["error"]}')
        return reply.get('result', {})

    def snapshot_events(self, keep=None):
        """The events as a list; with `keep` (a predicate), the events it rejects are removed afterwards, atomically."""
        with self.insp.cond:
            items = list(self.events)
            if keep is not None:
                self.events = collections.deque((m for m in items if keep(m)), maxlen=self.events.maxlen)
            return items

    def events_since(self, count):
        """The events that arrived after event number `count` (as returned by event_count), oldest first."""
        with self.insp.cond:
            fresh = self.event_count - count
            return list(self.events)[-fresh:] if fresh > 0 else []

    def wait_event(self, method, since, timeout, match=None):
        """Wait for an event of this method (or one of several: a tuple) that arrived after event number `since`."""
        methods = (method,) if isinstance(method, str) else tuple(method)
        end = time.time() + timeout
        with self.insp.cond:
            while time.time() < end:
                fresh = self.event_count - since
                for m in (list(self.events)[-fresh:] if fresh > 0 else []):
                    if m.get('method') in methods and (match is None or match(m.get('params', {}))):
                        return m
                self.insp._check()
                self.insp.cond.wait(0.5)
        return None

    def evaluate(self, expression, timeout=60, **kw):
        r = self.send('Runtime.evaluate', {'expression': expression, 'returnByValue': True, **kw}, timeout=timeout)
        if r.get('wasThrown'):
            raise InspectorError(f'exception: {r.get("result")}')
        return r['result'].get('value')
