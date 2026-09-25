"""Verify game control inside Windows Python under Wine and save engine outcomes."""

from __future__ import annotations

import argparse
import json
import math
import time
import traceback
from pathlib import Path

from wc3env.compatibility import EXE_SHA256
from wc3env.env import WC3Env
from wc3env.session import GameConfig


def checkpoint(report, name):
    report["checkpoint"] = name
    print(f"Engine checkpoint: {name}", flush=True)


def exercise(env, report):
    checkpoint(report, "reset")
    obs = env.reset()
    game = env.session.game
    pid = game.pid
    info = game.rpc.info()
    assert info["exe_hash"] == EXE_SHA256, info
    report.update(pid=pid, initial_game_seconds=obs["game_time_seconds"], engine=info)
    game.rpc.debug("speed", factor=1)
    workers = [u for u in obs["units"] if u["type_id"] in {"hpea", "opeo", "ewsp", "uaco"}]
    assert workers, "No visible worker in the initial observation"
    worker = workers[0]
    uid, x0, y0 = worker["unit_id"], worker["x"], worker["y"]
    actions = [{"unit_id": uid, "command": "move", "arguments": {"x": x0 - 400, "y": y0}}]
    checkpoint(report, "move_and_step")
    steps = []
    start = time.monotonic()
    for i in range(4):
        before = obs["game_time_seconds"]
        obs, done, info = env.step(actions if i == 0 else [])
        assert not done, "Game ended during movement test"
        assert not info.get("rejected"), info
        assert info["elapsed_ms"] == 1000, info
        assert abs(obs["game_time_seconds"] - before - 1) < 0.001, "Unexpected game time advance"
        steps.append(info["elapsed_ms"])
    unit = next(u for u in obs["units"] if u["unit_id"] == uid)
    distance = math.hypot(unit["x"] - x0, unit["y"] - y0)
    assert distance > 50, f"Worker moved only {distance:.1f} world units"
    report.update(step_elapsed_ms=steps, step_wall_seconds=time.monotonic() - start, moved_distance=distance)
    checkpoint(report, "second_reset")
    fresh = env.reset()
    assert env.session.game.pid == pid, "Reset unexpectedly replaced the process"
    assert fresh["game_time_seconds"] == report["initial_game_seconds"], "Reset did not restore initial game time"
    report["reset_game_seconds"] = fresh["game_time_seconds"]
    checkpoint(report, "close")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"status": "running", "checkpoint": "launch"}
    env = WC3Env(GameConfig(step_ms=1000, window_mode="interactive", render=True))
    try:
        exercise(env, report)
        env.close()
        report.update(status="passed", checkpoint="complete")
    except BaseException:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        try:
            env.close()
        finally:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
