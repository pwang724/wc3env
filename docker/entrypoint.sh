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
    init)
        # Initialize the Wine prefix, for an image built with INIT_WINE=0 (docker/modal_run.py).
        bash /opt/worker/install.sh
        if [ -d /opt/wheels/agent ]; then bash /opt/worker/install.sh agent; fi
        ;;
    bench|profile|rollout)
        # tools/bench.py, through bench_prefixes.py (--prefixes N: a wineserver per group of games);
        # tools/profile_game.py; or vector_rollout.py, VectorSession from native Linux Python as a
        # trainer runs it. linux-cpu.json adds the container's own CPU.
        python3 /opt/worker/license.py
        session="$(mktemp -d "/sessions/$mode-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX")"
        export WC3_OUTPUT_DIR
        WC3_OUTPUT_DIR="$(winepath -w "$session")"
        echo "Session: $session"
        case "$mode" in
            bench) set -- python3 /opt/worker/bench_prefixes.py --output-dir "$session" "$@" ;;
            profile) set -- wine "$python" 'Z:\opt\worker\profile_game.py' "$@" ;;
            rollout) set -- env PYTHONPATH=/opt/host python3 /opt/worker/vector_rollout.py "$@" ;;
        esac
        python3 /opt/worker/linux_cpu.py --output "$session/linux-cpu.json" -- "$@"
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
        echo 'Usage: init | smoke | bench|profile|rollout [arguments] | run <wc3agent arguments> | python [arguments]' >&2
        exit 2
        ;;
esac
