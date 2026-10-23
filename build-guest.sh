#!/usr/bin/env bash
# Build guest/wi_bridge and guest/wi_relay for the iOS 16.5 arm64 guest without the iOS SDK (nixpkgs clang + ld64.lld).
# Usage: ./build-guest.sh [NAME]   (default: both; the output is guest/NAME)
set -eu
HERE=$(dirname "$(readlink -f "$0")")
for NAME in ${1:-wi_bridge wi_relay}; do
OUT=$HERE/guest/$NAME
nix shell nixpkgs#llvmPackages.clang-unwrapped nixpkgs#lld --command sh -c "
	set -eu
	clang --target=arm64-apple-ios16.5 -nostdlibinc -fblocks -O1 -Wall -c '$HERE/guest/$NAME.c' -o '$OUT.o'
	ld64.lld -arch arm64 -platform_version ios 16.5 16.5 -o '$OUT' '$OUT.o' '$HERE/guest/libSystem.tbd' -e _main
"
rm -f "$OUT.o"
# entitlements travel in the code signature; this guest honours them (AMFI accepts any signature)
nix shell nixpkgs#ldid-procursus --command ldid -S"$HERE/guest/wi_bridge.entitlements" "$OUT"
file "$OUT"
done
