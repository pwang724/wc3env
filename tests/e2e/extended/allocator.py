"""Native arena growth must not exhaust address space for small moved buffers."""

import unittest

from wc3env.protocol import Action
from wc3env.session import GameConfig, GameSession, PlayerConfig


class AllocatorTest(unittest.TestCase):
    def test_large_army_advances_and_last_unit_remains_addressable(self):
        # The unpatched allocator exhausted its 2 GiB address space near 1.7 seconds,
        # reserving a fresh 2 MiB arena for each ~1 KiB resized proximity buffer.
        for render in (False, True):
            with (
                self.subTest(render=render),
                GameSession(
                    GameConfig(
                        map="(2)EchoIsles.w3x", players=(PlayerConfig(0), PlayerConfig(1)), render=render, step_ms=250
                    )
                ) as session,
            ):
                initial = session.reset()[0]
                rpc = session.game.rpc
                rpc.debug("speed", factor=64)
                rpc.debug("waitfloor", ms=1)
                bounds = initial["map"]["bounds"]
                columns = int((bounds["max_x"] - bounds["min_x"] - 1024) // 128)
                self.assertLessEqual(((2048 + columns - 1) // columns) * 128, bounds["max_y"] - bounds["min_y"] - 1024)
                ids = []
                for offset in range(0, 2048, columns * 2):
                    ids.extend(
                        rpc.debug(
                            "spawn",
                            type_id="hgry",
                            player=0,
                            x=bounds["min_x"] + 512,
                            y=bounds["min_y"] + 512 + (offset // columns) * 128,
                            n=min(columns * 2, 2048 - offset),
                            columns=columns,
                            spacing=128,
                        )["unit_ids"]
                    )
                observed = session._observe_all()[0]
                self.assertEqual(len(observed["units"]), len(initial["units"]) + 2048)
                before = next(u for u in observed["units"] if u["unit_id"] == ids[-1])
                target = before["x"] + (500 if before["x"] + 500 < bounds["max_x"] - 256 else -500)
                for tick in range(20):
                    actions = [Action(ids[-1], "move", {"x": target, "y": before["y"]})] if tick == 0 else []
                    observations, done, info = session.step({0: actions, 1: []})
                    self.assertFalse(done)
                    self.assertEqual(info["elapsed_ms"], 250)
                    self.assertEqual(info["rejected"], {0: [], 1: []})
                after = next(u for u in observations[0]["units"] if u["unit_id"] == ids[-1])
                self.assertGreater(abs(after["x"] - before["x"]), 20)
                self.assertNotIn("critical error", session.game.log())


if __name__ == "__main__":
    unittest.main()
