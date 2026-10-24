"""Stand-ins for the parts of the system that need a running VM: the relay in the guest and QEMU's monitor."""
import json
import re
import socket
import socketserver
import struct
import threading
from pathlib import Path

from ios_safari_mcp.inspector import bplist15, decode

LISTING = {'messageName': 'WIRListingMessage', 'userInfo': {
    'WIRAutomationAvailabilityKey': 'WIRAutomationAvailabilityUnknown', 'WIRRemoteAutomationEnabledKey': False,
    'WIRListingKey': {'1': {'WIRTitleKey': 'Example Domain', 'WIRTypeKey': 'WIRTypeWebPage',
                            'WIRURLKey': 'http://example.com/', 'WIRPageIdentifierKey': 1}}}}
JS_LISTING = {'messageName': 'WIRListingMessage', 'userInfo': {
    'WIRListingKey': {'2': {'WIRTypeKey': 'WIRTypeJavaScript', 'WIRTitleKey': 'JSContext', 'WIRPageIdentifierKey': 2}}}}

CONTROL_PEER = 0xFFFFFFFF


class FakeRelay:
    """A TCP server with the frames of guest/wi_relay (u32 length | u32 peer | data). Peer 1 is Safari's app process with one
    web page, peer 2 a process with a JavaScript context. It answers the inspector protocol like WebKit does, for a few
    expressions, and keeps a small model of the page: the location, a keyboard that a tap opens and blur() closes."""

    def __init__(self, with_page=True):
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.with_page = with_page
        self.received = []          # (peer, messageName, userInfo) of what clients sent
        self.commands = []          # control commands
        self.evaluated = []         # expressions that reached the page
        self.conn = None
        self.url = 'http://example.com/'
        self.keyboard = False
        self.active = None          # the focused field, or None
        self.opened = []            # addresses that SpringBoard was asked to open
        self.refuse = set()         # page ids that do not answer the setup (as after a snapshot restore)
        self.extra = {}             # pages that open-url added: id -> url
        threading.Thread(target=self._serve, daemon=True).start()

    # --- frames ------------------------------------------------------------------------------------------------------
    def frame(self, peer, message):
        data = bplist15.encode(message) if isinstance(message, dict) else message
        body = struct.pack('>I', peer) + data
        self.conn.sendall(struct.pack('>I', len(body)) + body)

    def _read(self, n):
        buf = b''
        while len(buf) < n:
            c = self.conn.recv(n - len(buf))
            if not c:
                raise EOFError
            buf += c
        return buf

    def _serve(self):
        while True:
            self.conn, _ = self.srv.accept()
            try:
                if self.with_page or self.extra:
                    self.frame(1, self.listing())   # the relay replays what each process listed last
                self.frame(2, JS_LISTING)
                while True:
                    length = struct.unpack('>I', self._read(4))[0]
                    body = self._read(length)
                    peer, data = struct.unpack('>I', body[:4])[0], body[4:]
                    if peer == CONTROL_PEER:
                        self.commands.append(data.decode())
                        self._control(data.decode())
                        continue
                    msg = decode(data)
                    self.received.append((peer, msg['messageName'], msg.get('userInfo')))
                    self._answer(peer, msg)
            except (EOFError, OSError):
                pass
            except Exception:  # noqa: BLE001 - a bug in the fake must be visible, not end the thread silently
                import traceback
                traceback.print_exc()

    def listing(self):
        pages = {'1': {'WIRTitleKey': 'Example Domain', 'WIRTypeKey': 'WIRTypeWebPage', 'WIRURLKey': 'http://example.com/', 'WIRPageIdentifierKey': 1}} \
            if self.with_page else {}
        for pid, url in self.extra.items():
            pages[str(pid)] = {'WIRTitleKey': 'Opened', 'WIRTypeKey': 'WIRTypeWebPage', 'WIRURLKey': url, 'WIRPageIdentifierKey': pid}
        return {**LISTING, 'userInfo': {**LISTING['userInfo'], 'WIRListingKey': pages}}

    def _control(self, text):
        if text.startswith('open-url '):
            url = text[9:]
            self.opened.append(url)
            self.url = url
            if self.with_page:
                self.extra[12] = url                  # Safari opens a new tab
            else:
                self.with_page = True
                self.extra[1] = url
            self.frame(CONTROL_PEER, b'ok SpringBoard opens the address')
            self.frame(1, self.listing())
        else:
            self.frame(CONTROL_PEER, b'error unknown command')

    # --- the page ----------------------------------------------------------------------------------------------------
    def _raw(self, info, payload):
        self.frame(1, {'messageName': 'WIRRawDataMessage', 'userInfo': {
            'WIRMessageDataTypeKey': 'WIRMessageDataTypeFull', 'WIRDestinationKey': info['WIRSenderKey'],
            'WIRConnectionIdentifierKey': info['WIRConnectionIdentifierKey'],
            'WIRRawDataKey': json.dumps(payload, separators=(',', ':')).encode()}})

    def _answer(self, peer, msg):
        name, info = msg['messageName'], msg.get('userInfo') or {}
        if name == 'WIRApplicationGetListingMessage':
            self.frame(1, self.listing())
        elif name == 'WIRSocketSetupMessage':
            if info['WIRPageIdentifierKey'] in self.refuse:
                return
            self._raw(info, {'method': 'Target.targetCreated', 'params': {'targetInfo': {'targetId': 'page-7', 'type': 'page'}}})
        elif name == 'WIRSocketDataMessage':
            outer = json.loads(info['WIRSocketDataKey'])
            if outer['method'] == 'Target.sendMessageToTarget':
                cmd = json.loads(outer['params']['message'])
                self._raw(info, {'result': {}, 'id': outer['id']})
                reply, events = self._run(cmd)
                self._raw(info, {'method': 'Target.dispatchMessageFromTarget', 'params': {
                    'targetId': 'page-7', 'message': json.dumps(reply)}})
                for ev in events:
                    self._raw(info, {'method': 'Target.dispatchMessageFromTarget', 'params': {
                        'targetId': 'page-7', 'message': json.dumps(ev)}})
            else:   # a command without the Target wrapping, as WebKit answers it
                self._raw(info, {'error': {'code': -32601, 'message': f"'{outer['method'].split('.')[0]}' domain was not found"},
                                 'id': outer['id']})

    def viewport(self):
        h = 366 if self.keyboard else 635
        return {'width': 375, 'height': h}

    def _state(self):
        vv_h = 366 if self.keyboard else 635
        active = {'tag': 'input', 'id': 'q', 'text': 'Search', 'top': 100, 'bottom': 149, 'position': 'static'} if self.active else None
        return {
            'keyboardVisible': self.keyboard, 'keyboardHeight': 635 - vv_h,
            'layoutViewport': {'width': 375, 'height': 635},
            'visualViewport': {'width': 375, 'height': vv_h, 'offsetTop': 0, 'offsetLeft': 0, 'scale': 1},
            'scroll': {'x': 0, 'y': 0}, 'active': active, 'activeVisible': True if active else None,
            'coveredByKeyboard': [{'tag': 'a', 'text': 'Card', 'top': 290, 'bottom': 762, 'position': 'static'}] if self.keyboard else [],
            'fixed': [{'tag': 'div', 'text': 'Bottom bar', 'top': 579, 'bottom': 635, 'position': 'fixed',
                       'behindKeyboard': self.keyboard}],
            'popups': [{'tag': 'ul', 'text': 'Suggestions', 'top': 150, 'bottom': 300, 'position': 'absolute',
                        'behindKeyboard': False}] if self.keyboard else [],
        }

    def _run(self, cmd):
        method, params = cmd['method'], cmd.get('params', {})
        events = []
        if method == 'Page.navigate':
            self.url = params['url']
            events = [{'method': 'Page.frameNavigated', 'params': {'frame': {'id': '0.1', 'url': self.url}}}]
            return {'id': cmd['id'], 'result': {}}, events
        if method != 'Runtime.evaluate':
            return {'id': cmd['id'], 'result': {}}, events
        expr = params['expression']
        self.evaluated.append(expr)
        value = 'Example Domain'
        if 'readyState' in expr:
            value = f'complete {self.url}'
        elif expr == 'throw':
            return {'id': cmd['id'], 'result': {'result': {'type': 'object'}, 'wasThrown': True}}, events
        elif 'keyboardVisible' in expr:
            value = self._state()
        elif 'return [innerWidth' in expr:                           # LAYOUT_KEY
            value = [375, 635, 0, 0, 1, 366 if self.keyboard else 635]
        elif 'getElementById' in expr and 'document.createElement' in expr or 'd.remove()' in expr:   # MARKER_ADD
            value = True
        elif 'scrollIntoView' in expr:
            value = True if '"#missing"' not in expr else None
        elif 'getBoundingClientRect' in expr and 'r.left + r.width / 2' in expr:   # RECT
            value = None if '"#missing"' in expr else {'x': 252.0, 'y': 124.0, 'width': 200, 'height': 40, 'tag': 'input', 'text': 'Search'}
        elif 'a.blur()' in expr:
            self.keyboard, self.active = False, None
            value = True
        elif 'HTMLInputElement.prototype' in expr:                    # FILL
            value = re.search(r'\)\(("[^"]*"), 0, ("[^"]*"),', expr) and json.loads(re.search(r'\)\("[^"]*", 0, ("[^"]*"),', expr).group(1))
        elif '__iosSafariMcp' in expr and 'events.slice' in expr:     # RECORDER_READ
            value = {'total': 2, 'events': [{'type': 'focusin', 't': 5}, {'type': 'visualViewport.resize', 't': 900}]}
        elif '__iosSafariMcp' in expr and 'installed' in expr:
            value = {'installed': True, 'events': 0}
        elif expr.startswith('(function(sel, what, limit)'):
            value = {'count': 1, 'items': [{'tag': 'h1', 'text': 'Example Domain', 'attrs': {}, 'visible': True,
                                            'rect': {'x': 0, 'y': 0, 'width': 10, 'height': 10}}], 'viewport': {}}
        elif re.fullmatch(r'[0-9 +*-]+', expr):
            value = eval(expr)  # noqa: S307 (digits and operators only)
        return {'id': cmd['id'], 'result': {'result': {'type': 'string', 'value': value}, 'wasThrown': False}}, events

    def tap(self, fx, fy):
        """A touch on the screen: opens the keyboard when it lands on the field (the model says where the field is)."""
        if 0.60 < fx < 0.72 and 0.19 < fy < 0.22:
            self.keyboard, self.active = True, 'q'
        elif self.keyboard and 0.88 < fx < 0.93 and 0.55 < fy < 0.57:      # the Done button above the keyboard
            self.keyboard, self.active = False, None


class FakeMonitor:
    """A stand-in for QEMU's human monitor: answers `screendump FILE` with the next frame (a solid shade per frame)."""

    def __init__(self, port, frames):
        outer = self
        self.frames, self.index, self.dumps = frames, 0, 0

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"QEMU 11.1.1 monitor - type 'help' for more information\r\n(qemu) ")
                for line in self.rfile:
                    cmd = line.decode().strip()
                    if cmd.startswith('screendump '):
                        shade = outer.frames[min(outer.index, len(outer.frames) - 1)]
                        outer.index += 1
                        outer.dumps += 1
                        w, h = 32, 32
                        Path(cmd.split(' ', 1)[1]).write_bytes(f'P6\n{w} {h}\n255\n'.encode() + bytes([shade]) * (w * h * 3 + h * 4))
                    self.wfile.write(b'\r\n(qemu) ')

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
        self.server = Server(('127.0.0.1', port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
