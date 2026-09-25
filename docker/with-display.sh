#!/usr/bin/env bash
set -euo pipefail
child_pid=''
Xvfb "$DISPLAY" -screen 0 1024x768x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
display_pid=$!
cleanup() {
    if [ -n "$child_pid" ]; then kill "$child_pid" 2>/dev/null || true; fi
    wineserver -k || true
    kill "$display_pid" 2>/dev/null || true
    wait "$display_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
for attempt in {1..60}; do
    if xdpyinfo >/dev/null 2>&1; then break; fi
    sleep 0.2
done
xdpyinfo >/dev/null
"$@" &
child_pid=$!
wait "$child_pid"
