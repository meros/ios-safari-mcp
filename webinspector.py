"""Client for the guest's Web Inspector, through guest/wi_relay (TCP, host port 27753 by default).

The guest relay stands in for webinspectord: Safari's processes connect to it and send property lists with
messageName and userInfo (see guest/wi_relay.c for the framing). This module decodes those (tests/bplist15.py), keeps
a table of what each process lists as inspectable, and speaks WebKit's inspector protocol to a page:

    insp = Inspector()                  # connects, waits for the listing
    insp.pages()                        # [{'peer': 1, 'page': 1, 'type': 'WIRTypeWebPage', 'url': ..., 'title': ...}]
    s = insp.attach(page)               # a Session
    s.send('Runtime.evaluate', {'expression': 'document.title', 'returnByValue': True})

The wire format towards the apps was written from WebKit's open-source constants (WIR*Key, WIR*Message) and is checked
against the traffic of the guest; set Inspector(debug=True) to print every message.
"""
import collections
import json
import plistlib
import queue
import socket
import struct
import sys
import threading
import time
import uuid

sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), 'tests'))
import bplist15  # noqa: E402


class InspectorError(Exception):
    pass


def decode(blob):
    """A property list from an app: bplist15 (what the apps send) or bplist00."""
    if blob[:8] == b'bplist00':
        return plistlib.loads(blob)
    return bplist15.decode(blob)


def encode(message_name, user_info, fmt='15'):
    """A property list for an app: bplist15, the format the apps send (tests/bplist15.py), or bplist00."""
    message = {'messageName': message_name, 'userInfo': user_info}
    if fmt == '00':
        return plistlib.dumps(message, fmt=plistlib.FMT_BINARY)
    return bplist15.encode(message)


