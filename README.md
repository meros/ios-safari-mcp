# ios-safari-mcp

An MCP server that drives **iOS 16.5 Safari** running in QEMU on the [HackiOS](https://github.com/oosexgang/hackios-xnu-kernel)
kernel, so that an agent (or a test) can look at a real iOS WebKit page, read its DOM, run JavaScript in it, watch its console
and network, and work with the **software keyboard** the way iOS does it.

It is a companion to the HackiOS repositories (the kernel and the userland that boot unmodified iOS in QEMU). It adds no
Apple code and changes none of the parent's source by hand: it patches a few lines of the parent's build at build time
(`scripts/apply-to-tree.py`) and ships one small guest program, the relay.

| | |
|---|---|
| **See** | `screenshot` (0.22 s), `settle` (wait for the screen to stop changing) |
| **Touch** | `tap`, `swipe`, `scroll`, `key`, `tap_element` (a real tap at a CSS selector's measured position) |
| **Read and run** | `js`, `dom`, `console_messages`, `network_log`, `inspector_raw` (any Web Inspector command) |
| **Navigate** | `open_url`, `navigate`, `reload`, `safari_ready` (no typing, no tap) |
| **Keyboard** | `keyboard_state`, `keyboard_show`, `keyboard_hide`, `keyboard_events`, `keyboard_check`, `fill`, `type_text` |
| **VM** | `vm_start` (boot or restore), `vm_stop`, `vm_save`, `vm_snapshots`, `vm_status`, `wait_for`, `guest_process`, `log_tail` |

41 tools in all; `tools/list` has the full schemas. No runtime dependencies beyond Python 3 and Nix.

## Status

Read this before relying on it. "Verified live" means: run against the real guest on 23 October 2026, on one machine.

| | |
|---|---|
| Verified live | the relay takes `webinspectord`'s place and Safari's processes connect to it; page listing; attach; `Runtime.evaluate`; DOM queries; `Page.navigate`; console and network events; screenshots, taps, boot, save and restore (earlier sessions); the keyboard opening on a real tap and closing on `blur()` (through a prototype script with the same logic as the keyboard tools) |
| Verified offline | 38 tests (`nix flake check`): the property list format against bytes captured from Safari, the client and every tool against a fake relay, calibration, the keyboard tools, the JSON-RPC surface, lint |
| **Not yet verified live** | the packaged tools through one long-lived MCP session; the relay's `open-url` command (page-less navigation through SpringBoard) and its replace-the-old-client loop; `safari_ready` end to end; snapshot save and restore with this image; `keyboard_check` as shipped (its parts were run, the whole was not); the relay signed as shipped (only the SpringBoard entitlement; the live runs used a different set) |

`tests/live/smoke.py` runs all of that against a running VM and prints a line with the time for each step.

## Quick start

You need Linux, [Nix](https://nixos.org/download) with flakes, 12 GB of RAM and 10 GB of disk. The VM boots in 9 to 14 minutes
(software emulation, no KVM on an x86 host); a restored snapshot takes about 30 seconds.

1. **Get the parent and the iOS files.** Check out the HackiOS kernel and userland repositories into one tree as their README
   says, then fetch iOS 16.5 (about 6 GB, from Apple's servers): `cd ios165 && ./fetch.sh`. The parent has fixes that were still
   open pull requests when this was written (the black screen, the missing on-demand jobs, the `/bin/bash` shebangs on NixOS, the
   7-Zip version): [docs/findings.md](docs/findings.md) lists them; without the first two the screen stays black and there is no network.
2. **Check the setup.** `nix run github:meros/ios-safari-mcp -- doctor` lists what is missing, with the command that fixes each
   item. Set `HACKIOS_DIR` to the tree (the default is `../hackios` next to a checkout, then `~/hackios`).
3. **Build the image.** `nix run github:meros/ios-safari-mcp -- build-image` builds the relay, patches the parent's build and
   builds the image. It takes a few minutes and needs the iOS files.
4. **Register the server.**
   ```sh
   claude mcp add ios-safari -e HACKIOS_DIR=$HOME/hackios -- nix run github:meros/ios-safari-mcp --
   ```
   or in any MCP client's config:
   ```json
   { "mcpServers": { "ios-safari": { "command": "nix", "args": ["run", "github:meros/ios-safari-mcp", "--"],
                                     "env": { "HACKIOS_DIR": "/home/you/hackios" } } } }
   ```
   From a checkout, use `bin/ios-safari-mcp` as the command (no install).
5. **Use it.** `vm_start`, `wait_for springboard_owns_screen`, `safari_ready` with a `url`, then the tools below. Save a
   snapshot once Safari is up (`vm_save safari-ready`) and every later session starts with `vm_start snapshot=safari-ready`.

## A session

```
vm_start                         -> boots (minutes)           | or: vm_start snapshot=safari-ready (30 s)
wait_for springboard_owns_screen
safari_ready url=https://example.com
js  expression="document.title"  -> "Example Domain"
dom selector=".CookieConsent button" what=text
tap_element selector=".CookieConsent-button.nb-button--blue"
keyboard_check selector="input[placeholder^='Område']" value="Bullerbygatan"
                                 -> verdict, what the keyboard covers, a screenshot with it up, viewport recovery
```

## The keyboard

On iOS Safari the keyboard does not resize the page: `window.innerHeight` stays, `visualViewport.height` shrinks, `position: fixed`
elements stay put and can end up behind the keyboard, and script cannot open the keyboard (`focus()` outside a user gesture
focuses the field without it). So the tools work with a real touch:

- `keyboard_state` returns both viewports, the keyboard height in CSS px, the focused field and whether it is visible above the
  keyboard, interactive elements and popups that reach under it, and fixed or sticky bars (`behindKeyboard`).
- `keyboard_show selector=...` scrolls the field into view, finds it on the screen, taps it and waits for the viewport to shrink.
  `tap_element` and `tap_css` use the same measured mapping from page to screen pixels (a 14 px marker drawn in the page and found
  in a screenshot, cached until the layout changes).
- `keyboard_hide` closes it by `blur` (default), the return key, or a tap outside, and waits for the viewport to recover.
- `keyboard_events` records focus, key, composition and viewport events with the viewport numbers at each moment.
- `keyboard_check` does all of it for one field and reports `ok` or `problems`: the field hidden, a fixed bar behind the keyboard, a
  suggestion popup reaching under it, a viewport that does not recover.
- `fill` sets a value fast (native setter, `input` and `change` events); it is synthetic. `type_text` taps the on-screen keys
  (about 2.5 s a key) for real key events, autocorrect and composition. Key positions are verified for letters on Safari's address
  bar keyboard only; digits and symbols follow the standard layout and other keyboard types (email, number pad) are not mapped.

On the search field of a property marketplace (iPhone X size, 23 October 2026): the keyboard was 269 CSS px tall (viewport 635 → 366), the field
stayed visible, and the viewport recovered after `blur()`.

## Measured

Intel Core Ultra 7 258V, on battery with turbo off, host load 4 to 13 from other work. The guest draws in software, so it is slow
by phone standards; the inspector is fast because it does not draw.

| What | Time | Runs |
|---|---|---|
| attach to a page | 0.1 s | 3 |
| `js` `1 + 1` | 17 to 76 ms | 5 |
| navigate to a loaded site (plain HTTP) | 2.6 s | 1 |
| first HTTPS load of example.com (cold) | 57 to 87 s | 2 |
| `screenshot` | 0.22 s | many |
| a tap until the screen shows the result | 14 to 60 s | many |
| keyboard open after the tap / closed after `blur()` | 7 to 28 s / 8 to 10 s | 2 / 2 |
| boot to SpringBoard | 9 to 14 min | earlier sessions |
| restore a snapshot / `vm_save` (2.9 GB) | 28 s / 70 to 98 s | earlier sessions |

## Known problems

| Symptom | Cause and what to do |
|---|---|
| Taps do nothing | In some boots `backboardd` does not accept the virtual touchscreen (a start-up race in the guest). `vm_status` shows `touchscreen: not recognized`. `vm_stop` and `vm_start` again. Seen once in about ten boots. |
| Pages fail with "network connection was lost" right after boot | The guest network needs a few minutes to warm up (the first DNS lookup took 26 s). Wait and retry. |
| `inspector_pages` is empty | Safari is not running, or shows its native start page (no web page exists). `safari_ready` or `open_url` opens one. |
| "the page did not answer the setup" | Another frontend is attached to the page. A page takes one at a time; the next call ends stale connections. |
| `Ticket spinlock timeout` panic | The host starved a vCPU thread. `vm_start` already raises the kernel's timeout (`slto_us`). Close other heavy work. |
| Everything is black | The display turned itself off: `wake`. |

More, with the reasons: [docs/findings.md](docs/findings.md).

## How it works, in short

Safari does not offer its inspector to the outside. Its processes send inspector messages to a system service,
`webinspectord`. This project replaces that service with a small program in the guest (`guest/wi_relay.c`, about 470 lines of C,
built without the iOS SDK) which posts the notification apps wait for, accepts their connections, and passes their messages over
TCP (a QEMU port forward) to this server, which speaks WebKit's inspector protocol. Details and the wire formats:
[docs/architecture.md](docs/architecture.md). Development, tests and fixtures: [docs/development.md](docs/development.md).

## Licence

MIT, see [LICENSE](LICENSE). This repository contains no file of Apple's iOS and none of the HackiOS kernel or userland. The
parent's kernel tree is under the Apple Public Source License 2.0, its userland tree under MIT. This is not an official part of
HackiOS and not affiliated with Apple. The property list format of Safari's messages was worked out from captured traffic.
