# Findings

What was learned while building this, including the routes that do not work. Dates are 22 and 23 October 2026; the
guest is iOS 16.5 (20F66) on `xnu-8796`, QEMU 11.1 `virt` machine, TCG.

## Prerequisites in the parent that are not merged yet

Each of these cost time. The first four are pull requests on the parent's repositories (open when this was written), so check
their state before assuming a fix is missing:

- **A black screen while iOS runs.** The prebuilt `xnuloader/loader.efi` configures QEMU's `ramfb` display with a fw_cfg DMA race:
  the guest boots and draws, and the screen stays black. Rebuild the loader with the `loader.c` hunk of
  `hackios-xnu-kernel` PR #6 (clang plus `lld-link` from nixpkgs).
- **No network, no on-demand jobs.** `userland/mkjobs.py` reuses the loop variable `name` for an environment variable name, so every
  on-demand job is written to one file called `RWA_PACKED16` and none of the ~400 jobs exists in the image: no `configd`, no DHCP, and
  Safari says "not connected to the Internet". `hackios-xnu-userland` PR #7 renames the variable. (`apply-to-tree.py` does not fix this.)
- **`#!/bin/bash`.** NixOS has no `/bin/bash`; the scripts run in a small FHS shell (`scripts/fhs.nix`). PR #7 of the kernel repository
  changes the shebangs.
- **7-Zip.** `ios165/fetch.sh` needs 7-Zip 23 or newer to read the APFS image; `p7zip` 17 lists raw partitions and the next step finds
  0 files. The FHS shell provides `7zz` as `7z`. PR #8 of the kernel repository adds a version check.
- `qemu/run.sh` copies the UEFI variables with `cp`, which keeps the read-only mode of the Nix store file; `launch.sh` makes a writable
  copy first. The default `rc.jobs` runs diagnostics (`cpu_test 240 &` burns CPU while SpringBoard starts) and never configures the
  network; `scripts/rc.jobs.mcp` runs DHCP on QEMU's virtio `en0` instead.

## Boot, host and snapshots

- **Speed.** The guest is aarch64 and the host x86, so QEMU translates in software and the speed follows one core. On battery
  `auto-cpufreq` turns turbo off (`intel_pstate/no_turbo` is 1) and every core runs at 2.2 GHz: the boot takes about 40 % longer.
  SpringBoard's software drawing is most of the boot. A restored snapshot avoids it.
- **Spinlock panics.** A loaded host starves vCPU threads and the guest panics with `Ticket spinlock timeout` (4 in about 10 boots).
  `vm_start` passes `slto_us=60000000`; none of the 4 boots after that panicked. Four boots are too few to be sure.
- **Session length.** The guest gets `loader_return_after=SECONDS` and ends after that much guest time since boot. A saved state
  keeps the clock, so a snapshot saved at 1,796 s of a 2,400 s session had 600 s left after every restore. The default is 8 hours.
- **Snapshots.** QEMU refuses to save a guest with a read-write `vvfat` disk or a `virtio-sound` device; the launch wrapper makes the
  ESP read-only and drops the sound device when the VM is started saveable. Saving takes 70 to 112 s and 2.9 GB; restoring 18 to 28 s.
  A snapshot is tied to the image it was saved from: rebuild the image and save a new one. **A web page that was open when a
  snapshot was restored does not answer the inspector's setup any more** (its listing is replayed, the setup message gets no answer);
  a page opened after the restore does. The tools open a fresh page through SpringBoard.
- **The display turns itself off** after boot. `wake` is a tap and then the home key.
- **A boot where touches are ignored.** In one boot in about ten, `backboardd` logs `still waiting for a main screen digitizer` and
  then `ignoring digitizer event for display <main> from unsupported service`: the virtual touchscreen came up classified as an
  external accessory with no display, and every tap is dropped. It is a start-up race in the guest. `vm_status` reports it
  (`touchscreen`), `safari_ready` refuses to tap, and the fix is a new boot.
- **The network warms up.** The first DNS lookup after boot took 26 s, and pages failed with "network connection was lost" for a few
  minutes although the guest had an address and the host network was fine. They loaded afterwards. Cause not found.

## Safari

