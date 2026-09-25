"""The e2e suite: every scenario in tests/e2e/configs through WC3Env over the RPC against the
real game (docs/design.md).

    python -m tests.e2e                        (from the repository root: every config not marked long)
    python -m tests.e2e worker_loop --ticks 40

`WC3Env.reset()` launches and initializes a game, one env step per game second, actions through `act`, the
observation from `observe`. A scenario's staging (`Stage` ops from its steps, the config's
`setup` list, cheats) goes through the server's `debug` method. Exit code 0 only if every
scenario's own checks pass. Results land in tests/e2e/out/scenarios/<name>.json (gitignored).
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.e2e.scenarios.base import Stage
from wc3env.env import GameConfig, WC3Env
from wc3env.protocol import Observation, ProtocolError
from wc3env.session import MatchSetup, PlayerConfig

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = ROOT / "tests" / "e2e" / "configs"
OUTPUT = ROOT / "tests" / "e2e" / "out" / "scenarios"
MAP = "(2)EchoIsles.w3x"


def load_agent(config: dict):
    module, _, cls = config["agent"].partition(":")
    return getattr(importlib.import_module(module), cls)(**config.get("params", {}))


def cheat_ops(cheats: list[str], agent, config: dict) -> list[dict]:
    """The single-player cheats a config names, as staging ops (warpten stretches the timeouts)."""
    ops = []
    for cheat in cheats:
        if cheat.startswith("greedisgood"):
            n = int(cheat.split()[1]) if " " in cheat else 500
            ops.append({"op": "resources", "player": 0, "gold": n, "lumber": n})
        elif cheat == "warpten":
            for st in agent.steps:
                st.timeout *= 10
            config["max_ticks"] *= 10
        elif cheat == "whosyourdaddy":
            ops.append({"op": "invulnerable", "player": 0, "on": 1})
        else:
            print(f"warning: cheat {cheat!r} has no equivalent")
    return ops


def run(name: str, ticks: int | None, speed: float, configs: Path = CONFIGS, output: Path = OUTPUT) -> dict:
    config = json.loads((configs / f"{name}.json").read_text())
    agent = load_agent(config)
    setup = cheat_ops(config.get("cheats", []), agent, config) + config.get("setup", [])
    max_ticks = ticks or config["max_ticks"]

    players = tuple(
        PlayerConfig(int(slot), race, "agent" if int(slot) == 0 else "computer")
        for slot, race in config["races"].items()
    )
    env = WC3Env(
        GameConfig(
            map=config.get("map", MAP),
            players=players,
            step_ms=int(1000 * config.get("tick_seconds", 1.0)),
            # A seed opts in to startup overrides, so races other than the map default can be requested.
            setup=MatchSetup(seed=config["seed"]) if "seed" in config else None,
        )
    )
    report = {"scenario": name, "ticks": 0, "rejected": 0, "errors": [], "wall_seconds": 0.0}
    report["timings_ms"] = {"agent": 0.0}
    report["rpc_timings_ms"] = {"server": 0.0, "game_thread": 0.0, "overhead": 0.0}
    t0 = time.time()
    obs = None
    game = None
    try:
        obs = env.reset()
        game = env.session.game
        report["setup"] = env.session.setup
        game.rpc.debug("speed", factor=speed)
        game.rpc.debug("waitfloor", ms=1)
        game.rpc.debug("render", on=0)
        for op in setup:
            game.rpc.debug(**op)
        if setup:
            obs, _, _ = env.step([])  # let the staging land before the scenario looks
        for i in range(max_ticks):
            agent_started = time.perf_counter()
            try:
                out = agent.act(Observation.from_dict(obs))
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"tick {obs['sequence']}: agent {type(exc).__name__}: {exc}")
                out = []
            report["timings_ms"]["agent"] += 1000 * (time.perf_counter() - agent_started)
            for s in out:
                if isinstance(s, Stage):
                    game.rpc.debug(s.op, **s.args)
            actions = [a for a in out if not isinstance(a, Stage)]
            try:
                obs, done, info = env.step(actions)
            except ProtocolError as exc:
                report["errors"].append(f"tick {obs['sequence']}: {exc}")
                obs, done, info = env.step([])
            report["ticks"] += 1
            for key, value in info["timings_ms"].items():
                report["timings_ms"][key] = report["timings_ms"].get(key, 0.0) + value
            for timing in info["rpc_timings_ms"]:
                for key in report["rpc_timings_ms"]:
                    value = timing.get(key)
                    if value is not None:
                        report["rpc_timings_ms"][key] += value
            report["rejected"] += len(info["rejected"])
            if info["rejected"]:
                print(f"tick {obs['sequence']}: rejected {info['rejected']}")
            if i % 50 == 0:
                print(
                    f"{name}: tick {obs['sequence']} gold={obs['player']['gold']} units={len(obs['units'])} {time.time() - t0:.1f}s",
                    flush=True,
                )
            if done:
                agent.act(Observation.from_dict(obs))  # the final observation carries the result
            if done or agent.finished:
                break
    except Exception as exc:  # noqa: BLE001  a silent game: keep its thread stacks and RPC log with the report
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        try:
            game.pipe.send("where")
            time.sleep(1.5)
            log = game.log().splitlines()
            report["hook_log"] = [l for l in log if l.startswith(("rpc", "thread", "  ", "step", "native"))][-60:]
            print("\n".join(report["hook_log"][:40]))
        except Exception:  # noqa: BLE001
            pass
    finally:
        env.close()
    report["wall_seconds"] = round(time.time() - t0, 2)
    if isinstance(obs, dict):  # the last observation, in brief: what the scenario saw when it stopped
        report["last"] = {
            "tick": obs.get("sequence"),
            "gold": obs["player"]["gold"],
            "units": sorted((u["type_id"], u["hp"]) for u in obs["units"]),
            "enemies": sorted((u["type_id"], u["hp"]) for u in obs["visible_enemies"]),
            "events": [e["kind"] for e in obs["events"]],
        }
    report["scenario_summary"] = agent.summary()
    report["passed"] = report["scenario_summary"]["passed"] and not report["errors"]
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{name}.json").write_text(json.dumps(report, indent=1))
    return report


def show(r: dict) -> None:
    checks = r["scenario_summary"]["checks"]
    print(
        f"{r['scenario']}: {r['ticks']} ticks in {r['wall_seconds']}s; {sum(c['status'] == 'pass' for c in checks)}/{len(checks)} checks; "
        f"{r['rejected']} rejected actions; {'PASS' if r['passed'] else 'FAIL'}"
    )
    for c in checks:
        if c["status"] not in ("pass", "skipped"):
            print(f"  check {c['step']}: {c['status']} {c['note']}")
    for e in r["errors"][:5]:
        print(f"  {e}")


def run_module(module: str) -> dict:
    """One unittest module in its own interpreter, so modules can run side by side."""
    started = time.time()
    done = subprocess.run([sys.executable, "-m", "unittest", module], cwd=ROOT, capture_output=True, text=True)
    lines = done.stderr.strip().splitlines()
    return {
        "module": module,
        "passed": done.returncode == 0,
        "summary": " ".join(lines[-3:]) if done.returncode == 0 else "\n".join(lines[-40:]),
        "wall_seconds": round(time.time() - started, 1),
    }


def main(configs: Path = CONFIGS, output: Path = OUTPUT, modules: list[str] | None = None) -> None:
    if modules is None:
        modules = [f"tests.e2e.{p.stem}" for p in sorted((ROOT / "tests" / "e2e").glob("test_*.py"))]
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "scenario",
        nargs="?",
        default="all",
        help="a scenario config name, or `all`: every test module and every config not marked long",
    )
    ap.add_argument("--ticks", type=int)
    ap.add_argument("--long", action="store_true")
    ap.add_argument("--speed", type=float, default=64.0)
    ap.add_argument("--jobs", type=int, default=4, help="modules and scenarios to run at once")
    ap.add_argument("--scenarios-only", action="store_true", help="with `all`, skip the unittest modules")
    a = ap.parse_args()
    if a.scenario == "all":
        names = [p.stem for p in sorted(configs.glob("*.json")) if a.long or not json.loads(p.read_text()).get("long")]
    else:
        names = [a.scenario]
    if a.scenario != "all" or a.scenarios_only:
        modules = []
    # Every job owns its game processes; these threads only wait on them. Modules go first: they run longest.
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        module_jobs = [pool.submit(run_module, m) for m in modules]
        scenario_jobs = [pool.submit(run, n, a.ticks, a.speed, configs, output) for n in names]
        module_results = [j.result() for j in module_jobs]
        results = [j.result() for j in scenario_jobs]
    for m in module_results:
        print(f"{m['module']}: {m['wall_seconds']}s; {'PASS' if m['passed'] else 'FAIL'}; {m['summary']}")
    for r in results:
        show(r)
    if module_results:
        print(f"{sum(m['passed'] for m in module_results)}/{len(module_results)} test modules passed")
    if len(results) > 1:
        print(f"{sum(r['passed'] for r in results)}/{len(results)} scenarios passed")
    sys.exit(0 if all(r["passed"] for r in results + module_results) else 1)


if __name__ == "__main__":
    main()
