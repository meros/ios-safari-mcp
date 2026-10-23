#!/usr/bin/env bash
# Build the HackiOS image that vm_start boots: the Web Inspector relay, the network job list (rc.jobs.mcp) and the on-demand jobs.
# Needs the iOS files (ios165/fetch.sh) and the FHS shell that launch.sh builds. usage: ./build-image.sh
set -eu
HERE=$(dirname "$(readlink -f "$0")")
H=${HACKIOS_DIR:-$HOME/git/personal/hackios}
STATE=${HACKIOS_MCP_STATE:-$HOME/.local/state/hackios-mcp}
[ -e "$STATE/fhs/bin/hfhs" ] || nix-build -I nixpkgs=flake:nixpkgs "$HERE/fhs.nix" -o "$STATE/fhs" >&2
"$HERE/build-guest.sh" wi_relay
"$HERE/apply-to-tree.py" "$H"
cd "$H"
REST=1 RC_JOBS="$HERE/rc.jobs.mcp" "$STATE/fhs/bin/hfhs" -c 'common/build.sh qemu-virt'