- **No page, no inspector.** On its native start page ("Favorites") Safari has no web view, so it does not connect to the inspector
  service and nothing is listed. A page must exist: tap a favourite, or ask SpringBoard to open a URL (`open_url` with no page does,
  through the relay's `open-url` command, which mirrors the parent's `battery_test open URL`; not yet verified live).
- **First-run screen.** Typing in the address bar the first time shows "Safari search now shows personalized suggestions" with a
  Continue button. Navigating through the inspector avoids it.
- **Settings > Safari aborts** the Settings app (`Preferences` SIGABRT), so the Web Inspector switch cannot be used. The relay needs
  no setting: the processes connect when the relay announces itself.
- **Back.** The edge-swipe gesture did not navigate back; `back` taps the toolbar arrow, which must be showing.
- **Touch to result takes 14 to 60 s** (software drawing), so a screenshot right after a tap shows the old screen. `settle` waits for
  the screen to change and then to stop changing, ignoring the status bar clock.

## The route that did not work: lockdownd

The standard route is `lockdownd` on TCP 62078 (what `usbmuxd` and `ios-webkit-debug-proxy` use). It can be reached, and it cannot
pair:

1. `mkjobs.py` denies the `lockdown` job; allow it by naming `com.apple.mobile.lockdown` in the job list. launchd then spawns it on
   the first connection to port 62078.
2. QEMU's user network has no port forward by default; `hostfwd=tcp:127.0.0.1:62078-:62078` adds one.
3. `lockdownd` classifies each connection (`socket_accept_callback`, arm64 disassembly): a loopback peer whose `TCP_PEER_PID` is 0
   (a kernel-owned socket) is USB, every other source is Wi-Fi. A Wi-Fi connection is killed at accept
   (`Killing the WiFi connection immediately`) unless one of three flags is set in `lockdownd`'s own settings store
   (`/var/root/Library/Lockdown/data_ark.plist`, keys `com.apple.mobile.wireless_lockdown-EnableWifiConnections`, `...EnableWifiPairing`,
   `...EnableWifiDebugging`). With them set, a plain client gets `QueryType`, `ProductVersion` 16.5 and `DeviceName`.
4. A Wi-Fi connection without a session may read only a short list of keys. `DevicePublicKey` is not on it (`GetProhibited`), so
   `pymobiledevice3` stops before `Pair`. Not tried: a pairing record put into the image, and a relay in the guest kernel that
   connects to 127.0.0.1:62078 from a kernel socket (pid 0).
5. The os_log output of a launchd job in this image is enabled by naming the job in `OSLOG` for `mkjobs.py`; it showed every line above.

## What the replacement relay learned

- `webinspectord` is a thin shell around `RWIRelay` in `WebInspector.framework` (in the dyld cache); it ignored a client that sent
  `lockdownd`'s check-in message (keys `_LDCHECKININFO`, `_LDTIMESTAMP`, `_LDCHECKINDICT`, `_LDSERVICESOCK`), so the relay replaces it
  instead. The dyld cache needs its `.symbols` file for `ipsw`, and that file is APFS-compressed with LZBITMAP, which 7-Zip cannot
  unpack: the code of the real relay was not read.
- The apps' messages are `bplist15` (not `bplist00`) in a `{"msgData": {"<UUID>": data}}` dictionary. See [architecture.md](architecture.md).
- The relay that worked in the live runs was signed with four guessed Web Inspector entitlements (they made no difference to `webinspectord`, which
  ignored it either way). The shipped relay is signed only with `com.apple.springboard.opensensitiveurl`, for `open-url`. That
  combination has not been run: if Safari's processes do not connect, sign with the earlier set first
  (`com.apple.private.webinspector.allow-remote-inspection`, `.proxy-application`, `.webinspectord`, and `com.apple.security.get-task-allow`).
- Closing a socket in one thread while another waits in `recv()` does not end a TCP connection on Linux; the client calls `shutdown()` first.
  (An offline test caught this; the first relay served one client at a time and would have blocked the next host for good.)

## Parent repositories

| | |
|---|---|
| `oosexgang/hackios-xnu-kernel` | PR #6 loader display race (a comment is posted), PR #7 shebangs, PR #8 7-Zip check |
| `oosexgang/hackios-xnu-userland` | PR #7 `mkjobs.py` variable shadowing |

Three small hooks in the parent's build (extra launchd plists, extra binaries, extra deny words) would remove the need for
`apply-to-tree.py`; not proposed yet.
