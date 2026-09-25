"""Native observations: map resources, production, abilities and inventory."""

import unittest

from wc3env.game import launch


class ObservationTest(unittest.TestCase):
    def setUp(self):
        self.game = launch(agents=(0, 1), render=False)
        self.addCleanup(self.game.close)
        self.c = self.game.rpc
        self.players = [{"slot": p, "control": "agent"} for p in (0, 1)]
        self.c.create_game(str(self.game.map), self.players)

    def test_alliances_are_live_and_neutral_ids_come_from_metadata(self):
        before = self.c.observe(0)
        self.assertTrue(any(p["kind"] == "neutral" for p in before["players"]))
        self.assertEqual(next(p for p in before["players"] if p["id"] == 1)["relation"], "enemy")
        for source, target in ((0, 1), (1, 0)):
            self.c.debug("alliance", player=source, other=target, kind=0, enabled=1)
        self.c.debug("alliance", player=1, other=0, kind=5, enabled=1)
        after = self.c.observe(0)
        ally = next(p for p in after["players"] if p["id"] == 1)
        self.assertEqual(ally["relation"], "ally")
        self.assertTrue(ally["shares_vision"])

    def test_own_scores_follow_production_and_reset(self):
        before = self.c.observe(0)
        enemy_before = self.c.observe(1)["score"]
        hall = next(u for u in before["units"] if u["structure"])
        self.c.debug("speed", factor=64)
        self.assertEqual(
            self.c.act(0, [{"unit_id": hall["unit_id"], "command": "train", "arguments": {"type_id": "hpea"}}])[
                "rejected"
            ],
            [],
        )
        for _ in range(8):
            self.c.step(2500)
            if self.c.observe(0)["score"]["units_trained"] > before["score"]["units_trained"]:
                break
        after = self.c.observe(0)
        self.assertEqual(after["score"]["units_trained"], before["score"]["units_trained"] + 1)
        self.assertEqual(self.c.observe(1)["score"]["units_trained"], enemy_before["units_trained"])
        self.c.reset()
        self.c.create_game(str(self.game.map), self.players)
        self.assertEqual(self.c.observe(0)["score"], before["score"])

    def test_resource_classification_distinguishes_trees_and_debris(self):
        unit = self.c.observe(0)["units"][0]
        fixtures = {}
        for index, (kind, invulnerable) in enumerate((("LTlt", 0), ("ATtr", 1), ("LTcr", 0))):
            fixtures[kind] = self.c.debug(
                "destructable", type_id=kind, x=unit["x"] + index * 80, y=unit["y"] + 150, invulnerable=invulnerable
            )["id"]
        visible = {d["id"]: d for d in self.c.observe(0)["destructables"]}
        for kind, resource, protected in (("LTlt", "lumber", False), ("ATtr", "lumber", True), ("LTcr", None, False)):
            with self.subTest(kind=kind):
                self.assertEqual(visible[fixtures[kind]]["resource"], resource)
                self.assertEqual(visible[fixtures[kind]]["invulnerable"], protected)

    def test_own_units_report_learned_and_runtime_added_abilities_and_enemies_do_not(self):
        own = self.c.observe(0)["units"][0]
        hero = self.c.debug("spawn", type_id="Hamg", player=0, x=own["x"], y=own["y"])["unit_ids"][0]
        self.c.debug("level", unit_id=hero, level=3)
        self.c.act(0, [{"unit_id": hero, "command": "learn", "arguments": {"ability_id": "AHwe"}}])
        self.c.step(250)
        unit = next(u for u in self.c.observe(0)["units"] if u["unit_id"] == hero)
        skill = next(a for a in unit["abilities"] if a["ability_id"] == "AHwe")
        self.assertEqual(skill["level"], 1)
        self.assertGreater(skill["mana_cost"], 0)
        self.assertGreater(skill["cooldown_seconds"], 0)
        self.assertGreaterEqual(skill["cooldown_remaining"], 0)
        self.assertNotIn("AHbz", {a["ability_id"] for a in unit["abilities"]})  # not learned
        for u in self.c.observe(1)["visible_enemies"]:
            self.assertNotIn("abilities", u)
        for u in self.c.observe(0)["units"]:
            self.assertIn("abilities", u)
        # The starting hall's Call to Arms is added at runtime, not listed in its type data.
        hall = next(u for u in self.c.observe(0)["units"] if u["type_id"] == "htow")
        call_to_arms = next(a for a in hall["abilities"] if a["ability_id"] == "Amic")
        self.assertGreater(call_to_arms["level"], 0)
        self.assertGreaterEqual(call_to_arms["cooldown_remaining"], 0)

    def test_a_consumed_tome_leaves_the_ground_list_and_an_untouched_potion_stays(self):
        own = self.c.observe(0)["units"][0]
        x, y = own["x"] - 900, own["y"] - 700
        hero = self.c.debug("spawn", type_id="Hamg", player=0, x=x, y=y)["unit_ids"][0]
        self.c.debug("item", type_id="tint", x=x + 200, y=y)  # a tome is used up the moment it is picked up
        self.c.debug("item", type_id="phea", x=x - 400, y=y)
        self.c.step(250)
        before = {i["type_id"]: i["item_id"] for i in self.c.observe(0)["items"]}
        self.assertEqual(set(before), {"tint", "phea"})
        self.c.act(0, [{"unit_id": hero, "command": "smart", "arguments": {"target_id": before["tint"]}}])
        picked = []
        for _ in range(12):
            self.c.step(250)
            obs = self.c.observe(0)
            picked += [e for e in obs["events"] if e["kind"] == "item_pickup"]
            if picked:
                break
        self.assertEqual([e["item_id"] for e in picked], [before["tint"]])
        # Consumption updates the item's own life immediately, even while its object lingers.
        self.assertEqual({i["type_id"] for i in obs["items"]}, {"phea"})
        stale = {"unit_id": hero, "command": "smart", "arguments": {"target_id": before["tint"]}}
        self.assertEqual(self.c.act(0, [stale])["rejected"], [{"index": 0, "reason": "bad_arguments"}])

    def test_ground_items_do_not_depend_on_pickup_history_or_the_previous_game(self):
        for episode in range(2):
            with self.subTest(episode=episode):
                if episode:
                    self.c.reset()
                    self.c.create_game(str(self.game.map), [{"slot": p, "control": "agent"} for p in (0, 1)])
                own = self.c.observe(0)["units"][0]
                x, y = own["x"] - 900, own["y"] - 700
                hero = self.c.debug("spawn", type_id="Hamg", player=0, x=x, y=y)["unit_ids"][0]
                untouched = {
                    self.c.debug("item", type_id=kind, x=x + dx, y=y)["item_id"]
                    for kind, dx in (("tint", 200), ("phea", -200))
                }
                self.c.step(250)
                self.c.debug("give", unit_id=hero, type_id="phea")
                # Many pickups without observations: ground state must not depend on event history.
                for _ in range(80):
                    self.c.debug("give", unit_id=hero, type_id="tint")
                obs = self.c.observe(0)
                self.assertEqual({i["item_id"] for i in obs["items"]}, untouched)
                self.assertEqual([i["type_id"] for i in obs["inventory"] if i["unit_id"] == hero], ["phea"])
                self.assertEqual(sum(e["kind"] == "item_pickup" for e in obs["events"]), 81)
                self.assertEqual(self.c.observe(0)["items"], obs["items"])
