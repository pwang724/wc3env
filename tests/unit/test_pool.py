"""StepPool fan-out, exactness accounting and failure isolation, with fake games (no process)."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wc3env.pool import StepPool
from wc3env.session import GameConfig, PlayerConfig


class FakeRpc:
    """Advances its clock by exactly `ms` per step, except: `short_at` makes one step come up
    25 ms short; `die_at` raises ConnectionError on that step. Each step sleeps `delay` so
    concurrency shows up in wall time."""

    def __init__(self, delay=0.0, short_at=None, die_at=None):
        self.gametime = 1000
        self.delay = delay
        self.short_at = short_at
        self.die_at = die_at
        self.calls = 0
        self.threads = set()

    def info(self):
        return {"game_time_ms": self.gametime}

    def step(self, ms):
        self.calls += 1
        self.threads.add(threading.get_ident())
        if self.die_at == self.calls:
            raise ConnectionError("pipe closed")
        time.sleep(self.delay)
        self.gametime += ms - (25 if self.short_at == self.calls else 0)
        return {"game_time_ms": self.gametime, "frames": ms // 25}


class FakeGame:
    def __init__(self, pid, **kw):
        self.pid = pid
        self.rpc = FakeRpc(**kw)
        self.closed = False

    def close(self):
        self.closed = True


class PoolTest(unittest.TestCase):
    def test_exact_steps_are_counted_per_instance(self):
        games = [FakeGame(1), FakeGame(2, short_at=3)]
        pool = StepPool(games)
        s = pool.run(5, 250)
        self.assertEqual([i.steps for i in s.instances], [5, 5])
        self.assertEqual([i.exact for i in s.instances], [5, 4])
        self.assertEqual(s.instances[0].frames, 50)
        self.assertFalse(s.all_exact)
        self.assertAlmostEqual(s.game_seconds, 2.475)
        pool.close()
        self.assertTrue(all(g.closed for g in games))

    def test_instances_step_concurrently(self):
        games = [FakeGame(i, delay=0.05) for i in range(4)]
        pool = StepPool(games)
        s = pool.run(4, 250)
        # serial would be 4 x 4 x 50 ms = 0.8 s; concurrent is ~4 x 50 ms
        self.assertLess(s.wall_seconds, 0.5)
        self.assertTrue(s.all_exact)

    def test_dead_instance_is_retired_others_continue(self):
        a, b = FakeGame(1, die_at=2), FakeGame(2)
        pool = StepPool([a, b])
        s = pool.run(6, 250)
        self.assertFalse(s.all_exact)
        self.assertTrue(a.closed)
        self.assertEqual(s.instances[0].error, "ConnectionError: pipe closed")
        self.assertEqual(s.instances[0].steps, 1)
        self.assertEqual(s.instances[1].steps, 6)
        self.assertEqual(a.rpc.calls, 2)  # not called again after it died
        self.assertIn("died: ConnectionError", s.text())

    def test_stops_when_every_instance_is_dead(self):
        game = FakeGame(1, die_at=1)
        pool = StepPool([game])
        s = pool.run(100, 250)
        self.assertTrue(game.closed)
        self.assertEqual((s.rounds, s.instances[0].steps), (1, 0))
        self.assertLess(s.wall_seconds, 1.0)

    def test_stalled_replies_are_retired_others_continue(self):
        stalled, healthy = FakeGame(1), FakeGame(2)
        pool = StepPool([stalled, healthy])
        self.addCleanup(pool.close)
        before = stalled.rpc.info()["game_time_ms"]
        with patch.object(
            stalled.rpc, "step", return_value={"game_time_ms": before + 100, "frames": 2, "reason": "stalled"}
        ):
            summary = pool.run(2, 250)
        self.assertTrue(stalled.closed)
        self.assertIn("stalled", summary.instances[0].error)
        self.assertEqual((summary.instances[0].steps, summary.instances[0].game_ms), (1, 100))
        self.assertEqual(summary.instances[1].exact, 2)
        self.assertFalse(healthy.closed)


class SetupRpc(FakeRpc):
    def __init__(self):
        super().__init__()
        self.setup = None
        self.tuning = []

    def create_game(self, map, players, mode):
        self.setup = (map, players, mode)
        return {"map": map, "players": players}

    def debug(self, op, **args):
        if self.setup is None:
            raise AssertionError("tuning must wait until the map is loaded")
        self.tuning.append((op, args))


class SetupGame(FakeGame):
    def __init__(self, pid, map):
        super().__init__(pid)
        self.map = map
        self.rpc = SetupRpc()
        self.parked = False

    def offscreen(self):
        self.parked = True


class PoolLaunchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.maps = [self.root / name for name in ("first.w3x", "second.w3x")]
        for path in self.maps:
            path.touch()
        settings = patch("wc3env.game.settings", return_value=SimpleNamespace(game_dir=self.root, map=self.maps[0]))
        settings.start()
        self.addCleanup(settings.stop)
        self.games = []

        def launch(**kwargs):
            game = SetupGame(100 + len(self.games), kwargs["map"])
            self.games.append(game)
            return game

        launcher = patch("wc3env.pool.launch", side_effect=launch)
        self.launcher = launcher.start()
        self.addCleanup(launcher.stop)

    def test_each_process_gets_its_own_config_and_tuning_after_map_load(self):
        configs = [
            GameConfig(map=str(self.maps[0]), players=(PlayerConfig(1, race="human"),), step_ms=250),
            GameConfig(map=str(self.maps[1]), players=(PlayerConfig(0), PlayerConfig(1)), step_ms=250),
        ]
        pool = StepPool.launch_configs(configs, speed=64, offscreen=True)
        self.addCleanup(pool.close)
        for i, config in enumerate(configs):
            self.assertEqual(self.launcher.call_args_list[i].kwargs["map"], self.maps[i])
            self.assertEqual(
                self.games[i].rpc.setup, (str(self.maps[i]), [p.to_dict() for p in config.players], "stepping")
            )
            self.assertEqual(self.games[i].rpc.tuning[0], ("speed", {"factor": 64}))
            self.assertTrue(self.games[i].parked)
        self.assertTrue(pool.run(2).all_exact)

    def test_missing_later_map_never_launches_earlier_game(self):
        with self.assertRaises(FileNotFoundError):
            StepPool.launch_configs(
                [GameConfig(map=str(self.maps[0])), GameConfig(map=str(self.root / "missing.w3x"))], speed=64
            )
        self.launcher.assert_not_called()

    def test_failed_or_interrupted_launch_closes_earlier_games(self):
        for error in (TimeoutError("second launch failed"), KeyboardInterrupt()):
            with self.subTest(error=error):
                game = SetupGame(1, self.maps[0])
                self.launcher.side_effect = [game, error]
                with self.assertRaises(type(error)):
                    StepPool.launch(2, speed=64)
                self.assertTrue(game.closed)

    def test_setup_failure_retires_only_that_instance(self):
        failed = SetupGame(1, self.maps[0])
        healthy = SetupGame(2, self.maps[1])
        self.launcher.side_effect = [failed, healthy]
        with patch.object(failed.rpc, "create_game", side_effect=ValueError("unsupported player setup")):
            pool = StepPool.launch_configs([GameConfig(map=str(path)) for path in self.maps], speed=64)
        self.addCleanup(pool.close)
        self.assertTrue(failed.closed)
        self.assertFalse(healthy.closed)
        self.assertEqual(pool.stats[0].error, "ValueError: unsupported player setup")
        results = pool.step_all(250)
        self.assertIsNone(results[0])
        self.assertEqual(results[1]["game_time_ms"], 1250)
        self.assertEqual((failed.rpc.calls, healthy.rpc.calls), (0, 1))


if __name__ == "__main__":
    unittest.main()
