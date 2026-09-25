"""StepPool: N hooked games in step mode, stepped concurrently, one driver thread each.

    python -m wc3env --instances 2 --steps 200 --stepms 250 --speed 2048

Each instance is a `Game` on its own pipe; a step round fans `step` out to every live instance
and waits for all of them. An instance whose pipe dies or whose step fails is retired with the
error recorded; the others keep going. The summary counts exact steps (game-time delta equals
the request) per instance, so a slow or stalled copy shows up as a number rather than a hang.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from .game import launch, resolve_map
from .session import GameConfig, MatchSetup, PlayerConfig


@dataclass
class InstanceStats:
    pid: int | None
    steps: int = 0
    exact: int = 0
    game_ms: int = 0
    wall_ms: float = 0.0  # summed over this instance's own step calls
    frames: int = 0  # GameUpdate calls the DLL saw across all steps
    error: str | None = None  # set once, when the instance is retired

    @property
    def alive(self) -> bool:
        return self.error is None


@dataclass
class PoolSummary:
    stepms: int
    rounds: int
    wall_seconds: float
    instances: list[InstanceStats] = field(default_factory=list)

    @property
    def game_seconds(self) -> float:
        return sum(i.game_ms for i in self.instances) / 1000

    @property
    def realtime_factor(self) -> float:
        return self.game_seconds / self.wall_seconds if self.wall_seconds else 0.0

    @property
    def all_exact(self) -> bool:
        return bool(self.instances and self.rounds) and all(
            i.alive and i.exact == i.steps == self.rounds for i in self.instances
        )

    def text(self) -> str:
        lines = [
            f"{len(self.instances)} instances x {self.rounds} steps of {self.stepms} ms: "
            f"{self.game_seconds:.0f} game s in {self.wall_seconds:.2f} s wall = {self.realtime_factor:.0f}x realtime aggregate"
        ]
        for n, i in enumerate(self.instances):
            per = i.wall_ms / i.steps if i.steps else 0.0
            fps = i.frames / i.steps if i.steps else 0.0
            state = f"died: {i.error}" if i.error else "ok"
            lines.append(
                f"  instance {n} pid {i.pid}: {i.exact}/{i.steps} exact, {per:.1f} ms/step, {fps:.1f} frames/step, {state}"
            )
        return "\n".join(lines)


class StepPool:
    """Games need `pid`, `rpc.info()`, `rpc.step(ms)` and `close()`; see `Game`."""

    def __init__(self, games: list):
        if not games:
            raise ValueError("pool requires at least one game")
        self.games = list(games)
        self.configs: tuple[GameConfig, ...] | None = None
        self._step_ms = GameConfig().step_ms
        self.stats = [InstanceStats(pid=g.pid) for g in games]
        self._gametime: list[int | None] = [None] * len(games)
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(games)))

    @classmethod
    def launch(
        cls,
        n: int,
        speed: float,
        render: bool | None = None,
        offscreen: bool = False,
        spin: bool = False,
        *,
        config: GameConfig | None = None,
    ) -> StepPool:
        """Launch n copies of one setup. Put render in config when supplying a config;
        the legacy render argument remains available for calls using the default setup."""
        if type(n) is not int or n <= 0:
            raise ValueError("n must be a positive integer")
        if config is not None and render is not None:
            raise ValueError("put render in GameConfig instead of supplying both config and render")
        if config is None:
            config = GameConfig() if render is None else GameConfig(render=render)
        return cls.launch_configs([config] * n, speed, offscreen=offscreen, spin=spin)

    @classmethod
    def launch_configs(
        cls, configs: Sequence[GameConfig], speed: float, *, offscreen: bool = False, spin: bool = False
    ) -> StepPool:
        """Launch independent setups with one common step duration. Validate all configs
        and resolve all maps before launch. Process-launch failure closes earlier games;
        a loaded game's setup failure retires only that instance, recorded in stats."""
        if not isinstance(configs, Sequence):
            raise ValueError("configs must be a nonempty sequence of GameConfig objects")
        configs = tuple(configs)
        if not configs or any(not isinstance(c, GameConfig) for c in configs):
            raise ValueError("configs must be a nonempty sequence of GameConfig objects")
        if any(c.mode != "stepping" for c in configs):
            raise ValueError("StepPool requires stepping mode for every game")
        if any(c.step_ms != configs[0].step_ms for c in configs):
            raise ValueError("all pool configurations must use the same step_ms")
        # The debug speed RPC accepts finite multipliers in (0, 2048].
        if type(speed) not in (int, float) or not 0 < speed <= 2048:
            raise ValueError("speed must be a finite number greater than 0 and at most 2048")
        if type(offscreen) is not bool or type(spin) is not bool:
            raise ValueError("offscreen and spin must be bools")
        maps = [resolve_map(c.map) for c in configs]
        games = []
        try:
            for i, (config, map_path) in enumerate(zip(configs, maps)):
                games.append(
                    launch(
                        instance=i,
                        map=map_path,
                        agents=config.agent_slots,
                        window_mode=config.window_mode,
                        render=config.render,
                        sound=config.sound,
                        output_dir=config.output_dir,
                        **config.launch_setup(),
                    )
                )
            pool = cls(games)
        except BaseException:
            for game in games:
                game.close()
            raise
        pool.configs = configs
        pool._step_ms = configs[0].step_ms

        def start(i: int) -> str | None:
            g, config = pool.games[i], configs[i]
            try:
                g.rpc.create_game(str(g.map), [p.to_dict() for p in config.players], config.mode)
                # Speed only once the map is up: at 64x a slow load (eight copies
                # booting at once) ended with the process gone 15 s in, no exit hook, no crash record.
                g.rpc.debug("speed", factor=speed)
                g.rpc.debug("waitfloor", ms=0 if spin else 1)
                if offscreen:
                    g.offscreen()
                return None
            except Exception as exc:  # noqa: BLE001
                return f"{type(exc).__name__}: {exc}"

        try:
            for i, err in enumerate(pool._pool.map(start, range(len(games)))):
                if err is not None:
                    pool.stats[i].error = err
                    pool.games[i].close()
        except BaseException:
            pool.close()
            raise
        return pool

    def _step_one(self, i: int, ms: int) -> dict:
        g, st = self.games[i], self.stats[i]
        if self._gametime[i] is None:
            self._gametime[i] = int(g.rpc.info()["game_time_ms"])
        t0 = time.perf_counter()
        r = g.rpc.step(ms)
        st.wall_ms += 1000 * (time.perf_counter() - t0)
        st.steps += 1
        st.frames += int(r.get("frames", 0))
        elapsed = r["game_time_ms"] - self._gametime[i]
        st.game_ms += elapsed
        st.exact += elapsed == ms
        self._gametime[i] = r["game_time_ms"]
        if r.get("reason") == "stalled":
            raise RuntimeError("game clock stalled")
        return r

    def step_all(self, ms: int | None = None) -> list:
        """One round: every live instance advances `ms`. Returns per-instance `step` results, or
        the exception that retired the instance. Omitted ms uses the configured duration."""
        ms = self._step_ms if ms is None else ms
        GameConfig(step_ms=ms)  # validate before any instance advances
        live = [i for i, st in enumerate(self.stats) if st.alive]
        futures = {i: self._pool.submit(self._step_one, i, ms) for i in live}
        results: list = [None] * len(self.games)
        for i, f in futures.items():
            try:
                results[i] = f.result()
            except Exception as exc:  # noqa: BLE001  one dead game must not stop the rest
                self.stats[i].error = f"{type(exc).__name__}: {exc}"
                results[i] = exc
                self.games[i].close()
        return results

    def run(self, steps: int, ms: int | None = None, report_every: int = 0) -> PoolSummary:
        if type(steps) is not int or steps <= 0:
            raise ValueError("steps must be a positive integer")
        ms = self._step_ms if ms is None else ms
        GameConfig(step_ms=ms)
        before = [replace(st) for st in self.stats]
        t0 = time.perf_counter()
        rounds = 0
        for r in range(1, steps + 1):
            if not any(st.alive for st in self.stats):
                break
            rounds = r
            self.step_all(ms)
            if report_every and r % report_every == 0:
                alive = sum(st.alive for st in self.stats)
                print(f"round {r}: {alive}/{len(self.stats)} alive, {time.perf_counter() - t0:.1f} s", flush=True)
            if not any(st.alive for st in self.stats):
                break
        snapshots = [
            replace(
                st,
                steps=st.steps - old.steps,
                exact=st.exact - old.exact,
                game_ms=st.game_ms - old.game_ms,
                wall_ms=st.wall_ms - old.wall_ms,
                frames=st.frames - old.frames,
            )
            for st, old in zip(self.stats, before)
        ]
        return PoolSummary(stepms=ms, rounds=rounds, wall_seconds=time.perf_counter() - t0, instances=snapshots)

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        for g in self.games:
            g.close()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, default=2)
    ap.add_argument("--steps", type=int, default=200)
    ap.add_argument("--stepms", type=int, default=250)
    ap.add_argument("--speed", type=float, default=2048.0)
    ap.add_argument("--map", help="map path or unique installed map filename")
    ap.add_argument("--agent-slot", type=int, action="append", help="agent slot; repeat for multiple agents")
    ap.add_argument("--computer-slot", type=int, action="append", help="computer slot; repeat for multiple computers")
    ap.add_argument("--configure-match", action="store_true", help="apply player settings before melee startup")
    ap.add_argument(
        "--race", action="append", default=[], metavar="SLOT:RACE", help="select a race for a configured slot"
    )
    ap.add_argument("--seed", type=int, help="startup and combat seed in 0..2147483647; enables match setup")
    ap.add_argument("--randomize-starts", action="store_true", help="shuffle map starts using the match seed")
    ap.add_argument("--window-mode", choices=("background", "interactive"), default="background")
    ap.add_argument("--render", action="store_true", help="keep drawing frames (about 3x slower)")
    ap.add_argument("--offscreen", action="store_true", help="park every game window off the desktop")
    ap.add_argument(
        "--spin",
        action="store_true",
        help="let scaled waits round to zero (one game above 25x; costs 2-3 cores per game)",
    )
    a = ap.parse_args(argv)
    if a.steps <= 0:
        ap.error("steps must be a positive integer")
    players = GameConfig().players
    try:
        if a.agent_slot or a.computer_slot:
            players = tuple(
                [PlayerConfig(p) for p in a.agent_slot or []]
                + [PlayerConfig(p, control="computer") for p in a.computer_slot or []]
            )
        races = {}
        for choice in a.race:
            slot_text, race = choice.split(":", 1)
            slot = int(slot_text)
            if slot in races or slot not in {p.slot for p in players}:
                raise ValueError("--race requires a unique configured player slot")
            races[slot] = race
        players = tuple(replace(p, race=races.get(p.slot, p.race)) for p in players)
        setup = (
            MatchSetup(seed=a.seed, randomize_starts=a.randomize_starts)
            if a.configure_match or a.race or a.seed is not None or a.randomize_starts
            else None
        )
        config = GameConfig(
            map=a.map, players=players, step_ms=a.stepms, window_mode=a.window_mode, render=a.render, setup=setup
        )
        pool = StepPool.launch(a.instances, a.speed, config=config, offscreen=a.offscreen, spin=a.spin)
    except ValueError as exc:
        ap.error(str(exc))
    try:
        for i, st in enumerate(pool.stats):
            if st.error:
                print(f"instance {i} pid {st.pid} did not start: {st.error}", flush=True)
        summary = pool.run(a.steps, report_every=max(1, a.steps // 10))
    finally:
        pool.close()
    print(summary.text())
    return 0 if summary.all_exact else 1
