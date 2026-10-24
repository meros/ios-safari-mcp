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

### Fixed after an independent review (two reviewers, read-only, about 30 findings; each one confirmed and fixed has a test)

- **Guest relay:** a frame is written whole under one lock, with a 5 s send timeout, and a failed write ends the connection (a host that
  stopped reading used to block every thread and the relay for good); the client descriptor is swapped and closed under the lock (a
  reused descriptor could receive half a frame); values of `msgData` that are not data are ignored; messages over 1 MiB and
  connections over 64 are logged and refused; `open-url` refuses control characters, reads the 8 bit result of the call correctly, and
  runs at most four at a time; the first answer of a process that has not sent yet uses CoreFoundation's key.
- **Server:** a bad argument, a bad regular expression or a line that is not JSON no longer ends the server (the VM, the connection and
  the calibration live in it); the pid file is checked against `launch.sh` before `vm_stop` signals a group, a second `vm_start` while
  the first is starting is refused, a failed `vm_save` leaves no `.part` file and waits for the file to settle.
- **Tools:** `keyboard_show` checks that the focused field is the one asked for; `inspector_pages` indexes web pages only, the same list
  as `page=`; the default field of `keyboard_check` is the first visible, enabled one; a tap off the screen is refused; a navigation
  that did not happen is an error (and no longer falls back to typing); `tel:123` is not turned into a host and port; `console_messages
  clear=true` no longer races the reader thread; text with upper case letters is refused instead of typed in lower case.
- **Client:** one odd message no longer kills the reader thread; closing a dead connection works; session worker threads end.
- **Scripts:** `apply-to-tree.py` checks everything before it writes and refuses a directory that is not a hackios tree; the scripts use
  the flake's pinned nixpkgs when packaged; the QEMU wrapper accepts one forward rule only; CI has a read-only token.
- **Known and documented, not fixed:** two VMs of one hackios tree cannot run at once (the parent keeps one `vars.fd`); any process in the
  guest can reach the relay's port and replace the host's connection (see Trust in docs/architecture.md).

## 0.1.0

Pixel-level server: boot, save and restore, screenshots, touch, typing on the on-screen keyboard.
