"""Replay recorded RPC movement for both players, then handle EOF without a result.

The 1.3 KiB fixture contains native replay data, no map assets. It was recorded on
the pinned build's stock Echo Isles, with two agents and no setup/debug mutations.
The reference digest covers both players at every second from 1 through 51.
"""

import hashlib
import json
import unittest
from pathlib import Path

from wc3env.game import launch
from wc3env.rpc import RpcError
from wc3env.session import GameConfig, GameSession, PlayerConfig

from .support import state


class ReplayTest(unittest.TestCase):
    def test_recorded_two_player_actions_and_eof(self):
        replay = Path(__file__).resolve().parents[1] / "fixtures" / "self_play.w3g"
        game = launch(map=replay, agents=(0, 1), render=False)
        self.addCleanup(game.close)
        rpc = game.rpc
        players = [{"slot": p, "control": "agent"} for p in (0, 1)]
        with self.assertRaisesRegex(RpcError, "requires stepping mode"):
            rpc.create_game(str(replay), players, "realtime")
        rpc.create_game(str(replay), players)
        rpc.debug("speed", factor=64)
        rpc.debug("waitfloor", ms=1)
        trace = [state({p: rpc.observe(p) for p in (0, 1)})]
        with self.assertRaisesRegex(RpcError, "replay actions"):
            rpc.act(0, [])
        for tick in range(50):
            result = rpc.step(1000)
            self.assertEqual(result["elapsed_ms"], 1000)
            self.assertEqual(result["reason"], "replay_end" if tick == 49 else "target")
            trace.append(state({p: rpc.observe(p) for p in (0, 1)}))
        digest = hashlib.sha256(json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, "362f4b20a9254551b9d0b29ed9bfdcfcebf7c3470381147294538e1d9ac09649")
        self.assertEqual(rpc.status, "ended")
        with self.assertRaises(RpcError):
            rpc.step(1000)
        with self.assertRaisesRegex(RpcError, "replay reset requires a new process"):
            rpc.reset()
        self.assertEqual(state({p: rpc.observe(p) for p in (0, 1)}), trace[-1])

    def test_session_step_stops_at_replay_end_without_inventing_results(self):
        replay = Path(__file__).resolve().parents[1] / "fixtures" / "self_play.w3g"
        with GameSession(
            GameConfig(
                map=str(replay),
                players=(PlayerConfig(0), PlayerConfig(1)),
                render=False,
                step_ms=60000,
                max_episodes_per_process=None,
            )
        ) as session:
            pids = []
            starts = []
            for _ in range(2):
                starts.append(state(session.reset()))
                pids.append(session.game.pid)
                session.game.rpc.debug("speed", factor=64)
                session.game.rpc.debug("waitfloor", ms=1)
                observations, done, info = session.step({0: [], 1: []})
                self.assertTrue(done)
                self.assertEqual((info["elapsed_ms"], info["step_reason"]), (50000, "replay_end"))
                self.assertEqual([observations[p]["result"] for p in (0, 1)], ["", ""])
            self.assertNotEqual(*pids)
            self.assertEqual(*starts)


if __name__ == "__main__":
    unittest.main()
