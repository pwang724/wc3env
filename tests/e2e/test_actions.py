"""Orders the engine executes beyond a plain move: Shift-queued waypoints and special casts."""

import unittest

from wc3env.protocol import Action
from wc3env.session import GameConfig, GameSession, PlayerConfig


class ActionExtensionsTest(unittest.TestCase):
    def setUp(self):
        self.session = GameSession(GameConfig(players=(PlayerConfig(0), PlayerConfig(1)), render=False, step_ms=250))
        self.addCleanup(self.session.close)
        self.obs = self.session.reset()
        self.c = self.session.game.rpc
        self.c.debug("speed", factor=64)
        self.home = self.obs[0]["units"][0]

    def spawn(self, kind, player=0, dx=-500, dy=-500):
        home = self.obs[player]["units"][0]
        return self.c.debug("spawn", type_id=kind, player=player, x=home["x"] + dx, y=home["y"] + dy)["unit_ids"][0]

    def cast(self, uid, order, **args):
        self.assertEqual(self.c.act(0, [Action(uid, "cast", {"order": order, **args}).to_dict()])["rejected"], [])

    def test_shifted_moves_visit_both_waypoints_then_stop_clears_the_queue(self):
        uid = self.spawn("hgry")
        self.session._observe_all()
        before = next(u for u in self.session.observations[0]["units"] if u["unit_id"] == uid)
        x, y = before["x"] + 600, before["y"]
        commands = [Action(uid, "move", {"x": x, "y": y}), Action(uid, "move", {"x": x, "y": y + 600, "queued": True})]
        turned = False
        for tick in range(32):
            obs, _, info = self.session.step({0: commands if tick == 0 else [], 1: []})
            self.assertEqual(info["rejected"], {0: [], 1: []})
            unit = next(u for u in obs[0]["units"] if u["unit_id"] == uid)
            if unit["y"] > y + 50 and not turned:
                self.assertLess(abs(unit["x"] - x), 100)
                turned = True
        self.assertTrue(turned)
        self.assertLess(abs(unit["x"] - x) + abs(unit["y"] - (y + 600)), 100)
        self.c.act(0, [a.to_dict() for a in commands])
        self.c.act(0, [Action(uid, "stop", {}).to_dict()])
        stopped = unit
        for _ in range(8):
            obs, _, _ = self.session.step({0: [], 1: []})
        unit = next(u for u in obs[0]["units"] if u["unit_id"] == uid)
        self.assertLess(abs(unit["x"] - stopped["x"]) + abs(unit["y"] - stopped["y"]), 20)

    def test_dreadlord_inferno_consumes_mana_and_summons_an_infernal(self):
        hero = self.spawn("Udre")
        self.c.debug("level", unit_id=hero, level=6)
        self.assertEqual(self.c.act(0, [Action(hero, "learn", {"ability_id": "AUin"}).to_dict()])["rejected"], [])
        self.c.step(250)
        self.c.debug("mana", unit_id=hero, value=1000)
        before = next(u for u in self.c.observe(0)["units"] if u["unit_id"] == hero)
        self.cast(hero, "dreadlordinferno", x=before["x"] + 200, y=before["y"])
        events = []
        for _ in range(20):
            self.c.step(250)
            obs = self.c.observe(0)
            events.extend(obs["events"])
        after = next(u for u in obs["units"] if u["unit_id"] == hero)
        self.assertLess(after["mana"], before["mana"])
        self.assertTrue(any(e["kind"] == "spell_effect" and e.get("ability_id") == "AUin" for e in events))
        self.assertIn("ninf", {u["type_id"] for u in obs["units"]})

    def test_sacrifice_converts_an_acolyte_into_a_shade(self):
        pit = self.spawn("usap", dx=-600, dy=-800)
        acolyte = self.spawn("uaco", dx=-350, dy=-800)
        self.cast(acolyte, "requestsacrifice", target_id=pit)
        for _ in range(60):
            self.c.step(500)
            obs = self.c.observe(0)
            if any(u["type_id"] == "ushd" for u in obs["units"]):
                break
        self.assertIn("ushd", {u["type_id"] for u in obs["units"]})
        self.assertNotIn(acolyte, {u["unit_id"] for u in obs["units"]})
