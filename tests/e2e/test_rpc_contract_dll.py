"""The RPC conformance suite (tests/rpc_contract.py) against wc3hook.dll in a real game.

    python -m unittest tests.e2e.test_rpc_contract_dll

Tests reset one shared process between episodes. Launch and quit checks use separate processes.
"""

from __future__ import annotations

import unittest

from tests.rpc_contract import RpcContract
from wc3env.game import launch
from wc3env.rpc import RpcClient

ENEMY_BASE = (-5184.0, 2944.0)


class DllContractTest(RpcContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared_game = launch()
        cls.addClassCleanup(cls.shared_game.close)

    def setUp(self):
        self.games = []

    def tearDown(self):
        for g in self.games:
            g.close()

    def make_client(self) -> RpcClient:
        if self._testMethodName == "test_quit_is_valid_anywhere":
            g = launch(instance=1 + len(self.games))
            self.games.append(g)
            return g.rpc
        c = self.shared_game.rpc
        if c.status == "in_game":
            c.debug("speed", factor=64)
        if c.status in ("in_game", "ended"):
            c.reset()
        return c

    def start(self, mode="stepping") -> RpcClient:
        c = super().start(mode)
        c.debug("speed", factor=1 if mode == "realtime" else 64)
        c.debug("waitfloor", ms=1)
        c.debug("render", on=0)
        return c

    def end_game(self, c: RpcClient) -> None:
        """Raze the enemy base with 24 invulnerable Knights until `ended`."""
        c.debug("speed", factor=64)
        c.debug("waitfloor", ms=1)
        c.debug("invulnerable", player=0, on=1)
        c.debug("spawn", type_id="hkni", player=0, x=ENEMY_BASE[0] + 984, y=ENEMY_BASE[1] - 44, n=24)
        for _ in range(120):
            c.step(2500)
            if c.status == "ended":
                return
            o = c.observe(0)
            targets = [u for u in o["visible_enemies"] if u["owner"] == 1]
            structures = [u for u in targets if u["structure"]] or targets
            if structures:
                t = min(structures, key=lambda u: (u["x"] - ENEMY_BASE[0]) ** 2 + (u["y"] - ENEMY_BASE[1]) ** 2)
                x, y = t["x"], t["y"]
            else:
                x, y = ENEMY_BASE
            knights = [u for u in o["units"] if u["type_id"] == "hkni"]
            if knights:
                c.act(
                    0, [{"unit_id": u["unit_id"], "command": "attack", "arguments": {"x": x, "y": y}} for u in knights]
                )
        self.fail("the game did not end")

    # ---- beyond the shared contract: what only the real game can show ------------------------
    def test_unsupported_player_actions_are_rejected(self):
        c = self.start()
        enemy = c.observe(1)["units"][0]
        error = self.assertFault("bad_params", c.act, 1, [{"unit_id": enemy["unit_id"], "command": "stop"}])
        self.assertIn("not configured as an agent", error.detail)

    def test_events_keep_values_after_units_change_or_disappear(self):
        c = self.start()
        hall = next(u for u in c.observe(0)["units"] if u["structure"])
        hero = c.debug("spawn", type_id="Hamg", player=0, x=hall["x"] + 300, y=hall["y"] - 500)["unit_ids"][0]
        footman = c.debug("spawn", type_id="hfoo", player=0, x=hall["x"] - 300, y=hall["y"] - 500)["unit_ids"][0]
        c.observe(0)
        c.debug("level", unit_id=hero, level=2)
        c.debug("level", unit_id=hero, level=3)
        levels = [e for e in c.observe(0)["events"] if e["kind"] == "hero_level"]
        self.assertEqual([(e["unit_id"], e["level"]) for e in levels], [(hero, 2), (hero, 3)])

        c.debug("level", unit_id=hero, level=4)
        c.debug("give", unit_id=hero, type_id="phea")
        c.debug("kill", unit_id=footman)
        c.debug("remove", unit_id=footman)
        c.act(0, [{"unit_id": hero, "command": "learn", "arguments": {"ability_id": "AHwe"}}])
        c.step(250)
        c.act(0, [{"unit_id": hero, "command": "cast", "arguments": {"order": "waterelemental"}}])
        c.step(500)
        c.debug("hp", unit_id=hero, value=100)
        c.act(0, [{"unit_id": hero, "command": "use_item", "arguments": {"slot": 0}}])
        c.step(250)
        c.debug("remove", unit_id=hero)
        events = c.observe(0)["events"]
        self.assertIn({"kind": "hero_level", "unit_id": hero, "level": 4}, events)
        self.assertIn({"kind": "death", "unit_id": footman, "type_id": "hfoo", "owner": 0}, events)
        pickup = next(e for e in events if e["kind"] == "item_pickup" and e["unit_id"] == hero)
        self.assertEqual(pickup["type_id"], "phea")
        self.assertGreater(pickup["item_id"], 0)
        self.assertIn({"kind": "hero_learn", "unit_id": hero, "ability_id": "AHwe"}, events)
        self.assertIn({"kind": "spell_effect", "unit_id": hero, "ability_id": "AHwe"}, events)
        self.assertIn({"kind": "item_use", "unit_id": hero, "type_id": "phea"}, events)
        summons = [e for e in events if e["kind"] == "summon"]
        self.assertTrue(summons, events)
        summon = next((e for e in summons if e["unit_id"] == hero), None)
        self.assertIsNotNone(summon, summons)
        self.assertEqual(summon["type_id"], "hwat")
        self.assertGreater(summon["summoned_id"], 0)

    def test_events_are_per_player_whatever_the_observe_order(self):
        """Each player gets the events it could see when they fired, since its own previous
        observation: another player's observe neither consumes them nor lends its fog."""
        c = self.start()
        hall = next(u for u in c.observe(0)["units"] if u["structure"])
        hx, hy = hall["x"], hall["y"]
        own = c.debug("spawn", type_id="hfoo", player=0, x=hx + 300, y=hy - 500)["unit_ids"][0]
        near = c.debug("spawn", type_id="ogru", player=1, x=hx - 300, y=hy - 500)["unit_ids"][0]  # in player 0's sight
        far = c.debug("spawn", type_id="ogru", player=1, x=ENEMY_BASE[0] + 400, y=ENEMY_BASE[1] - 400)["unit_ids"][0]
        c.step(250)
        c.observe(0)
        c.observe(1)
        c.debug("kill", unit_id=near)
        c.debug("kill", unit_id=far)
        c.step(250)
        c.debug("kill", unit_id=own)
        c.step(250)  # after `near` is gone: no orc eyes on the footman

        def deaths(o):
            return {e["unit_id"] for e in o["events"] if e["kind"] == "death"}

        second, first = c.observe(1), c.observe(0)  # player 1 asks first
        self.assertEqual(deaths(first), {own, near})
        self.assertEqual(deaths(second), {near, far})
        self.assertEqual(deaths(c.observe(0)), set())  # given once


if __name__ == "__main__":
    unittest.main()
