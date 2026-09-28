"""Throughput and memory benchmark for RL-style rollouts.

    python tools/bench.py --instances 4 --episodes 2 --observation binary --output runs/bench.json

Each instance is one GameSession, in its own Python process, playing full melee games: slot 0 is an
agent whose melee AI keeps playing (so armies, bases and fights grow like a real match), slot 1 is the
built-in computer. Every step advances the clock and observes, as a training loop would. An episode
ends when the game does or at --max-game-minutes.

Reports what decides rollout cost: game seconds per wall second and per CPU second (game processes
and their Python hosts; winproc.py), where each step's time goes, reset and launch times, and memory
per process over time (private, resident and used 32-bit address space, which is what runs out).
Works on Windows and under Wine (docker/, where linux_cpu.py adds the container's own CPU).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from winproc import cpu_seconds, k32, memory_mib  # noqa: E402

from wc3env.protocol import observation_result  # noqa: E402
from wc3env.session import RACES, GameConfig, GameSession, MatchSetup, PlayerConfig  # noqa: E402


def add_workload_arguments(parser: argparse.ArgumentParser) -> None:
    """The game every benchmark instance (and tools/profile_game.py) plays."""
    parser.add_argument("--map", default="(2)EchoIsles.w3x")
    parser.add_argument("--difficulty", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--step-ms", type=int, default=250)
    parser.add_argument("--observation", choices=("json", "binary"), default="json")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--speed", type=float, default=2048.0)
    parser.add_argument("--wait-floor", type=int, default=1, help="ms; 0 spins (costs extra cores)")
    parser.add_argument("--episodes-per-process", type=int, default=32)


def workload_config(args, index: int = 0) -> GameConfig:
    """Instance `index`: its own races and seed; slot 0's melee AI plays against the computer's."""
    return GameConfig(
        map=args.map,
        players=(PlayerConfig(0, RACES[index % 4]), PlayerConfig(1, RACES[(index + 1) % 4], control="computer")),
        ai_agents=(0,),
        ai_difficulty=args.difficulty,
        setup=MatchSetup(seed=args.seed + index, randomize_starts=True),
        render=args.render,
        sound=False,
        step_ms=args.step_ms,
        max_episodes_per_process=args.episodes_per_process,
        observation=args.observation,
    )


def tune(session: GameSession, args) -> None:
    """Once, after the first reset: the session replays tuning on the processes it launches later."""
    session.debug("speed", factor=args.speed)
    session.debug("waitfloor", ms=args.wait_floor)


class Totals:
    """Sums for one span of steps."""

    def __init__(self):
        self.steps = self.game_ms = self.units = 0
        self.phase_ms: dict[str, float] = {}
        self.server_ms: dict[str, float] = {}
        self.work_ms: dict[str, float] = {}

    def add(self, info: dict, units: int) -> None:
        self.steps += 1
        self.game_ms += info["elapsed_ms"]
        self.units += units
        for key, value in info["timings_ms"].items():
            self.phase_ms[key] = self.phase_ms.get(key, 0.0) + value
        for rpc in info["rpc_timings_ms"]:
            for field, sums in (("server", self.server_ms), ("game_thread", self.work_ms)):
                if rpc.get(field) is not None:
                    sums[rpc["method"]] = sums.get(rpc["method"], 0.0) + rpc[field]

    def per_step(self) -> dict:
        n = max(1, self.steps)
        return {
            "phase_ms": {k: round(v / n, 3) for k, v in self.phase_ms.items()},
            "server_ms": {k: round(v / n, 3) for k, v in self.server_ms.items()},
            "game_thread_ms": {k: round(v / n, 3) for k, v in self.work_ms.items()},
            "mean_units_seen": round(self.units / n, 1),
        }


def run_instance(index: int, args) -> dict:
    """One game in its own Python process, as an RL environment worker would run it."""
    host0 = cpu_seconds(k32.GetCurrentProcess())
    record = {"races": RACES[index % 4], "episodes": [], "error": None}
    pid = None
    with GameSession(workload_config(args, index)) as session:
        try:
            for episode in range(args.episodes):
                t0 = time.perf_counter()
                observations = session.reset()
                game = session.game
                if pid is None:
                    tune(session, args)
                ep = {
                    "pid": game.pid,
                    "relaunched": game.pid != pid,
                    "reset_seconds": round(time.perf_counter() - t0, 3),
                    "memory_start": memory_mib(game._process_handle),
                    "samples": [],
                }
                pid = game.pid
                record["episodes"].append(ep)
                window, total = Totals(), Totals()
                wall0 = window_wall = time.perf_counter()
                cpu0 = window_cpu = cpu_seconds(game._process_handle)
                done = False
                while not done and total.game_ms < args.max_game_minutes * 60_000:
                    observations, done, info = session.step({0: []})
                    obs = observations[0]
                    units = len(obs["units"]) if isinstance(obs, dict) else len(obs.units)
                    window.add(info, units)
                    total.add(info, units)
                    if window.game_ms >= args.sample_game_seconds * 1000 or done:
                        now, cpu = time.perf_counter(), cpu_seconds(game._process_handle)
                        wall = now - window_wall
                        s = {
                            "game_seconds": round(total.game_ms / 1000),
                            "units_seen": units,
                            "realtime_factor": round(window.game_ms / 1000 / wall, 1),
                            "game_cores": round((cpu - window_cpu) / wall, 2),
                            "ms_per_step": round(1000 * wall / window.steps, 2),
                            **window.per_step(),
                            "memory": memory_mib(game._process_handle),
                        }
                        ep["samples"].append(s)
                        print(
                            f"[{index}] ep {episode} t={s['game_seconds']}s units={units} {s['realtime_factor']}x "
                            f"{s['ms_per_step']} ms/step cores={s['game_cores']} "
                            f"addr={s['memory']['address_space']:.0f}MiB",
                            flush=True,
                        )
                        window, window_wall, window_cpu = Totals(), now, cpu
                ep.update(
                    steps=total.steps,
                    game_seconds=total.game_ms / 1000,
                    wall_seconds=round(time.perf_counter() - wall0, 2),
                    game_cpu_seconds=round(cpu_seconds(game._process_handle) - cpu0, 2),
                    finished=done,
                    results={p: observation_result(o) for p, o in observations.items()},
                    per_step=total.per_step(),
                    memory_end=memory_mib(game._process_handle),
                )
        except Exception as exc:  # noqa: BLE001  one failed game is a result, not the end of the run
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["traceback"] = traceback.format_exc()
            print(f"[{index}] failed: {record['error']}", flush=True)
    record["host_cpu_seconds"] = cpu_seconds(k32.GetCurrentProcess()) - host0
    return record


def summarize(records: list[dict], wall: float) -> dict:
    episodes = [e for r in records for e in r["episodes"] if "steps" in e]
    samples = [s for e in episodes for s in e["samples"]]
    game_seconds = sum(e["game_seconds"] for e in episodes)
    game_cpu = sum(e["game_cpu_seconds"] for e in episodes)
    host_cpu = sum(r["host_cpu_seconds"] for r in records)
    steps = sum(e["steps"] for e in episodes)
    resets = [e["reset_seconds"] for e in episodes if not e["relaunched"]]
    launches = [e["reset_seconds"] for e in episodes if e["relaunched"]]
    return {
        "instances": len(records),
        "failed_instances": sum(1 for r in records if r["error"]),
        "episodes": len(episodes),
        "steps": steps,
        "wall_seconds": round(wall, 1),
        "game_seconds": round(game_seconds),
        "aggregate_realtime_factor": round(game_seconds / wall, 1),
        "steps_per_second": round(steps / wall, 1),
        "game_cpu_cores": round(game_cpu / wall, 2),
        "host_cpu_cores": round(host_cpu / wall, 2),
        "game_seconds_per_cpu_second": round(game_seconds / (game_cpu + host_cpu), 1) if game_cpu else None,
        "median_ms_per_step": statistics.median(s["ms_per_step"] for s in samples) if samples else None,
        "reset_seconds_median": statistics.median(resets) if resets else None,
        "launch_seconds_median": statistics.median(launches) if launches else None,
        "peak_address_space_mib": max((s["memory"]["address_space"] for s in samples), default=None),
        "peak_private_mib": max((s["memory"]["private"] for s in samples), default=None),
        "peak_working_mib": max((s["memory"]["working"] for s in samples), default=None),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instances", type=int, default=1)
    parser.add_argument("--episodes", type=int, default=1, help="episodes per instance")
    parser.add_argument("--max-game-minutes", type=float, default=20.0)
    parser.add_argument("--sample-game-seconds", type=float, default=60.0)
    parser.add_argument("--stagger-seconds", type=float, default=2.0, help="delay between instance launches")
    parser.add_argument("--output", type=Path, default=ROOT / "runs" / "bench.json")
    add_workload_arguments(parser)
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.instances) as pool:
        futures = []
        for i in range(args.instances):
            futures.append(pool.submit(run_instance, i, args))
            time.sleep(args.stagger_seconds)
        records = [f.result() for f in futures]
    report = {
        "started_utc": datetime.now(UTC).isoformat(),
        "machine": {"os": platform.platform(), "python": sys.version, "cpu_count": os.cpu_count()},
        "arguments": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "summary": summarize(records, time.perf_counter() - t0),
        "instances": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {args.output.resolve()}")
    return 1 if report["summary"]["failed_instances"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
