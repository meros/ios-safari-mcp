#!/usr/bin/env bash
# Build guest/wi_relay, the Web Inspector relay that runs inside the iOS 16.5 arm64 guest, without the iOS SDK:
# nixpkgs clang compiles it against the prototypes in the source, and ld64.lld links it against guest/libSystem.tbd
# (a stub that lists the names of the libSystem symbols it uses) and guest/CoreFoundation.tbd. ldid then signs it with
# guest/wi_relay.entitlements: the guest accepts any signature but only honours the entitlements it carries.
# Usage: ./build-guest.sh [OUTPUT]     (default guest/wi_relay)
set -eu
HERE=$(dirname "$(readlink -f "$0")")
ROOT=$(dirname "$HERE")
OUT=${1:-$ROOT/guest/wi_relay}
nix shell nixpkgs#llvmPackages.clang-unwrapped nixpkgs#lld nixpkgs#ldid-procursus --command sh -c "
	set -eu
	clang --target=arm64-apple-ios16.5 -nostdlibinc -fblocks -O1 -Wall -Werror -c '$ROOT/guest/wi_relay.c' -o '$OUT.o'
	ld64.lld -arch arm64 -platform_version ios 16.5 16.5 -o '$OUT' '$OUT.o' '$ROOT/guest/libSystem.tbd' '$ROOT/guest/CoreFoundation.tbd' -e _main
	ldid -S'$ROOT/guest/wi_relay.entitlements' '$OUT'
"
rm -f "$OUT.o"
file "$OUT"
