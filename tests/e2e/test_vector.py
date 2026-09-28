"""VectorSession steps games asynchronously and returns whichever finishes first."""

import unittest

from wc3env.session import GameConfig, MatchSetup, PlayerConfig
from wc3env.vector import VectorSession

CONFIG = GameConfig(
    map="(2)EchoIsles.w3x",
    players=(PlayerConfig(0, "human"), PlayerConfig(1, "orc", control="computer")),
    setup=MatchSetup(seed=3),
    render=False,
    sound=False,
    step_ms=250,
    observation="binary",
)


class VectorSessionTest(unittest.TestCase):
    def test_games_step_independently(self):
        with VectorSession([CONFIG, CONFIG]) as games:
            started = games.reset()
            self.assertEqual([i for i, _ in started], [0, 1])
            for i, _ in started:
                games.send(i, {0: []})
            steps = [0, 0]
            while sum(steps) < 40:
                i, observations, done, info = games.recv()
                self.assertFalse(done)
                self.assertEqual(info["elapsed_ms"], 250)
                steps[i] += 1
                self.assertEqual(observations[0].game_time_seconds, 1 + 0.25 * steps[i])
                if steps[i] < 20:
                    games.send(i, {0: []})
            self.assertEqual(steps, [20, 20])
            with self.assertRaisesRegex(RuntimeError, "send"):
                games.recv()


if __name__ == "__main__":
    unittest.main()
