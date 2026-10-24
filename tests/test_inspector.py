"""The Web Inspector client and tools against a fake relay (tests/fakes.py): no VM needed."""
import json
import time

import pytest

from fakes import FakeRelay
from ios_safari_mcp import config
from ios_safari_mcp.inspector import Inspector, InspectorError, manager
from ios_safari_mcp.tools import inspector as tools


@pytest.fixture
def relay(monkeypatch):
    r = FakeRelay()
    monkeypatch.setattr(config, 'WI_PORT', r.port)
    config.ensure_dirs()
    manager._inspector = None
    yield r
    manager.close()


def text(result):
    return result['content'][0]['text']


def test_client_lists_pages_and_attaches(relay):
    insp = Inspector(relay.port, connect_timeout=5)
    pages = insp.wait_for_pages(5)
    assert [(p['peer'], p['page'], p['url']) for p in pages] == [(1, 1, 'http://example.com/')]
    for _ in range(50):                                   # the second listing may still be on its way
        if len(insp.pages(None)) == 2:
            break
        time.sleep(0.1)
    assert len(insp.pages(None)) == 2
    s = insp.session()
    assert s.target == 'page-7'
    assert s.evaluate('1 + 2') == 3
    assert s.evaluate('document.title') == 'Example Domain'
    with pytest.raises(InspectorError):
        s.evaluate('throw')
    assert insp.session() is s                            # kept
    insp.close()
    time.sleep(0.5)
    died = [m for m in relay.received if m[1] == 'WIRConnectionDiedMessage']
    assert died and died[-1][2]['WIRConnectionIdentifierKey'] == insp.connection_id


def test_a_second_client_gets_a_connection_after_the_first_closed(relay):
    first = Inspector(relay.port)
    first.close()
    second = Inspector(relay.port)
    assert second.wait_for_pages(5)
    second.close()


def test_connection_lost_is_reported_not_hung(relay):
    insp = Inspector(relay.port)
    insp.wait_for_pages(5)
    relay.conn.close()
    time.sleep(0.5)
    with pytest.raises(InspectorError, match='connection lost'):
        insp.session()._post({})          # the dead flag is checked on the way in


def test_control_command_open_url(relay):
    insp = Inspector(relay.port)
    assert insp.open_url('https://a.example/').startswith('ok')
    assert relay.opened == ['https://a.example/']
    with pytest.raises(InspectorError, match='unknown command'):
        raise InspectorError(insp.command('bogus'))
    insp.close()


def test_recorded_frames(relay, tmp_path):
    rec = tmp_path / 'frames.jsonl'
    insp = Inspector(relay.port, record=str(rec))
    insp.wait_for_pages(5)
    time.sleep(0.3)
    lines = [json.loads(line) for line in rec.read_text().splitlines()]
    assert lines and all('peer' in x and 'hex' in x for x in lines)
    insp.close()


# --- the tools ---------------------------------------------------------------------------------------------------------
def test_tools_pages_js_dom(relay):
    out = json.loads(text(tools.inspector_pages({'wait': 5})))
    assert out[0]['url'] == 'http://example.com/' and out[0]['index'] == 0
    assert json.loads(text(tools.js({'expression': '6 * 7'}))) == 42
    dom = json.loads(text(tools.dom({'selector': 'h1'})))
    assert dom['count'] == 1 and dom['items'][0]['tag'] == 'h1'


def test_navigate_with_a_page(relay):
    r = tools.open_url({'url': 'example.com', 'timeout': 10})
    assert 'through the inspector' in text(r), text(r)
    sent = [m for m in relay.received if m[1] == 'WIRSocketDataMessage']
    assert any('Page.navigate' in m[2]['WIRSocketDataKey'].decode() and 'https://example.com' in m[2]['WIRSocketDataKey'].decode() for m in sent)


def test_navigate_without_a_page_asks_springboard(relay, monkeypatch):
    relay.with_page = False
    r = tools.open_url({'url': 'https://example.com', 'timeout': 20})
    assert relay.opened == ['https://example.com'], text(r)
    assert 'SpringBoard' in text(r) and 'example.com' in text(r)


def test_a_page_that_will_not_attach_is_replaced_by_a_fresh_one(relay, monkeypatch):
    from ios_safari_mcp.inspector import client
    relay.refuse = {1}                                    # like a page that was open when a snapshot was restored
    monkeypatch.setattr(client.Session, 'start', _short_start(client.Session.start))
    r = tools.open_url({'url': 'https://example.com', 'timeout': 30})
    assert relay.opened == ['https://example.com'] and 'SpringBoard' in text(r) and 'did not answer the setup' in text(r), text(r)
    # the next call needs no second fallback: the refusing page is skipped and the newest page is used
    assert 'through the inspector' in text(tools.open_url({'url': 'example.org', 'timeout': 30}))


def _short_start(start):
    return lambda self, timeout: start(self, 2)


def test_full_url():
    f = manager.full_url
    assert f('example.com/a') == 'https://example.com/a'
    assert f('localhost:8080/x') == 'http://localhost:8080/x'
    assert f('10.0.2.2:8000') == 'http://10.0.2.2:8000'
    assert f('http://a.b/') == 'http://a.b/' and f('about:blank') == 'about:blank'


def test_stale_connections_of_an_earlier_run_are_ended(relay):
    manager._save_ids(['STALE-ID'])
    tools.inspector_pages({'wait': 5})
    time.sleep(0.3)
    assert 'STALE-ID' in [m[2]['WIRConnectionIdentifierKey'] for m in relay.received if m[1] == 'WIRConnectionDiedMessage']
    tools.inspector_close({})
    assert manager._stale_ids() == []


def test_missing_relay_says_what_to_do(monkeypatch):
    monkeypatch.setattr(config, 'WI_PORT', 1)
    manager._inspector = None
    from ios_safari_mcp import registry
    r = registry.call('inspector_pages', {'wait': 1})
    assert r['isError'] and 'build-image' in text(r)
