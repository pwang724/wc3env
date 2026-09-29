"""Many games stepped asynchronously by worker processes, for RL rollouts.

    with VectorSession([config] * 16, group_size=2) as games:
        for i, observations in games.reset():          # every game's first observations
            games.send(i, policy(observations))
        while training:
            i, observations, done, info = games.recv()  # whichever game finishes its step first
            games.send(i, policy(observations))

Each worker process hosts a group of games (one thread and GameSession each; use
`observation="binary"`). `send` returns at once; `recv` returns the next finished step from any game,
so the policy can batch whatever is ready while the others simulate. A finished episode resets inside
its worker: `recv` then reports `done` with the final observations in `info["final_observations"]` and
the new episode's first observations as `observations`, so a reload never stalls the other games.

Workers speak length-prefixed pickles over stdin/stdout, so the host needs no Windows: on a Linux
host, `launch=wine_workers()` runs each worker as Windows Python under Wine in its own copy of the Wine
prefix. Each prefix has its own wineserver, which without ntsync stops scaling at about four games;
two games per worker measured best (docs/compatibility.md).
"""

from __future__ import annotations

import os
import pickle
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from .session import GameConfig, GameSession

Launch = Callable[[int], tuple[list[str], dict | None]]  # worker index -> argv, environment (None: inherit)


def local_workers(worker: int) -> tuple[list[str], dict | None]:
    """Workers in this Python (Windows)."""
    return [sys.executable, "-m", "wc3env.vector"], None


def wine_workers(prefix: str | None = None, python: str = r"C:\Python311\python.exe") -> Launch:
    """Workers as Windows Python under Wine, from a Linux host: worker 0 in `prefix` ($WINEPREFIX), the
    others in copies of it in the temporary directory, each with its own wineserver."""
    base = Path(prefix or os.environ["WINEPREFIX"])

    def launch(worker: int) -> tuple[list[str], dict | None]:
        path = base if worker == 0 else Path(tempfile.gettempdir()) / f"wine-prefix-{worker}"
        if not path.exists():
            shutil.copytree(base, path, symlinks=True)
        return ["wine", python, "-m", "wc3env.vector"], {**os.environ, "WINEPREFIX": str(path)}

    return launch


def _write(stream, message) -> None:
    data = pickle.dumps(message, pickle.HIGHEST_PROTOCOL)
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def _read(stream):
    """The next message, or None at end of stream."""
    head = stream.read(4)
    if len(head) < 4:
        return None
    return pickle.loads(stream.read(struct.unpack("<I", head)[0]))


class VectorSession:
    def __init__(
        self,
        configs: Sequence[GameConfig],
        *,
        group_size: int = 1,
        launch: Launch = local_workers,
        speed: float = 2048,
        wait_floor: int = 1,
    ):
        if not configs or group_size < 1:
            raise ValueError("VectorSession needs at least one GameConfig and a positive group_size")
        self._games = len(configs)
        self._where = [(i // group_size, i % group_size) for i in range(self._games)]  # game -> (worker, slot)
        self._game = {where: i for i, where in enumerate(self._where)}
        self._results: queue.Queue = queue.Queue()
        self._waiting: list = []  # results read while waiting for a particular game
        self._busy: set[int] = set()
        self._workers = []
        for w in range(0, self._games, group_size):
            argv, env = launch(w // group_size)
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
            _write(proc.stdin, (list(configs[w : w + group_size]), speed, wait_floor))
            threading.Thread(target=self._drain, args=(len(self._workers), proc), daemon=True).start()
            self._workers.append(proc)

    def __len__(self) -> int:
        return self._games

    def _drain(self, worker: int, proc) -> None:
        """Every reply of one worker into the shared queue; a worker that exits fails its games."""
        while (message := _read(proc.stdout)) is not None:
            slot, status, payload = message
            self._results.put((self._game[worker, slot], status, payload))
        error = f"worker {worker} exited with {proc.wait()}"
        for (w, _), i in self._game.items():
            if w == worker:
                self._results.put((i, "error", error))

    def _take(self, game: int | None = None, timeout: float | None = None):
        """The next reply (for `game` only, if given; others wait for recv) as (index, payload)."""
        while True:
            match = next((r for r in self._waiting if game is None or r[0] == game), None)
            if match:
                self._waiting.remove(match)
                i, status, payload = match
                break
            try:
                self._waiting.append(self._results.get(timeout=timeout))
            except queue.Empty:
                return None
        self._busy.discard(i)
        if status == "error":
            raise RuntimeError(f"game {i}: {payload}; call reset_one({i})")
        return i, payload

    def _send(self, i: int, message) -> None:
        worker, slot = self._where[i]
        self._busy.add(i)
        _write(self._workers[worker].stdin, (slot, message))

    def reset(self) -> list[tuple[int, dict]]:
        """Reset every game (in parallel); returns (index, observations) for each, in index order."""
        for i in range(self._games):
            self._send(i, "reset")
        return [self._take(i) for i in range(self._games)]

    def reset_one(self, i: int) -> dict:
        self._send(i, "reset")
        return self._take(i)[1]

    def send(self, i: int, actions: dict) -> None:
        """Start game i's next step with one action batch per agent slot; returns at once."""
        if i in self._busy:
            raise RuntimeError(f"game {i} is already stepping; recv() its result first")
        self._send(i, actions)

    def recv(self, timeout: float | None = None):
        """(index, observations, done, info) of the first game to finish a step, or None on timeout."""
        if not self._busy:
            raise RuntimeError("no game is stepping; send() first")
        taken = self._take(timeout=timeout)
        return None if taken is None else (taken[0], *taken[1])

    def close(self) -> None:
        for proc in self._workers:
            try:
                proc.stdin.close()  # end of input: the worker closes its games and exits
            except OSError:
                pass
        for proc in self._workers:
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _serve() -> None:
    """A worker: the games of one group, each on its own thread, answering over stdin/stdout."""
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must not corrupt the replies
    configs, speed, wait_floor = _read(stdin)
    if any(c.observation == "binary" for c in configs):
        # Before the blocking reads of stdin below: on Windows, numpy's import touches the standard
        # handles and would wait behind a pending read, deadlocking the first game thread that imports it.
        from . import binary  # noqa: F401
    lock = threading.Lock()
    inboxes = [queue.Queue() for _ in configs]

    def game(slot: int, config: GameConfig) -> None:
        session, tuned = GameSession(config), False

        def reset():
            nonlocal tuned
            observations = session.reset()
            if not tuned:  # the session replays tuning on the processes it launches later
                session.debug("speed", factor=speed)
                session.debug("waitfloor", ms=wait_floor)
                tuned = True
            return observations

        try:
            while (message := inboxes[slot].get()) is not None:
                try:
                    if message == "reset":
                        reply = reset()
                    else:
                        observations, done, info = session.step(message)
                        if done:
                            info["final_observations"] = observations
                            observations = reset()
                        reply = (observations, done, info)
                    status = "ok"
                except Exception as exc:  # noqa: BLE001  report; the host asks for a fresh episode
                    status, reply = "error", f"{type(exc).__name__}: {exc}"
                with lock:
                    _write(stdout, (slot, status, reply))
        finally:
            session.close()

    threads = [threading.Thread(target=game, args=(k, c)) for k, c in enumerate(configs)]
    for thread in threads:
        thread.start()
    while (message := _read(stdin)) is not None:
        slot, payload = message
        inboxes[slot].put(payload)
    for inbox in inboxes:
        inbox.put(None)
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    _serve()
