# Architecture

## Overview

```
 MCP client (an agent)
        │  JSON-RPC 2.0, one message per line, stdio
        ▼
 ios_safari_mcp (host, Python, standard library only)
   ├─ QEMU monitor + QMP ───────────► QEMU aarch64 (TCG, no KVM)  ── virtio ──►  HackiOS guest (iOS 16.5)
   │    screendump, touch events, snapshots                                          │
   │                                                                                 │  XPC, mach service
   └─ TCP 127.0.0.1:27753 + 10·n  ─── QEMU hostfwd ───►  wi_relay (guest, port 27753) ◄─ com.apple.webinspector
                                                                                     ▲
                                                           MobileSafari, WebContent processes, JavaScript contexts
```

| Module (`src/ios_safari_mcp/`) | Job |
|---|---|
| `mcp.py`, `registry.py`, `results.py` | the MCP protocol; the tool registry (`@tool`); result shapes and `ToolError` |
| `tools/` | the 41 tools, grouped: `vm`, `screen`, `input`, `guest`, `inspector`, `keyboard` |
| `vm.py`, `qemu.py`, `screen.py` | VM lifecycle and snapshots; monitor, QMP and the touch helper; screenshots and settling |
| `inspector/client.py` | the connection to the relay: frames, page listing, sessions, the Target wrapping |
| `inspector/bplist15.py` | the property list format of Safari's messages (reader and writer) |
| `inspector/page.py` | the JavaScript the tools run in the page |
| `inspector/manager.py` | the shared connection, stale-connection cleanup, navigation |
| `calibration.py`, `keyboard.py` | page pixels to screen pixels; the on-screen key positions |
| `doctor.py`, `cli.py` | `ios-safari-mcp doctor`, `build-image`, `serve`, `version` |
| `guest/wi_relay.c` | the program in the guest |
| `scripts/` | launching QEMU, building the image and the relay, patching the parent's build |

## Why the relay exists

Safari does not offer its Web Inspector to anything but a connected Mac. Its processes (MobileSafari, every WebContent process,
JavaScript contexts) send inspector messages over XPC to a system service, `webinspectord`, which a host reaches through
`lockdownd` over USB. In this guest that path is closed: `lockdownd` treats every connection that does not come from the kernel
over loopback as Wi-Fi, and a Wi-Fi connection cannot pair (see [findings.md](findings.md)). So the relay takes the place of
`webinspectord`: a launchd job (`guest/com.redwood.wirelay.plist`) claims the mach service name `com.apple.webinspector`
(the parent's build leaves `webinspectord`'s own job out), and passes everything over TCP.

## The handshake with Safari's processes

1. The relay holds the mach service and posts the Darwin notifications `com.apple.webinspectord.available` and
   `com.apple.webinspectord.enabled`.
2. A process that starts later posts `com.apple.webinspectord.availability_check`; the relay answers by posting the two again.
   Without this answer no process connects (this was the missing piece for a long time).
3. The process connects to the service and sends `syn`, then `check`, then `WIRListingMessage` with its pages. A process
   that has no web view (Safari on its native start page) connects late or not at all.

## Wire formats

**XPC, between a process and the relay**: a dictionary `{"msgData": {"<UUID>": <data>}}`. The UUID is a constant of
CoreFoundation's XPC encoding (the same in every process); the relay answers with the UUID it saw. The data is a property list
in the `bplist15` format (below) holding `{messageName, userInfo}`.

**`bplist15`**: the magic, an 8 byte integer (bit 63 set; the low bits count the bytes up to the end of the root object), a 4
byte integer 0, then the root object. Objects: `d<n>` dictionary of n pairs (n keys, then n values), `7<n>` string (`7f`: length
follows), `4<n>` data, `08`/`09` false/true, `1<k>` an integer of 2^k bytes, little endian, whose top bit is a flag that is
always set. Worked out from captured traffic; `tests/fixtures/handshake.json` holds two real messages, and the encoder
reproduces them byte for byte. Arrays are read but not written, because none appeared in the traffic.

**TCP, between the relay and the host**: frames of `u32 length | u32 peer | data` (big endian, `length` counts peer and data).
`peer` is the number the relay gave the process (from 1). Host to guest, peer 0 means every process. A frame with no data says
that a process is gone. Peer `0xFFFFFFFF` is the control channel: the host sends a command as text (`open-url URL`), the relay
answers with a frame from the same peer (`ok ...` or `error ...`). The relay serves one host at a time; a new connection
replaces the old one. On connect it replays what each process listed last, so a late host sees the pages at once.

