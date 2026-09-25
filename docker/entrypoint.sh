#!/usr/bin/env bash
set -euo pipefail
export WC3_GAME_DIR='C:\wc3'
export WC3_USER_DIR='C:\scratch\Warcraft III'
export AGENT_SESSIONS_DIR='Z:\sessions'
export WC3_MAP="${WC3_MAP:-Maps/FrozenThrone/(2)EchoIsles.w3x}"
python='C:\Python311\python.exe'
mode="${1:-smoke}"
if [ "$#" -gt 0 ]; then shift; fi
python3 /opt/worker/platform_probe.py
case "$mode" in
    smoke)
        python3 /opt/worker/license.py
        session="$(mktemp -d "/sessions/env-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX")"
        mkdir "$session/env"
        cp /tmp/platform-result.json "$session/env/platform-result.json"
        export WC3_OUTPUT_DIR
        WC3_OUTPUT_DIR="$(winepath -w "$session/env")"
        echo "Session: $session"
        timeout 180s wine "$python" 'Z:\opt\worker\smoke.py' \
            --output "$(winepath -w "$session/env/result.json")" "$@"
        ;;
    run|agent)
        python3 /opt/worker/license.py
        wine "$python" -m wc3agent "$@"
        ;;
    python)
        python3 /opt/worker/license.py
        wine "$python" "$@"
        ;;
    *)
        echo 'Usage: smoke | run <wc3agent arguments> | python [arguments]' >&2
        exit 2
        ;;
esac
