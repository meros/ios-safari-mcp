#!/usr/bin/env bash
# Boot HackiOS in QEMU (blocks until the session ends). Started by server.py's vm_start.
set -eu
HERE=$(dirname "$(readlink -f "$0")")
ROOT=${IOS_SAFARI_MCP_ROOT:-$(dirname "$HERE")}
H=${HACKIOS_DIR:-$ROOT/../hackios}
STATE=${IOS_SAFARI_MCP_STATE:-$HOME/.local/state/ios-safari-mcp}
# the nixpkgs of the flake's lock when packaged (IOS_SAFARI_MCP_NIXPKGS), else the one in the user's registry
NP=${IOS_SAFARI_MCP_NIXPKGS:-nixpkgs}
NIX_PATH_ARG=nixpkgs=${IOS_SAFARI_MCP_NIXPKGS:-flake:nixpkgs}
SECS=${RUN_SECONDS:-1200}
mkdir -p "$STATE"
[ -e "$STATE/fhs/bin/hfhs" ] || nix-build -I "$NIX_PATH_ARG" "$HERE/fhs.nix" -o "$STATE/fhs" >&2
Q=$(nix build "$NP#qemu" --no-link --print-out-paths | head -1)
# qemu/run.sh copies the vars file with cp and keeps the read-only mode of the Nix store file. The parent keeps one vars.fd
# (and one esp/) per tree, so two VMs of one tree cannot run at the same time: starting the second rewrites the first one's firmware variables.
cp -f "$Q/share/qemu/edk2-arm-vars.fd" "$H/qemu/vars.fd"
chmod u+w "$H/qemu/vars.fd"
export QEMU_NET=1 QEMU_EFI="$Q/share/qemu/edk2-aarch64-code.fd" QEMU_VARS="$Q/share/qemu/edk2-arm-vars.fd"
# the wrapper in bin/ is found first on PATH inside the FHS shell; HACKIOS_MIGRATABLE and
# HACKIOS_SNAPSHOT (a file written by vm_save) are set by server.py
export REAL_QEMU="$Q/bin/qemu-system-aarch64"
# QEMU's user network forwards a host port to the Web Inspector relay in the guest (guest/wi_relay.c, port 27753)
export HACKIOS_HOSTFWD="${HACKIOS_HOSTFWD-tcp:127.0.0.1:${WI_PORT:-$((27753 + 10 * ${QEMU_INSTANCE:-0}))}-:27753}"
[ -n "${HACKIOS_SNAPSHOT:-}" ] && export HACKIOS_INCOMING="exec:gzip -dc '$HACKIOS_SNAPSHOT'"
exec "$STATE/fhs/bin/hfhs" -c "PATH='$HERE/bin':\$PATH; cd '$H' && common/run.sh qemu-virt $SECS"
