# Changelog

## 0.2.0

- Web Inspector access: a relay in the guest takes the place of `webinspectord` (`guest/wi_relay.c`); `js`, `dom`, `navigate`, `reload`,
  `console_messages`, `network_log`, `inspector_pages`, `inspector_raw`, `inspector_close`.
- Keyboard: `keyboard_state`, `keyboard_show`, `keyboard_hide`, `keyboard_events`, `keyboard_check`, `tap_element`, `tap_css`, `fill`,
  `scroll_into_view`, with a measured mapping from page pixels to screen pixels.
- `safari_ready` and a page-less `open_url` through SpringBoard (not yet verified live); `vm_status` reports a boot where the touchscreen
  is not accepted.
- A Python package with one entry point (`ios-safari-mcp serve | doctor | build-image | version`), a Nix flake (package, relay, checks, dev
  shell), CI, 42 offline tests.
- `open_url` and `safari_ready` handle a restored snapshot: a page that no longer attaches is replaced through SpringBoard.
- `keyboard_hide method=done` presses Safari's Done button.
- Fixed in `vm_status`: the phase of an old boot no longer shows while QEMU is stopped, and a restored guest reports `running`.

## 0.1.0

Pixel-level server: boot, save and restore, screenshots, touch, typing on the on-screen keyboard.
