"""Run a repeatable local compatibility/scale matrix and save incremental JSON evidence.

    python tools/check_compatibility.py --soak-resets 120 --output runs/compatibility.json

No game assets are copied. GPU/driver and DPI coverage is the current machine only.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def dimensions(value):
    try:
        width, height = map(int, value.lower().split("x"))
        if not 160 <= width <= 7680 or not 120 <= height <= 4320:
            raise ValueError
        return width, height
    except ValueError as exc:
        raise argparse.ArgumentTypeError("resolution must be WIDTHxHEIGHT within 160x120..7680x4320") from exc


def machine_info():
    from wc3env.game import hook_dll
    from wc3env.settings import settings

    data = {
        "os": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "exe_sha256": hashlib.sha256(settings().game_exe.read_bytes()).hexdigest(),
        "dll_sha256": hashlib.sha256(hook_dll().read_bytes()).hexdigest(),
    }
    for key, command in (("revision", ["git", "rev-parse", "HEAD"]), ("dirty_paths", ["git", "status", "--short"])):
        data[key] = subprocess.check_output(command, cwd=ROOT, text=True).strip()
    try:
        command = (
            "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,"
            "CurrentHorizontalResolution,CurrentVerticalResolution | ConvertTo-Json -Compress"
        )
        data["gpu"] = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", command], text=True))
        user = ctypes.WinDLL("user32")
        data["system_dpi"] = user.GetDpiForSystem()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        data["hardware_query_error"] = str(exc)
    return data


class ProcessMemory(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
        (name, ctypes.c_size_t)
        for name in (
            "peak_working",
            "working",
            "peak_paged",
            "paged",
            "peak_nonpaged",
            "nonpaged",
            "pagefile",
            "peak_pagefile",
            "private",
        )
    ]


def private_mib(game):
    ps = ctypes.WinDLL("psapi", use_last_error=True)
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemory), ctypes.c_ulong]
    memory = ProcessMemory()
    memory.size = ctypes.sizeof(memory)
    if not ps.GetProcessMemoryInfo(game._process_handle, ctypes.byref(memory), memory.size):
        raise ctypes.WinError(ctypes.get_last_error())
    return memory.private / 2**20


def window_dimensions(game):
    from ctypes import wintypes

    from wc3env.game import user32

    windows = game.windows()
    if not windows:
        raise AssertionError("no game window")
    rect = wintypes.RECT()
    if not user32.GetWindowRect(windows[0], ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    return [rect.right - rect.left, rect.bottom - rect.top]


def run_game_case(config, *, resolution=None, army=0, rounds=12):
    from wc3env.protocol import Action
    from wc3env.session import GameSession

    started = time.perf_counter()
    with GameSession(config) as session:
        observations = session.reset()
        game = session.game
        game.rpc.debug("speed", factor=64)
        game.rpc.debug("waitfloor", ms=1)
        if resolution:
            assert game.resize(*resolution), "could not resize game"
            game.offscreen()
        counts = {p: len(o["units"]) for p, o in observations.items()}
        spawned = []
        player = config.agent_slots[0]
        home = observations[player]["units"][0]
        bounds = observations[player]["map"]["bounds"]
        columns = min(500, int((bounds["max_x"] - bounds["min_x"] - 1024) // 128))
        rows = (army + columns - 1) // columns
        assert rows * 128 <= bounds["max_y"] - bounds["min_y"] - 1024, "army grid does not fit this map"
        batch_size = max(1, 500 // columns) * columns
        for offset in range(0, army, batch_size):
            spawned.extend(
                game.rpc.debug(
                    "spawn",
                    type_id="hgry",
                    player=player,
                    x=bounds["min_x"] + 512,
                    y=bounds["min_y"] + 512 + (offset // columns) * 128,
                    n=min(batch_size, army - offset),
                    columns=columns,
                    spacing=128,
                )["unit_ids"]
            )
        assert len(spawned) == army, "spawn was truncated"
        target_id = (
            spawned[-1]
            if spawned
            else game.rpc.debug("spawn", type_id="hgry", player=player, x=home["x"], y=home["y"])["unit_ids"][0]
        )
        observations = session._observe_all()
        if army:
            assert len(observations[player]["units"]) >= counts[player] + army, "observation was truncated"
        before = next(u for u in observations[player]["units"] if u["unit_id"] == target_id)
        bounds = observations[player]["map"]["bounds"]
        target_x = min(before["x"] + 500, bounds["max_x"] - 256)
        if target_x < before["x"] + 100:
            target_x = before["x"] - 500
        actions = {p: [] for p in config.agent_slots}
        actions[player] = [Action(target_id, "move", {"x": target_x, "y": before["y"]})]
        phases = {}
        round_started = time.perf_counter()
        for round in range(rounds):
            observations, done, info = session.step(actions if round == 0 else {p: [] for p in config.agent_slots})
            assert not done, "match ended unexpectedly"
            assert info["elapsed_ms"] == config.step_ms, info
            assert not any(info["rejected"].values()), info["rejected"]
            assert len({o["game_time_seconds"] for o in observations.values()}) == 1
            for key, value in info["timings_ms"].items():
                phases[key] = phases.get(key, 0.0) + value
        round_wall = time.perf_counter() - round_started
        after = next(u for u in observations[player]["units"] if u["unit_id"] == target_id)
        assert abs(after["x"] - before["x"]) > 20, "last unit's move did not execute"
        measured = window_dimensions(game)
        return {
            "setup": session.setup,
            "window_size": measured,
            "requested_window_size": resolution,
            "resize_exact": measured == list(resolution) if resolution else True,
            "rounds": rounds,
            "game_seconds": rounds * config.step_ms / 1000,
            "round_wall_seconds": round_wall,
            "game_seconds_per_wall_second": rounds * config.step_ms / 1000 / round_wall,
            "unit_counts": {p: len(o["units"]) for p, o in observations.items()},
            "private_mib": private_mib(game),
            "phase_totals_ms": phases,
            "wall_seconds": time.perf_counter() - started,
        }


def run_soak(config, episodes):
    from wc3env.session import GameSession

    samples = []
    pids = []
    previous_pid = None
    with GameSession(config) as session:
        for episode in range(episodes):
            observations = session.reset()
            if session.game.pid != previous_pid:
                session.game.rpc.debug("speed", factor=64)
                session.game.rpc.debug("waitfloor", ms=1)
                previous_pid = session.game.pid
            _, done, info = session.step({p: [] for p in config.agent_slots})
            assert not done and info["elapsed_ms"] == config.step_ms
            assert {o["sequence"] for o in observations.values()} == {0}
            samples.append(private_mib(session.game))
            pids.append(session.game.pid)
            if (episode + 1) % 10 == 0:
                print(f"reset {episode + 1}/{episodes}: {samples[-1]:.1f} MiB", flush=True)
    growth = statistics.median(samples[-5:]) - statistics.median(samples[6:11])
    assert growth < 48, f"retained memory grew {growth:.1f} MiB: {samples}"
    return {
        "episodes": episodes,
        "private_mib": samples,
        "pids": pids,
        "processes": len(set(pids)),
        "growth_mib": growth,
        "limit_mib": 48,
    }


def run_pool(config, count, rounds):
    from wc3env.pool import StepPool

    pool = StepPool.launch(count, speed=64, config=config)
    try:
        summary = pool.run(rounds)
        assert summary.all_exact, summary.text()
        return asdict(summary)
    finally:
        pool.close()


def main(argv=None):
    from wc3env.game import resolve_map
    from wc3env.session import GameConfig, MatchSetup, PlayerConfig

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runs" / "compatibility.json")
    parser.add_argument(
        "--resolutions", type=dimensions, nargs="+", default=[(320, 240), (640, 480), (1280, 720), (1920, 1080)]
    )
    parser.add_argument("--soak-resets", type=int, default=36, help="0 skips; otherwise at least 16")
    parser.add_argument(
        "--army-sizes",
        type=int,
        nargs="+",
        default=[512, 1024],
        help="spawn counts; larger explicit stress cases can exhaust Warcraft's allocator",
    )
    parser.add_argument("--pool-sizes", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument(
        "--only", action="append", default=[], help="run only case names containing this text; repeatable"
    )
    args = parser.parse_args(argv)
    if args.soak_resets != 0 and args.soak_resets < 16:
        parser.error("--soak-resets must be 0 or at least 16")
    if any(not 1 <= n <= 8192 for n in args.army_sizes) or any(not 1 <= n <= 16 for n in args.pool_sizes):
        parser.error("army sizes must be 1..8192 and pool sizes 1..16")
    report = {
        "started_utc": datetime.now(UTC).isoformat(),
        "machine": machine_info(),
        "cases": [],
        "not_tested": [
            "Other physical GPUs/drivers",
            "Other desktop DPI settings",
            "Other Windows versions",
            "LAN/replay playback",
            "Full-game determinism",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def record(name, params, work):
        if args.only and not any(pattern in name for pattern in args.only):
            report["not_tested"].append(f"Filtered case: {name}")
            return
        print(f"checking {name}", flush=True)
        case = {"name": name, "parameters": params}
        started = time.perf_counter()
        try:
            case["result"] = work()
            case["status"] = "pass"
            if case["result"].get("resize_exact") is False:
                case["status"] = "limited"
                report["not_tested"].append(f"{name}: engine clamped window to {case['result']['window_size']}")
        except Exception as exc:
            case["status"] = "fail"
            case["error"] = f"{type(exc).__name__}: {exc}"
            case["traceback"] = traceback.format_exc()
        case["wall_seconds"] = time.perf_counter() - started
        report["cases"].append(case)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"{name}: {case['status']} ({case['wall_seconds']:.1f}s) {case.get('error', '')}", flush=True)

    def config(map="(2)EchoIsles.w3x", players=2, render=False):
        races = ("human", "orc", "undead", "night_elf")
        return GameConfig(
            map=map,
            players=tuple(PlayerConfig(p, races[p % 4]) for p in range(players)),
            setup=MatchSetup(seed=42, randomize_starts=True),
            render=render,
            sound=False,
            step_ms=250,
        )

    for map, players in (
        ("(2)EchoIsles.w3x", 2),
        ("(2)SecretValley.w3x", 2),
        ("(4)TwistedMeadows.w3x", 4),
        ("(8)TwilightRuins.w3x", 8),
    ):
        try:
            resolve_map(map)
        except (ValueError, FileNotFoundError):
            report["not_tested"].append(f"Map unavailable: {map}")
            continue
        c = config(map, players)
        record(f"map-{map}", asdict(c), lambda c=c: run_game_case(c))
    for render in (True, False):
        for resolution in args.resolutions:
            c = config(render=render)
            record(
                f"window-{resolution[0]}x{resolution[1]}-render-{render}",
                asdict(c),
                lambda c=c, resolution=resolution: run_game_case(c, resolution=resolution),
            )
    for army in args.army_sizes:
        c = config()
        record(f"army-{army}", {**asdict(c), "army": army}, lambda army=army: run_game_case(c, army=army))
    for count in args.pool_sizes:
        c = config()
        record(f"pool-{count}", {**asdict(c), "instances": count}, lambda count=count: run_pool(c, count, 40))
    if args.soak_resets:
        c = config()
        record("reset-soak", {**asdict(c), "episodes": args.soak_resets}, lambda: run_soak(c, args.soak_resets))
    else:
        report["not_tested"].append("Reset soak (disabled)")
    report["finished_utc"] = datetime.now(UTC).isoformat()
    report["passed"] = bool(report["cases"]) and all(c["status"] in ("pass", "limited") for c in report["cases"])
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {args.output.resolve()}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
