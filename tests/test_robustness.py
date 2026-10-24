"""What the independent review found: each of these used to go wrong. One test per finding."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fakes import FakeRelay
from ios_safari_mcp import calibration, config, qemu, registry, screen, vm
from ios_safari_mcp.inspector import manager
from ios_safari_mcp.results import ToolError
from ios_safari_mcp.tools import inspector as tools
from ios_safari_mcp.tools import keyboard as kb

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bin', 'ios-safari-mcp')


def text(result):
    return result['content'][0]['text']


@pytest.fixture
def relay(monkeypatch):
    r = FakeRelay()
    monkeypatch.setattr(config, 'WI_PORT', r.port)
    config.ensure_dirs()
    manager._inspector = None
    calibration._cache = None
    monkeypatch.setattr(qemu, 'alive', lambda: True)
    monkeypatch.setattr(screen, 'screenshot', lambda div: __import__('test_keyboard_tools').fake_screen()(div))
    monkeypatch.setattr(qemu, 'touch', lambda *argv: (r.tap(float(argv[1]), float(argv[2])) if argv[0] == 'tap' else None, (True, ''))[1])
    monkeypatch.setattr(time, 'sleep', lambda s: time.perf_counter())
    yield r
    manager.close()


# --- the server survives bad input ---------------------------------------------------------------------------------------
def test_the_server_answers_garbage_and_keeps_serving():
    p = subprocess.Popen([sys.executable, '-I', CLI, 'serve'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        def ask(line):
            p.stdin.write(line + '\n')
            p.stdin.flush()
            return json.loads(p.stdout.readline())
        assert ask('this is not json')['error']['code'] == -32700
        assert ask('[1, 2]')['error']['code'] == -32700
        bad = ask(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'log_tail', 'arguments': {'pattern': '('}}}))
        assert bad['result']['isError'] and 'regular expression' in bad['result']['content'][0]['text']
        odd = ask(json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'navigate', 'arguments': {'url': 5}}}))
        assert odd['result']['isError']
        assert 'tools' in ask(json.dumps({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'}))['result']
    finally:
        p.stdin.close()
        p.wait(timeout=5)


def test_an_unexpected_exception_is_an_error_result_not_a_crash(monkeypatch):
    def boom(args):
        raise RuntimeError('a bug')
    monkeypatch.setitem(registry.TOOLS, 'wait', registry.Tool('wait', boom, 'x', {}, ()))
    r = registry.call('wait', {})
    assert r['isError'] and 'internal error' in text(r) and 'RuntimeError' in text(r)


# --- the launcher -------------------------------------------------------------------------------------------------------
def test_a_stale_pid_file_is_not_a_launcher(monkeypatch):
    config.ensure_dirs()
    Path(config.PIDFILE).write_text(f'{os.getpid()} 1000.0 \n')         # alive, but it started long after the file says: a reused pid
    assert vm.launcher_pid() is None
    assert vm.stop()['launcher_alive'] is False            # and stop() does not signal this process' group


def test_a_second_start_while_the_launcher_runs_is_refused():
    config.ensure_dirs()
    # any process will do: the real launcher replaces its own image with exec, so its command line says nothing
    p = subprocess.Popen(['sleep', '60'], start_new_session=True)
    try:
        Path(config.PIDFILE).write_text(f'{p.pid} {time.time()} \n')
        assert vm.launcher_pid() == p.pid
        with pytest.raises(ToolError, match='already running or starting'):
            vm.start()
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


# --- the inspector tools ------------------------------------------------------------------------------------------------
def test_page_index_is_a_position_among_web_pages(relay):
    out = json.loads(text(tools.inspector_pages({'wait': 5, 'all': True})))
    web = [p for p in out if p['type'] == 'WIRTypeWebPage']
    other = [p for p in out if p['type'] != 'WIRTypeWebPage']
    assert [p['index'] for p in web] == [0] and other and all(p['index'] is None for p in other)


def test_console_tools(relay):
    s = manager.session({'wait': 5})
    for i in range(5):
        s._dispatch({'method': 'Console.messageAdded', 'params': {'message': {'text': f'm{i}', 'level': 'log'}}})
    assert [m['text'] for m in json.loads(text(tools.console_messages({'limit': 2})))] == ['m3', 'm4']
    assert json.loads(text(tools.console_messages({'limit': 0}))) == []
    assert len(json.loads(text(tools.console_messages({'clear': True})))) == 5
    assert json.loads(text(tools.console_messages({}))) == []


def test_a_navigation_that_did_not_happen_is_an_error(relay):
    relay.no_navigate = True
    r = registry.call('open_url', {'url': 'example.org', 'timeout': 2})
    assert r['isError'] and 'did not navigate' in text(r)


def test_full_url_keeps_schemes_that_end_in_digits():
    assert manager.full_url('tel:123') == 'tel:123'
    assert manager.full_url('mailto:a@b.se') == 'mailto:a@b.se'
    assert manager.full_url('example.com:8080/x') == 'https://example.com:8080/x'
    assert manager.full_url('localhost:3000') == 'http://localhost:3000'


def test_closing_after_the_relay_died_is_not_an_error(relay):
    tools.inspector_pages({'wait': 5})
    relay.conn.close()
    for _ in range(100):                 # until the reader has seen the connection end
        if manager._inspector.dead:
            break
        threading.Event().wait(0.05)
    assert text(tools.inspector_close({})) == 'closed'
    assert manager._inspector is None and manager._stale_ids() == []


# --- the keyboard tools -------------------------------------------------------------------------------------------------
def test_show_on_another_field_taps_it_again(relay):
    first = json.loads(text(kb.keyboard_show({'selector': '#q'})))
    assert first['active']['id'] == 'q'
    same = json.loads(text(kb.keyboard_show({'selector': '#q'})))
    assert same['already_visible']
    other = json.loads(text(kb.keyboard_show({'selector': '#other'})))
    assert 'already_visible' not in other and other['active']['id'] == 'other'


def test_the_default_field_is_the_first_visible_one(relay):
    r = kb.keyboard_check({})
    report = json.loads(r['content'][0]['text'])
    assert report['field']['tag'] == 'input'


def test_a_tap_point_off_the_screen_is_refused(relay):
    with pytest.raises(ToolError, match='off the screen'):
        kb.tap_css({'x': 252, 'y': 3000})
