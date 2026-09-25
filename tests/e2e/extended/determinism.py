"""A complete native melee match must repeat across reload and process replacement."""

import unittest

from tests.e2e.support import state
from wc3env.session import GameConfig, GameSession, MatchSetup, PlayerConfig


class DeterminismTest(unittest.TestCase):
    def test_complete_melee_match_repeats(self):
        config = GameConfig(
            map="(2)EchoIsles.w3x",
            players=(PlayerConfig(0, "human"), PlayerConfig(1, "orc", "computer")),
            setup=MatchSetup(seed=42),
            render=False,
            step_ms=1000,
            max_episodes_per_process=2,
        )
        with GameSession(config) as session:
            reference = None
            pids = []
            for episode in range(3):
                observations = session.reset()
                pids.append(session.game.pid)
                initial = observations[1]["score"].copy()
                session.game.rpc.debug("speed", factor=64)
                session.game.rpc.debug("waitfloor", ms=1)
                trace = [state(observations)]
                for tick in range(2400):
                    observations, done, info = session.step({0: []})
                    self.assertEqual(info["rejected"], {0: []})
                    trace.append(state(observations))
                    if done:
                        break
                self.assertTrue(done, "native AI must finish within 40 simulated minutes")
                self.assertEqual([observations[p]["result"] for p in (0, 1)], ["defeat", "victory"])
                for counter in ("gold_mined", "units_trained", "structures_built"):
                    self.assertGreater(observations[1]["score"][counter], initial[counter])
                if reference is None:
                    reference = trace
                else:
                    self.assertEqual(len(trace), len(reference))
                    for tick, (expected, actual) in enumerate(zip(reference, trace)):
                        self.assertEqual(actual, expected, f"episode {episode}, sample {tick}")
            self.assertEqual(pids[0], pids[1])
            self.assertNotEqual(pids[0], pids[2])


if __name__ == "__main__":
    unittest.main()
