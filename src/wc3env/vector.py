"""Many games stepped asynchronously, one worker process each, for RL rollouts.

    with VectorSession([config] * 16) as games:
        for i, observations in games.reset():          # every game's first observations
            games.send(i, policy(observations))
        while training:
            i, observations, done, info = games.recv()  # whichever game finishes its step first
            games.send(i, policy(observations))

A worker owns one GameSession (use `observation="binary"`). `send` returns at once; `recv` waits for
the next finished step from any game, so the policy can batch whatever is ready while other games
simulate. A finished episode resets inside its worker: `recv` then reports `done` with the final
observations in `info["final_observations"]` and the new episode's first observations as
`observations`, and a reload never stalls the other games. Separate processes keep one Python
host from limiting throughput.
"""

from __future__ import annotations

import multiprocessing as mp
from collections.abc import Sequence
from multiprocessing.connection import wait

from .session import GameConfig, GameSession


def _worker(conn, config: GameConfig, speed: float, wait_floor: int) -> None:
    session = GameSession(config)
    tuned = False

    def reset():
        nonlocal tuned
        observations = session.reset()
        if not tuned:  # the session replays tuning on the processes it launches later
            session.debug("speed", factor=speed)
            session.debug("waitfloor", ms=wait_floor)
            tuned = True
        return observations

    try:
        while True:
            message = conn.recv()
            if message is None:
                break
            if message == "reset":
                conn.send(("ok", reset()))
                continue
            try:
                observations, done, info = session.step(message)
                if done:
                    info["final_observations"] = observations
                    observations = reset()
                conn.send(("ok", (observations, done, info)))
            except Exception as exc:  # noqa: BLE001  report, then start a fresh episode on request
                conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        session.close()
        conn.close()


class VectorSession:
    def __init__(self, configs: Sequence[GameConfig], *, speed: float = 2048, wait_floor: int = 1):
        if not configs:
            raise ValueError("VectorSession needs at least one GameConfig")
        context = mp.get_context("spawn")
        self._conns = []
        self._procs = []
        for config in configs:
            parent, child = context.Pipe()
            proc = context.Process(target=_worker, args=(child, config, speed, wait_floor), daemon=True)
            proc.start()
            child.close()
            self._conns.append(parent)
            self._procs.append(proc)
        self._busy: set[int] = set()

    def __len__(self) -> int:
        return len(self._conns)

    def _receive(self, i: int):
        status, payload = self._conns[i].recv()
        self._busy.discard(i)
        if status == "error":
            raise RuntimeError(f"game {i}: {payload}; call reset_one({i})")
        return payload

    def reset(self) -> list[tuple[int, dict]]:
        """Reset every game (in parallel); returns (index, observations) for each."""
        for conn in self._conns:
            conn.send("reset")
        return [(i, self._receive(i)) for i in range(len(self._conns))]

    def reset_one(self, i: int) -> dict:
        self._conns[i].send("reset")
        return self._receive(i)

    def send(self, i: int, actions: dict) -> None:
        """Start game i's next step with one action batch per agent slot; returns at once."""
        if i in self._busy:
            raise RuntimeError(f"game {i} is already stepping; recv() its result first")
        self._busy.add(i)
        self._conns[i].send(actions)

    def recv(self, timeout: float | None = None):
        """(index, observations, done, info) of the first game to finish a step, or None on timeout."""
        if not self._busy:
            raise RuntimeError("no game is stepping; send() first")
        ready = wait([self._conns[i] for i in self._busy], timeout)
        if not ready:
            return None
        i = self._conns.index(ready[0])
        return (i, *self._receive(i))

    def close(self) -> None:
        for conn in self._conns:
            try:
                conn.send(None)
            except OSError:
                pass
        for proc in self._procs:
            proc.join(10)
            if proc.is_alive():
                proc.kill()
        for conn in self._conns:
            conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
