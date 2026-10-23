#!/usr/bin/env bash
# Build the HackiOS image that vm_start boots: the Web Inspector relay, the network job list (rc.jobs.mcp) and the on-demand jobs.
# Needs the iOS files (ios165/fetch.sh) and the FHS shell that launch.sh builds. usage: ./build-image.sh
set -eu
HERE=$(dirname "$(readlink -f "$0")")
ROOT=${IOS_SAFARI_MCP_ROOT:-$(dirname "$HERE")}
H=${HACKIOS_DIR:-$ROOT/../hackios}
STATE=${IOS_SAFARI_MCP_STATE:-$HOME/.local/state/ios-safari-mcp}
[ -e "$STATE/fhs/bin/hfhs" ] || nix-build -I nixpkgs=flake:nixpkgs "$HERE/fhs.nix" -o "$STATE/fhs" >&2
# a packaged install carries the relay it was built with (nix build .#wi-relay); a checkout builds it here
[ -n "${IOS_SAFARI_MCP_PREBUILT_RELAY:-}" ] && [ -x "$ROOT/guest/wi_relay" ] || "$HERE/build-guest.sh"
"$HERE/apply-to-tree.py" "$H"
cd "$H"
REST=1 RC_JOBS="$HERE/rc.jobs.mcp" "$STATE/fhs/bin/hfhs" -c 'common/build.sh qemu-virt'
