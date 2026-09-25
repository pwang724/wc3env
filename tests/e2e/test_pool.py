"""Configured pool startup and stepping against independent offline game processes."""

import unittest
from pathlib import Path

from wc3env.pool import StepPool
from wc3env.session import GameConfig, MatchSetup, PlayerConfig


class PoolGameTest(unittest.TestCase):
    def test_distinct_startup_races_and_seeds(self):
        configs = [
            GameConfig(
                map="(2)EchoIsles.w3x",
                players=(PlayerConfig(0, race),),
                render=False,
                step_ms=250,
                setup=MatchSetup(seed=seed),
            )
            for race, seed in (("orc", 31), ("undead", 73))
        ]
        pool = StepPool.launch_configs(configs, speed=64)
        self.assert_setup_and_steps(pool, configs)
        for game, seed, hall in zip(pool.games, (31, 73), ("ogre", "unpl")):
            self.assertEqual(game.rpc.info()["match_setup"]["seed"], seed)
            self.assertIn(hall, {u["type_id"] for u in game.rpc.observe(0)["units"]})

    def assert_setup_and_steps(self, pool, configs):
        self.addCleanup(pool.close)
        self.assertEqual([s.error for s in pool.stats], [None] * len(configs))
        self.assertEqual(pool.configs, tuple(configs))
        self.assertEqual(len({g.pid for g in pool.games}), len(configs))
        self.assertEqual(len({g.data_dir for g in pool.games}), len(configs))
        for game, config in zip(pool.games, configs):
            info = game.rpc.info()
            self.assertEqual(Path(info["map"]).name, config.map)
            self.assertEqual(info["mode"], "stepping")
            for slot in config.agent_slots:
                obs = game.rpc.observe(slot)
                self.assertEqual(obs["observer"], slot)
                self.assertTrue(obs["units"])
                self.assertTrue(all(u["owner"] == slot for u in obs["units"]))
        for result in pool.step_all():
            self.assertEqual((result["elapsed_ms"], result["reason"]), (configs[0].step_ms, "target"))
        summary = pool.run(3)
        self.assertTrue(summary.all_exact, summary.text())
        self.assertEqual(summary.stepms, configs[0].step_ms)

    def test_shared_two_agent_config(self):
        config = GameConfig(
            map="(2)EchoIsles.w3x", render=False, step_ms=500, players=(PlayerConfig(0), PlayerConfig(1))
        )
        pool = StepPool.launch(2, speed=64, config=config)
        self.assert_setup_and_steps(pool, [config, config])

    def test_unsupported_loaded_controller_retires_only_that_game(self):
        configs = [
            GameConfig(map="(2)EchoIsles.w3x", render=False, step_ms=250),
            GameConfig(
                map="(2)SecretValley.w3x",
                render=False,
                step_ms=250,
                players=(PlayerConfig(1), PlayerConfig(0, control="computer")),
            ),
        ]
        pool = StepPool.launch_configs(configs, speed=64)
        self.addCleanup(pool.close)
        self.assertIsNone(pool.stats[0].error)
        self.assertIn("player 0 setup is unsupported", pool.stats[1].error or "")
        self.assertTrue(pool.games[1]._closed)
        first, second = pool.step_all()
        self.assertEqual((first["elapsed_ms"], first["reason"]), (250, "target"))
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