## The inspector protocol

Messages to a process are `{messageName, userInfo}` with WebKit's own constants:

| Message | userInfo |
|---|---|
| `WIRApplicationGetListingMessage` | `WIRConnectionIdentifierKey` |
| `WIRSocketSetupMessage` | `WIRConnectionIdentifierKey`, `WIRPageIdentifierKey`, `WIRSenderKey`, `WIRAutomaticallyPause` |
| `WIRSocketDataMessage` | the same three keys and `WIRSocketDataKey` (the protocol JSON as data) |
| `WIRConnectionDiedMessage` | `WIRConnectionIdentifierKey` |

and from a process: `WIRListingMessage` (`WIRListingKey`: page id → type, title, url) and `WIRRawDataMessage` (`WIRRawDataKey`,
`WIRDestinationKey` = the sender, `WIRConnectionIdentifierKey`). A page takes **one frontend at a time**, so every connection ends
with `WIRConnectionDiedMessage`, and the manager ends connections that an earlier run left behind (`wi-connections.json`).

Recent WebKit multiplexes through the **Target domain**: after the setup a `Target.targetCreated` event names the page target,
commands go out wrapped in `Target.sendMessageToTarget`, and answers and events come back in
`Target.dispatchMessageFromTarget`. A navigation to another process creates a provisional target, commits it
(`Target.didCommitProvisionalTarget`) and destroys the old one, and a new target starts with no domain enabled; the session
enables Console, Network and Page on every new target so that events are not lost.

## Page pixels to screen pixels

A tap takes screen pixels; the page knows CSS pixels. The offset (status bar, Safari's bars, the keyboard) and the scale (3 here)
change as the bars collapse and the keyboard opens, so `calibration.py` measures them: it draws a 14 px magenta square at a known
place of the layout viewport (`position: fixed`), finds it in a screenshot, and derives the origin and the scale. The result is
cached against the layout (viewport size, offset, zoom) and measured again only when that changes.

## The keyboard

On iOS the keyboard does not resize the layout viewport. `window.innerHeight` stays; `visualViewport.height` shrinks by the
keyboard's height and `visualViewport.offsetTop` may scroll. `position: fixed` elements stay glued to the layout viewport and can
end up behind the keyboard. `keyboard_state` computes all of this from `visualViewport` (`js` in `inspector/page.py`) and lists
interactive elements, popups and fixed bars that reach under it. Showing the keyboard needs a real touch, so `keyboard_show` goes
through `calibration`. Closing by `blur()` is what pages do; the return key and a tap outside are the alternatives.

## Building

- **The relay** is plain C with the few prototypes it needs declared in the source (no iOS SDK). `clang --target=arm64-apple-ios16.5`
  compiles it; `ld64.lld` links it against `guest/libSystem.tbd` and `guest/CoreFoundation.tbd`, hand-written stubs that list
  symbol names only (the guest's dyld resolves them against the real libraries); `ldid` signs it with
  `guest/wi_relay.entitlements`. The guest accepts any signature but honours the entitlements in it. `nix build .#wi-relay` does
  this hermetically.
- **The image** is the parent's, built by `scripts/build-image.sh` inside an FHS shell (the parent's scripts expect `/bin/bash`).
  `scripts/apply-to-tree.py` makes three small changes to the parent's tree, idempotently: it copies the relay and its job into
  `userland/`, adds two copy lines to `userland/build.sh`, and adds `webinspectord` to the deny list in `userland/mkjobs.py`.
- **Launching** (`scripts/launch.sh`) builds the FHS shell once, gets QEMU from nixpkgs, and starts `common/run.sh`
  with a wrapper (`scripts/bin/qemu-system-aarch64`) that adds the port forward, and, for a guest that can be saved, a read-only ESP
  and no sound device (QEMU cannot migrate a read-write vvfat disk or a virtio-sound device).

## Trust

The relay listens on 0.0.0.0 inside the guest's own NAT (QEMU user networking), which the outside reaches only through the
forward bound to 127.0.0.1 on the host. Its one command, `open-url`, accepts `http:`, `https:` and `about:` addresses. The guest is a
throw-away VM; do not put anything in it that you would not hand to the page you are testing.
