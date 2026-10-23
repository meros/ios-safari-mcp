"""The keyboard tools and the calibration, against the fake relay and a fake screen."""
import json
import time

import pytest

from fakes import FakeRelay
from ios_safari_mcp import calibration, config, qemu, registry, screen
from ios_safari_mcp.inspector import manager
from ios_safari_mcp.tools import keyboard as kb


def text(result):
    return result['content'][0]['text']


def data(result):
    return json.loads(text(result))


def fake_screen(origin=(0, 132), scale=3, size=14, at=(6, 96), draw=True):
    """A screenshot function whose picture has the magenta marker where a phone with this origin and scale shows it."""
    def shot(div):
        w, h = 1125 // div, 2436 // div
        rgb = bytearray(w * h * 3)
        if draw:
            x0, y0 = origin[0] + at[0] * scale, origin[1] + at[1] * scale
            for y in range(int(y0) // div, int(y0 + size * scale) // div):
                for x in range(int(x0) // div, int(x0 + size * scale) // div):
                    rgb[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes((255, 0, 255))
        return w, h, bytes(rgb), b'png'
    return shot


@pytest.fixture
def phone(monkeypatch):
    r = FakeRelay()
    monkeypatch.setattr(config, 'WI_PORT', r.port)
    config.ensure_dirs()
    manager._inspector = None
    calibration._cache = None
    shots = []
    monkeypatch.setattr(screen, 'screenshot', lambda div: shots.append(div) or fake_screen()(div))
    monkeypatch.setattr(qemu, 'alive', lambda: True)
    taps = []

    def touch(*argv):
        taps.append(argv)
        if argv[0] == 'tap':
            r.tap(float(argv[1]), float(argv[2]))
        return True, ''
    monkeypatch.setattr(qemu, 'touch', touch)
    monkeypatch.setattr(time, 'sleep', lambda s: time.perf_counter())   # the guest's slow animations are not simulated
    r.taps, r.shots = taps, shots
    yield r
    manager.close()


def test_find_marker_and_calibration(phone):
    w, h, rgb, _ = screen.screenshot(2)
    assert calibration.find_marker(rgb, w, h, 2) == (18, 420, 60, 462)
    cal = calibration.calibrate(manager.session({'wait': 5}))
    assert (cal.origin_x, cal.origin_y, cal.scale) == (0, 132, 3)
    assert cal.to_fractions(252, 124) == (round(756 / 1125, 4), round(504 / 2436, 4))


def test_calibration_is_cached_until_the_layout_changes(phone):
    s = manager.session({'wait': 5})
    calibration.calibrate(s)
    n = len(phone.shots)
    calibration.calibrate(s)
    assert len(phone.shots) == n                          # no second marker
    phone.keyboard = True                                 # the layout changes: measure again
    calibration.calibrate(s)
    assert len(phone.shots) > n


def test_calibration_fails_with_a_clear_message_when_the_marker_is_not_seen(phone, monkeypatch):
    monkeypatch.setattr(screen, 'screenshot', lambda div: fake_screen(draw=False)(div))
    with pytest.raises(Exception, match='did not show'):
        calibration._measure(manager.session({'wait': 5}), timeout=0.01)


def test_keyboard_show_taps_the_field_and_waits(phone):
    st = data(kb.keyboard_show({'selector': '#q'}))
    assert st['keyboardVisible'] and st['keyboardHeight'] == 269
    assert st['tapped']['screen_fraction'] == {'x': round(756 / 1125, 4), 'y': round(504 / 2436, 4)}
    assert st['tapped']['pixels_per_css_pixel'] == 3
    assert st['active']['tag'] == 'input' and st['activeVisible'] is True
    assert phone.taps and phone.taps[0][0] == 'tap'


def test_keyboard_show_when_already_up_does_not_tap_again(phone):
    kb.keyboard_show({'selector': '#q'})
    n = len(phone.taps)
    again = data(kb.keyboard_show({'selector': '#q'}))
    assert again['already_visible'] and len(phone.taps) == n


def test_keyboard_show_unknown_selector(phone):
    r = registry.call('keyboard_show', {'selector': '#missing'})
    assert r['isError'] and 'no element' in text(r)


def test_keyboard_state(phone):
    assert data(kb.keyboard_state({}))['keyboardVisible'] is False
    kb.keyboard_show({'selector': '#q'})
    st = data(kb.keyboard_state({}))
    assert st['keyboardVisible'] and st['fixed'][0]['behindKeyboard'] and st['popups']


def test_keyboard_hide_by_blur_recovers_the_viewport(phone):
    kb.keyboard_show({'selector': '#q'})
    out = data(kb.keyboard_hide({}))
    assert out['method'] == 'blur' and out['viewport_recovered'] is True and out['keyboardVisible'] is False


def test_keyboard_hide_when_already_hidden(phone):
    assert data(kb.keyboard_hide({}))['already_hidden'] is True


def test_keyboard_hide_rejects_an_unknown_method(phone):
    kb.keyboard_show({'selector': '#q'})
    r = registry.call('keyboard_hide', {'method': 'shake'})
    assert r['isError'] and 'blur, return or tap_outside' in text(r)


def test_keyboard_events(phone):
    assert data(kb.keyboard_events({'action': 'start'}))['installed'] is True
    ev = data(kb.keyboard_events({'action': 'read'}))
    assert [e['type'] for e in ev['events']] == ['focusin', 'visualViewport.resize']


def test_keyboard_check_reports_a_fixed_bar_behind_the_keyboard(phone):
    r = kb.keyboard_check({'selector': '#q'})
    report = json.loads(r['content'][0]['text'])
    assert report['verdict'] == 'problems'
    assert any('Bottom bar' in p and 'behind the keyboard' in p for p in report['problems']), report['problems']
    assert report['keyboard_height_css_px'] == 269
    assert report['field']['tag'] == 'input'
    assert report['after_hide']['viewport_recovered'] is True
    assert any(c['type'] == 'image' for c in r['content'])
    assert report['events_seen'] == {'focusin': 1, 'visualViewport.resize': 1}


def test_tap_element_and_tap_css_and_fill(phone):
    out = data(kb.tap_element({'selector': '#q'}))
    assert out['element']['tag'] == 'input' and out['screen_fraction']['x'] == round(756 / 1125, 4)
    out = data(kb.tap_css({'x': 252, 'y': 124}))
    assert out['css'] == {'x': 252, 'y': 124}
    assert data(kb.fill({'selector': '#q', 'value': 'Bullerbygatan'}))['value'] == 'Bullerbygatan'
