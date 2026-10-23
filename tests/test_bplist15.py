"""The property list format of the apps: decoding what Safari sent, and encoding what it accepts."""
import json
import os
from pathlib import Path

import pytest

from ios_safari_mcp.inspector import bplist15

HERE = os.path.dirname(os.path.abspath(__file__))


def handshake():
    return json.loads(Path(HERE, 'fixtures', 'handshake.json').read_text())


def test_decode_what_safari_sent():
    h = handshake()
    assert bplist15.decode(bytes.fromhex(h['syn'])) == {'messageName': 'syn'}
    assert bplist15.decode(bytes.fromhex(h['check'])) == {'messageName': 'check'}


def test_encoding_is_byte_for_byte_what_safari_sends():
    # 39 bytes: the message; the apps pad it with zeros to 85
    assert bplist15.encode({'messageName': 'syn'}) == bytes.fromhex(handshake()['syn'])[:39]
    assert bplist15.encode({'messageName': 'check'}) == bytes.fromhex(handshake()['check'])[:41]


def test_round_trip_long_strings_data_and_big_integers():
    d = {'a': 'x' * 200, 'n': 300, 'big': 70000, 't': True, 'f': False, 'b': b'y' * 300, 'd': {'k': 'v'}, 'ö': 'drömbostad'}
    assert bplist15.decode(bplist15.encode(d)) == d


def test_listing_message():
    m = {'messageName': 'WIRListingMessage', 'userInfo': {'WIRListingKey': {'1': {
        'WIRTitleKey': 'Example Domain', 'WIRTypeKey': 'WIRTypeWebPage', 'WIRURLKey': 'http://example.com/', 'WIRPageIdentifierKey': 1}}}}
    assert bplist15.decode(bplist15.encode(m)) == m


def test_recorded_traffic_decodes():
    path = os.path.join(HERE, 'fixtures', 'traffic.jsonl')
    if not os.path.exists(path):
        pytest.skip('no recorded traffic (Inspector(record=FILE) writes some)')
    lines = [json.loads(line) for line in Path(path).read_text().splitlines()]
    assert lines
    for rec in lines:
        bplist15.decode(bytes.fromhex(rec['hex']))


def test_refuses_what_it_cannot_write():
    with pytest.raises(bplist15.Bad):
        bplist15.encode({'a': object()})
    with pytest.raises(bplist15.Bad):
        bplist15.decode(b'bplist00' + b'\0' * 30)
