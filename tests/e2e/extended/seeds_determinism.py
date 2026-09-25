"""Seeded native matches must repeat on other maps, races and seeds than the everyday check."""

import unittest

from tests.e2e.support import state
from wc3env import GameConfig, GameSession, MatchSetup, PlayerConfig

MATCHES = (
    ("(2)EchoIsles.w3x", ("night_elf", "undead"), 7),
    ("(2)SecretValley.w3x", ("orc", "human"), 20260920),
    ("(4)TwistedMeadows.w3x", ("undead", "night_elf", "orc", "human"), 1234),
)


def play(session):
    observations = session.reset()
    session.game.rpc.debug("speed", factor=64)
    session.game.rpc.debug("waitfloor", ms=1)
    trace, done = [state(observations)], False
    for _ in range(3600):
        observations, done, _ = session.step({0: []})
        trace.append(state(observations))
        if done:
            break
    return trace, done, observations[0]["result"]


class DeterminismSeedsTest(unittest.TestCase):
    def test_idle_agent_loses_the_same_way_twice(self):
        for map_name, races, seed in MATCHES:
            players = (PlayerConfig(0, races[0]),) + tuple(
                PlayerConfig(slot, race, "computer") for slot, race in enumerate(races[1:], start=1)
            )
            config = GameConfig(map=map_name, players=players, setup=MatchSetup(seed=seed), render=False)
            with self.subTest(map=map_name, seed=seed), GameSession(config) as session:
                first, done, result = play(session)
                self.assertTrue(done, "native AI must finish within an hour of game time")
                self.assertEqual(result, "defeat")
                second, _, _ = play(session)
                self.assertEqual(len(second), len(first))
                for tick, (expected, actual) in enumerate(zip(first, second)):
                    self.assertEqual(actual, expected, f"sample {tick}")


if __name__ == "__main__":
    unittest.main()
