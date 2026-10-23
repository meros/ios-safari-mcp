#!/usr/bin/env bash
# Boot helper for the Web Inspector experiments: wait for SpringBoard, open Safari from the dock, dismiss the first-run
# screen if it shows, load a page.   usage: tests/start_safari.sh [URL]
set -u
cd "$(dirname "$(readlink -f "$0")")/.."
call() { timeout 900 python3 -I tests/call.py "$@" 2>&1 | tail -n 1 | cut -c1-200; }
call wait_for '{"phase":"springboard_owns_screen","timeout":1400}'
call wake '{}'
call tap '{"x":0.384,"y":0.929,"settle":true,"timeout":200}'
call tap '{"x":0.5,"y":0.481,"settle":true,"timeout":120}'
call open_url "{\"url\":\"${1:-example.com}\",\"settle\":true,\"timeout\":300}"