class Inspector:
    def __init__(self, port=27753, host='127.0.0.1', debug=False, connect_timeout=30, fmt='15'):
        self.debug = debug
        self.fmt = fmt
        self.sock = socket.create_connection((host, port), timeout=connect_timeout)
        self.sock.settimeout(None)
        self.lock = threading.Lock()
        self.cond = threading.Condition()
        self.peers = {}          # peer number -> {'listing': {page id: info}, 'seen': time}
        self.log = []            # (time, peer, message name, user info) of everything received
        self.sessions = {}       # (peer, page) -> Session
        self.connection_id = str(uuid.uuid4()).upper()
        self.dead = None
        threading.Thread(target=self._reader, daemon=True).start()

    # --- transport -------------------------------------------------------------------------------------------------
    def _read(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise EOFError('closed')
            buf += chunk
        return buf

    def _reader(self):
        try:
            while True:
                length = struct.unpack('>I', self._read(4))[0]
                body = self._read(length)
                peer = struct.unpack('>I', body[:4])[0]
                data = body[4:]
                if not data:
                    with self.cond:
                        self.peers.pop(peer, None)
                        self.cond.notify_all()
                    continue
                try:
                    message = decode(data)
                except Exception as e:  # noqa: BLE001
                    if self.debug:
                        print('undecodable from peer', peer, e, data[:40].hex(), file=sys.stderr)
                    continue
                self._handle(peer, message)
        except Exception as e:  # noqa: BLE001
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

    def send(self, peer, message_name, user_info):
        data = encode(message_name, user_info, self.fmt)
        body = struct.pack('>I', peer) + data
        if self.debug:
            print('->', peer, message_name, repr(user_info)[:400], file=sys.stderr)
        with self.lock:
            self.sock.sendall(struct.pack('>I', len(body)) + body)

    def forget_connection(self, connection_id):
        """Tell every process that a frontend connection is over (also one a crashed earlier run left behind): a page
        takes one frontend at a time."""
        self.send(0, 'WIRConnectionDiedMessage', {'WIRConnectionIdentifierKey': connection_id})

    def close(self):
        try:
            self.forget_connection(self.connection_id)
            time.sleep(0.5)
        finally:
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

    def wait_for_pages(self, timeout=60, kinds=('WIRTypeWebPage',)):
        end = time.time() + timeout
        asked = 0
        while time.time() < end:
            if self.dead:
                raise InspectorError(f'connection lost: {self.dead}')
            if time.time() - asked > 8:   # the apps list their pages when something changes, so ask
                self.request_listing()
                asked = time.time()
            with self.cond:
                if any(p['listing'] for p in self.peers.values()) and self.pages(kinds):
                    return self.pages(kinds)
                self.cond.wait(1)
        return self.pages(kinds)

    def request_listing(self):
        """Ask every process (peer 0) for its list of inspectable pages."""
        self.send(0, 'WIRApplicationGetListingMessage', {'WIRConnectionIdentifierKey': self.connection_id})

    def select(self, index=None, wait=60):
        """The page to work on: index into pages(), default the first one that has a real address."""
        pages = self.wait_for_pages(wait)
        if not pages:
            raise InspectorError('no web page is listed: is Safari running with a page open?')
        if index is None:
            real = [p for p in pages if p['url'] and p['url'] != 'about:blank']
            return (real or pages)[0]
        if not 0 <= index < len(pages):
            raise InspectorError(f'page {index} is not in the list of {len(pages)}')
        return pages[index]

    def session(self, index=None, wait=60):
        """An attached Session for that page (made once, then kept)."""
        page = self.select(index, wait)
        key = (page['peer'], page['page'])
        s = self.sessions.get(key)
        if s is None or s.target is None:
            s = self.attach(page)
        return s

    # --- sessions --------------------------------------------------------------------------------------------------
    def attach(self, page, timeout=30):
        s = Session(self, page['peer'], page['page'])
        self.sessions[(s.peer, s.page)] = s
        s.start(timeout)
        return s


class Session:
    """One frontend connection to a page. Handles the Target domain wrapping of recent WebKit."""

    def __init__(self, inspector, peer, page):
        self.insp = inspector
        self.peer = peer
        self.page = page
        self.sender = str(uuid.uuid4()).upper()
        self.next_id = 1
        self.target = None
        self.results = {}
        self.events = collections.deque(maxlen=5000)
        self.domains = []
        self.tasks = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

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
                self.insp.cond.wait(0.5)
        if self.target is None:
            raise InspectorError('the page did not answer the setup (is another frontend attached to it?)')
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
        except Exception:  # noqa: BLE001
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
            self.results[message['id']] = message
        else:
            self.events.append(message)

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
            for domain in list(self.domains):
                try:
                    self.send(f'{domain}.enable', target=target, timeout=30)
                except InspectorError:
                    pass

    def send(self, method, params=None, timeout=60, target=None):
        mid = self.next_id
        self.next_id += 1
        command = {'id': mid, 'method': method, 'params': params or {}}
        target = target or self.target
        if target:
            self._post({'id': mid + 100000, 'method': 'Target.sendMessageToTarget',
                        'params': {'targetId': target, 'message': json.dumps(command)}})
        else:
            self._post(command)
        wait_id = mid
        end = time.time() + timeout
        with self.insp.cond:
            while wait_id not in self.results and time.time() < end:
                if self.insp.dead:
                    raise InspectorError(f'connection lost: {self.insp.dead}')
                self.insp.cond.wait(0.5)
        reply = self.results.pop(wait_id, None)
        if reply is None:
            raise InspectorError(f'no answer to {method} in {timeout} s')
        if 'error' in reply:
            raise InspectorError(f'{method}: {reply["error"]}')
        return reply.get('result', {})

    def evaluate(self, expression, **kw):
        r = self.send('Runtime.evaluate', {'expression': expression, 'returnByValue': True, **kw})
        if r.get('wasThrown'):
            raise InspectorError(f'exception: {r.get("result")}')
        return r['result'].get('value')
