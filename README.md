# hackios-safari-mcp

An MCP server that drives HackiOS (iOS 16.5 on the XNU kernel, in QEMU) with taps, swipes, typing and screenshots.
It has no dependencies beyond Python 3 and Nix. It wraps what the `hackios-xnu-kernel` tree already has:
the QEMU monitor (`screendump`), QMP input events (`qemu/touch.py`) and the console log.

## Register it

```json
{ "mcpServers": { "hackios": { "command": "/home/meros/git/personal/hackios-safari-mcp/server.py" } } }
```

Set `HACKIOS_DIR` (default `~/git/personal/hackios`) if the tree is elsewhere. `launch.sh` needs the iOS files
(`ios165/fetch.sh`) and an image built with this repository's job list:

```sh
RC_JOBS=$PWD/rc.jobs.mcp common/build.sh qemu-virt     # in the hackios tree, inside the FHS shell (fhs.nix)
```

## Tools

| Tool | Does |
|---|---|
| `vm_start` | boot in the background; `snapshot=NAME` restores a saved state instead |
| `vm_save`, `vm_snapshots` | save the whole machine state (about 2.9 GB) and list the saved states |
| `vm_status`, `wait_for` | phase: kernel, launchd, backboardd_ready, springboard_owns_screen, running (restored); panic detection |
| `screenshot` | PNG of the 1125x2436 screen, scaled down by `scale` (default 2) |
| `tap`, `tap_px`, `hold`, `swipe`, `key`, `scroll`, `back`, `home_screen`, `wake` | input; positions are fractions of the screen, `tap_px` takes pixels of a screenshot |
| `type_text`, `open_url` | type on the on-screen keyboard; open an address in Safari |
| `settle` | wait for the screen to change and then to stop changing |
| `guest_process` | is a process running in the guest (by name), with pid and thread count |
| `log_tail`, `wait`, `vm_stop` | guest console lines by regex, a pause, stop |

Every input tool takes `settle: true`: it then returns only after the screen has changed and stopped changing
(`timeout`, `stable_for`, `wait_change` as in `settle`). The guest answers an input 15 to 60 seconds later because it draws
in software, so a screenshot right after a tap shows the old screen. The status bar is ignored in the comparison
(its clock ticks every minute).

## Measured (Intel Core Ultra 7 258V, on battery with turbo off, host load 4 to 48 from other work)

| What | Time |
|---|---|
| boot to SpringBoard | 8.9 minutes on the charger with turbo on and low load; 10.7 to 14.4 minutes on battery |
| `vm_save` | 70 s, 2,879 MB |
| `vm_start(snapshot=...)` to a running guest | 28 s |
| `screenshot` | 0.22 s (was 2.1 s with fixed sleeps) |
| tap on the screen to the result | 14 to 60 s |

A restored guest answers input and its clock continues. The saved state includes the loaded page.

## Session length and snapshots

The guest gets `loader_return_after=SECONDS` and ends after that much guest time since boot (default 8 hours here). A saved
state keeps that clock: a snapshot saved at 1,796 s of a 2,400 s session had 600 s left after every restore. Boot with the
long default before you save.

## Web Inspector: what is known

- `webinspectord` is not running with Safari open (`guest_process webinspectord` says no), although its job is defined
  once the `mkjobs.py` bug is fixed. Safari does not start it by itself.
- The Web Inspector switch (Settings, Safari, Advanced) cannot be used: tapping Safari in Settings aborts the Settings app
  (`Preferences[235] aborts`, SIGABRT). The switch is added in code, not in `Developer.plist`.
- The dyld cache has the keys `RemoteInspectorEnabled`, `RemoteInspectionEnabled`, `AllowRemoteInspection`,
  `DeveloperExtrasEnabled` and the notifications `com.apple.webinspectord.available` and `.automatic_inspection_enabled`.
  Which preference domain holds them is not known; the cache stores strings in a pool, so no neighbouring string helps.
- `lockdownd` can be reached from the host (23 October, 5 boots): `seed/build-sh-lockdown-dir.txt` lists the steps. With
  the job loaded, QEMU forwarding host 62078 to the guest, and `com.apple.mobile.wireless_lockdown-EnableWifiConnections`
  (and `EnableWifiPairing`, `EnableWifiDebugging`) set in `/var/root/Library/Lockdown/data_ark.plist`, a plain lockdown
  client gets `QueryType` (`com.apple.mobile.lockdown`), `ProductVersion` 16.5 and `DeviceName` iPhone.
  Without the data_ark flags `lockdownd` closes every connection at accept: `socket_accept_callback: Killing the WiFi
  connection immediately` (os_log, shown with `OSLOG=com.apple.mobile.lockdown`). The preference domain file
  `com.apple.mobile.wireless_lockdown.plist` has no effect.
