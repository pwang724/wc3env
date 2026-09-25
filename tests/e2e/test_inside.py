"""Own units inside a mine or building leave `units` for `inside`, and come back out."""

import unittest

from wc3env import Action, GameConfig, GameSession, MatchSetup, PlayerConfig


class InsideTest(unittest.TestCase):
    def test_wisps_fill_an_entangled_mine_and_a_builder_leaves_its_moon_well(self):
        session = GameSession(
            GameConfig(
                players=(PlayerConfig(0, "night_elf"), PlayerConfig(1, "human", control="computer")),
                setup=MatchSetup(seed=73),
                render=False,
                sound=False,
                step_ms=250,
            )
        )
        self.addCleanup(session.close)
        obs = session.reset()[0]
        for name, args in (("ai", dict(player=1, paused=1)), ("speed", dict(factor=64)), ("waitfloor", dict(ms=1))):
            session.debug(name, **args)
        session.debug("resources", player=0, gold=9999, lumber=9999)
        mine = next(u for u in obs["units"] if u["type_id"] == "egol")
        hall = next(u for u in obs["units"] if u["type_id"] == "etol")
        wisps = [u["unit_id"] for u in obs["units"] if u["type_id"] == "ewsp"]
        wisps += session.debug("spawn", type_id="ewsp", player=0, x=hall["x"], y=hall["y"] - 300, n=2)["unit_ids"]
        session.step({0: []})
        builder, gatherers, sixth = wisps[0], wisps[1:6], wisps[6]
        orders = [Action(w, "harvest", dict(target_id=mine["unit_id"])) for w in gatherers]
        orders.append(Action(builder, "build", dict(type_id="emow", x=hall["x"], y=hall["y"], auto_place=True)))
        for tick in range(400):
            views, _, info = session.step({0: orders if tick == 0 else []})
            self.assertEqual(info["rejected"][0], [])
            obs = views[0]
            if tick == 40:
                session.step({0: [Action(sixth, "harvest", dict(target_id=mine["unit_id"]))]})
            if tick == 80:
                inside = {u["unit_id"]: u["order"] for u in obs["inside"]}
                self.assertEqual({w for w in gatherers if (inside.get(w) or {}).get("target_id") == mine["unit_id"]},
                                 set(gatherers))  # fmt: skip
                self.assertEqual(inside[builder]["name"], "emow")
                self.assertNotIn(builder, {u["unit_id"] for u in obs["units"]})
                self.assertIsNone(next(u for u in obs["units"] if u["unit_id"] == sixth)["order"])  # the mine is full
            if any(e["kind"] == "construct_finish" for e in obs["events"]):
                break
        for _ in range(8):
            obs = session.step({0: []})[0][0]
        self.assertIn(builder, {u["unit_id"] for u in obs["units"]})
        self.assertNotIn(builder, {u["unit_id"] for u in obs["inside"]})


if __name__ == "__main__":
    unittest.main()
