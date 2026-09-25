"""Playback of a recorded game with building, heroes, spells and deaths must reproduce it exactly.

tests/fixtures/combat.w3g comes from record_combat_replay.py, which saved the native replay with
`save_replay` and printed the digest of the live game's state trace. Playing the file back has to
yield the same trace, second by second, and the same kinds of events.
"""

import unittest

from tests.e2e.support import state
from wc3env.game import launch

from .record_combat_replay import FIXTURE, PLAYERS, digest

SECONDS = 161
LIVE_DIGEST = "95dcfd0e8e512195e176ca231988503ab4c5f2e3059259488da4975892caeea5"
RECORDED_KINDS = {"construct_finish", "train_finish", "hero_learn", "spell_effect", "attacked", "death"}


class CombatReplayTest(unittest.TestCase):
    def test_playback_matches_the_live_game(self):
        game = launch(map=FIXTURE, agents=PLAYERS, render=False)
        self.addCleanup(game.close)
        rpc = game.rpc
        rpc.create_game(str(FIXTURE), [{"slot": p, "control": "agent"} for p in PLAYERS])
        rpc.debug("speed", factor=64)
        rpc.debug("waitfloor", ms=1)
        observations = {p: rpc.observe(p) for p in PLAYERS}
        trace, kinds = [state(observations)], {p: set() for p in PLAYERS}
        for second in range(SECONDS):
            result = rpc.step(1000)
            self.assertEqual(result["reason"], "replay_end" if second == SECONDS - 1 else "target")
            observations = {p: rpc.observe(p) for p in PLAYERS}
            for p in PLAYERS:
                kinds[p] |= {e["kind"] for e in observations[p]["events"]}
            trace.append(state(observations))
        for p in PLAYERS:
            self.assertLessEqual(RECORDED_KINDS, kinds[p], f"player {p}")
        self.assertEqual(digest(trace), LIVE_DIGEST)
        self.assertEqual(rpc.status, "ended")


if __name__ == "__main__":
    unittest.main()