- Pairing does not work over that path. `lockdownd` calls a connection USB only when its source is 127.0.0.1 and the peer
  has pid 0 (`TCP_PEER_PID`, a kernel-owned socket); every other source is "WiFi". A WiFi connection that has no session
  may read only a short list of keys, and `DevicePublicKey` is not on it (`GetProhibited`), so `pymobiledevice3` stops
  before `Pair`. This is from reading the arm64 code, plus the log: one run each of `pymobiledevice3` 11.43.1 and a
  raw client. Not tried: a pairing record put in the image, and a kernel-side relay that connects to 127.0.0.1:62078
  from a kernel socket (pid 0).
- `lockdownd` starts `com.apple.webinspector` as an XPC service (table `__TEXT,__services`: `XPCServiceName`); the
  relay is `RWIRelay` in WebInspector.framework (dyld cache), so a guest-side bridge that talks to that mach service
  and to TCP could skip lockdown. nixpkgs clang plus `ld64.lld` link an arm64 iOS binary against a hand-written
  `libSystem.tbd` (tested with a 3-line program, not run in the guest); `ldid-procursus` would sign entitlements.
  The message keys are not known yet.

## What cannot be done (yet)

- Reading the DOM, running JavaScript, the console and network panels: this needs a connection to Safari's Web Inspector.
  `webinspectord` is not loaded in the default boot, `lockdownd` and `usb` are in `mkjobs.py`'s deny list, there is no
  `usbmuxd`, and QEMU's network has no port forward. Approach A (host side): load `lockdownd`, forward its port, put a
  usbmuxd shim in front of `ios-webkit-debug-proxy`; pairing and SSL are unknown. Approach B (guest side): a bridge
  inside Safari; it needs the iOS SDK and theos, which this machine does not have. Neither is built.
- Typing upper case, `ö`, and most symbols. Letters, digits (UNVERIFIED), space and `. - / : ( ) ; $ & @ "` are mapped.
- Reading the current URL or page text (use screenshots).
- The Safari edge-swipe back gesture does not navigate; `back` taps the toolbar arrow, which must be showing.

## Why `launch.sh` does what it does (each item cost time to find)

- The scripts use `#!/bin/bash`; NixOS has no `/bin/bash`, so they run in a small FHS environment (`fhs.nix`).
  Open PR oosexgang/hackios-xnu-kernel#7 changes the shebangs.
- `ios165/fetch.sh` needs 7-Zip 23 or newer for APFS. `p7zip` 17.x lists the image as raw partitions and
  `select-system.py` then finds 0 files (PR #8 adds a version check). `fhs.nix` provides `_7zz` as `7z`.
- `qemu/run.sh` copies the UEFI vars file with `cp`; from the Nix store the copy stays read-only and QEMU fails with
  `Could not open 'vars.fd'`. `launch.sh` makes a writable copy first.
- The prebuilt `loader.efi` sets up QEMU's ramfb display with a fw_cfg DMA race: the screen stays black while iOS runs.
  Rebuild `xnuloader/loader.efi` with the `loader.c` hunk of oosexgang/hackios-xnu-kernel#6 (clang plus lld-link
  from nixpkgs). Not automated here.
- `userland/mkjobs.py` has a variable shadowing bug: the loop `for name in ('RWA_PACKED16',)` overwrites `name`, the file
  name of the job being written, so every on-demand job lands in one file called `RWA_PACKED16` and none of the 400
  jobs exist in the image (no configd, so no DHCP, so Safari says "not connected to the Internet"). Rename the loop
  variable. Not yet reported upstream.
- The default `rc.jobs` runs diagnostics (`cpu_test 240 &` burns CPU while SpringBoard starts) and never configures the
  network. `rc.jobs.mcp` runs DHCP on QEMU's virtio `en0` instead.
- QEMU refuses to save a guest with a read-write `vvfat` disk or a `virtio-sound` device. `bin/qemu-system-aarch64`
  makes the ESP read-only and drops the sound device when `HACKIOS_MIGRATABLE=1`.
- A loaded host starves vCPU threads: the guest kernel then panics with `Ticket spinlock timeout` (4 panics in about 10
  boots here). `vm_start` passes `slto_us=60000000` to raise that timeout; none of the 4 boots after that panicked
  (two of them I stopped after 8 to 11 minutes). Four boots are too few to be sure.

## Speed

The guest is aarch64 and the host x86, so QEMU translates in software (no KVM). Speed follows the speed of one core:
on battery `auto-cpufreq` turns turbo off (`intel_pstate/no_turbo` is 1), so every core runs at 2.2 GHz. Plug in.
SpringBoard's software drawing is the larger share of the boot. Restoring a saved state avoids the boot.

## Tests

`python3 -I tests/test_server.py` needs no VM (JSON-RPC, PPM to PNG, keyboard map, a fake QEMU monitor for `hmp` and `settle`).
`tests/live2.py OUTDIR` boots a VM and takes about 15 minutes; `tests/call.py TOOL '{json}' [OUT.png]` calls one tool.
