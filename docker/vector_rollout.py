"""Rollout throughput of VectorSession from a Linux host, as a trainer would run it.

    python3 vector_rollout.py --games 16 --group-size 2 --game-minutes 10

Native Python drives Wine workers (wine_workers: a Wine prefix, so a wineserver, per group of games).
Each game plays the benchmark's workload (slot 0's melee AI against the computer, binary observations)
with empty actions; a finished or timed-out episode resets inside its worker. Prints steps and game
seconds per wall second; linux_cpu.py adds the container's CPU.
"""

from __future__ import annotations

import argparse
import json
import time

from wc3env.session import RACES, GameConfig, MatchSetup, PlayerConfig
from wc3env.vector import VectorSession, wine_workers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=int, default=8)
    parser.add_argument("--group-size", type=int, default=2)
    parser.add_argument("--game-minutes", type=float, default=10.0, help="per game")
    parser.add_argument("--step-ms", type=int, default=250)
    parser.add_argument("--map", default="(2)EchoIsles.w3x")
    args = parser.parse_args()
    configs = [
        GameConfig(
            map=args.map,
            players=(PlayerConfig(0, RACES[i % 4]), PlayerConfig(1, RACES[(i + 1) % 4], control="computer")),
            ai_agents=(0,),
            ai_difficulty=2,
            setup=MatchSetup(seed=i + 1, randomize_starts=True),
            render=False,
            sound=False,
            step_ms=args.step_ms,
            observation="binary",
        )
        for i in range(args.games)
    ]
    steps_each = int(args.game_minutes * 60_000 / args.step_ms)
    t0 = time.monotonic()
    with VectorSession(configs, group_size=args.group_size, launch=wine_workers()) as games:
        started = games.reset()
        t1 = time.monotonic()
        for i, _ in started:
            games.send(i, {0: []})
        steps, finished = [0] * args.games, 0
        while finished < args.games:
            i, _, _, _ = games.recv()
            steps[i] += 1
            if steps[i] < steps_each:
                games.send(i, {0: []})
            else:
                finished += 1
        t2 = time.monotonic()
    total = sum(steps)
    report = {
        "games": args.games,
        "group_size": args.group_size,
        "startup_seconds": round(t1 - t0, 1),
        "rollout_seconds": round(t2 - t1, 1),
        "steps_per_second": round(total / (t2 - t1), 1),
        "aggregate_realtime_factor": round(total * args.step_ms / 1000 / (t2 - t1), 1),
    }
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
