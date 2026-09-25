"""Macro replies become environment actions: references, groups, queueing, production and build sites."""

import unittest
from pathlib import Path

from agent_fixtures import observation, own, world
from wc3agent.game.catalog import Catalog
from wc3agent.game.featurize import describe
from wc3agent.game.orders import Orders


class OrderParsing(unittest.TestCase):
    def setUp(self):
        self.world = world()
        self.obs = observation(player=dict(gold=2000, lumber=2000, food_used=5, food_cap=100))
        self.world.update(self.obs)
        self.orders = Orders(self.world)

    def test_a_skill_learned_in_the_reply_can_be_cast_and_takes_a_target_only_if_it_has_one(self):
        catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        self.world.catalog = self.world.references.catalog = catalog
        obs = observation(units=[own(1, "htow"), own(10, "Hamg", hero=True, level=2)])
        self.world.update(obs)
        text = (
            "learn archmage1 Summon Water Elemental\nlearn archmage1 Blizzard\n"
            "cast archmage1 Summon Water Elemental at 100 200\ncast archmage1 Blizzard at 300 400"
        )
        actions, notes = Orders(self.world).parse(text, obs)
        self.assertEqual(notes, [])
        self.assertEqual(
            [a["arguments"] for a in actions if a["command"] == "cast"],
            [{"order": "waterelemental"}, {"order": "blizzard", "x": 300.0, "y": 400.0}],
        )

    def test_heroes_give_sell_and_drop_items_by_slot(self):
        catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        self.world.catalog = self.world.references.catalog = catalog
        shop = dict(unit_id=60, type_id="ngme", owner=15, x=900.0, y=0.0, hp=1, max_hp=1, mana=0, max_mana=0,
                    structure=True, hero=False, level=0)  # fmt: skip
        obs = observation(
            units=[own(1, "htow"), own(10, "Hamg", hero=True, level=2), own(11, "Hmkg", hero=True, level=1)],
            visible_enemies=[shop],
        )
        self.world.update(obs)
        text = "\n".join(
            [
                "give archmage1 slot 2 to mountainking1",
                "sell archmage1 slot 3 to goblinmerchant1",
                "drop archmage1 slot 1 at 100 200",
                "drop archmage1 slot 4",
                "give archmage1 slot 5",
                "sell archmage1 to goblinmerchant1",
            ]
        )
        actions, notes = Orders(self.world).parse(text, obs)
        self.assertEqual(
            [(a["command"], a["arguments"]) for a in actions],
            [
                ("drop_item", {"slot": 1, "target_id": 11}),
                ("drop_item", {"slot": 2, "target_id": 60}),
                ("drop_item", {"slot": 0, "x": 100.0, "y": 200.0}),
                ("drop_item", {"slot": 3}),
            ],
        )
        self.assertEqual(len(notes), 2)  # give without a target; sell without a slot
        self.assertIn("needs 'to'", notes[0])
        self.assertIn("slot 1", notes[1])

    def test_a_dead_hero_is_shown_and_revived_not_trained_again(self):
        catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        self.world.catalog = self.world.references.catalog = self.world.outcomes.catalog = catalog
        alive = observation(
            units=[
                own(1, "htow"),
                own(3, "hpea"),
                own(7, "halt", structure=True, state=None, state_seconds=0.0, queue=[], queue_seconds=0.0),
                own(10, "Hamg", hero=True, level=4),
            ]
        )
        self.world.update(alive)
        died = observation(
            game_time_seconds=31,
            units=alive["units"][:3],
            events=[dict(kind="death", unit_id=10, type_id="Hamg", owner=0)],
        )
        self.world.update(died)
        self.assertIn("archmage1 level 4 DEAD: revive altarofkings1 archmage1", describe(self.world, died))
        orders = Orders(self.world)
        actions, notes = orders.parse("train altarofkings1 Archmage", died)
        self.assertEqual(actions, [])
        self.assertIn("revive altarofkings1 archmage1", notes[0])
        actions, notes = orders.parse("revive altarofkings1 archmage1", died)
        self.assertEqual((actions, notes), ([{"unit_id": 7, "command": "revive", "arguments": {"target_id": 10}}], []))
        died["units"][2]["queue"] = ["Hamg"]  # the altar is reviving it
        self.assertIn("archmage1 level 4 DEAD: being revived at altarofkings1", describe(self.world, died))

    def test_orders_become_environment_actions(self):
        text = "Build up the economy first.\ntrain #2 Footman x2\n- lumber #4\nbuild u3 farm at 100 200\nqueue gold u3\nresearch #2 Defend"
        actions, notes = self.orders.parse(text, self.obs)
        self.assertEqual(notes, [])  # the plan sentence starts with "Build" and is not an order
        self.assertEqual([a["command"] for a in actions], ["train", "train", "harvest", "build", "harvest", "research"])
        self.assertEqual(actions[2]["arguments"], {"target_id": 70})
        self.assertNotIn("queued", actions[3]["arguments"])
        self.assertEqual(actions[4]["arguments"], {"target_id": 50, "queued": True})  # after the Farm, back to gold
        self.assertEqual([a["command"] for a in actions if a["unit_id"] == 3], ["build", "harvest"])

    def test_unusable_lines_come_back_as_notes(self):
        actions, notes = self.orders.parse(
            "train #2 Dragon\nattack #3\nmove #99 at 1 2\nI think we should wait.", self.obs
        )
        self.assertEqual(actions, [])
        self.assertEqual(len(notes), 3)

    def test_typed_references_translate_to_native_ids_and_reject_the_wrong_kind(self):
        obs = observation(game_time_seconds=500, units=[own(1, "hkee"), own(3, "hpea", hero=True), own(9, "hvlt")])
        self.world.update(obs)
        actions, notes = self.orders.parse(
            "repair u3 on b1\ntake u3 i90\nbuy u3 from b9 Potion of Healing\nattack u3 on d70", obs
        )
        self.assertEqual(notes, [])
        self.assertEqual(
            [a["arguments"].get("target_id", a["arguments"].get("shop_id")) for a in actions], [1, 90, 9, 70]
        )
        self.assertTrue(all(a["unit_id"] == 3 for a in actions))
        self.world.update(self.obs)
        actions, notes = self.orders.parse("gold b3 u4\nrepair u3 on u1\ngroup scouts b3 u4: scout", self.obs)
        self.assertEqual([a["unit_id"] for a in actions], [4])  # a wrong member does not block valid ones
        self.assertEqual(self.world.control.groups["scouts"]["ids"], {4})
        self.assertEqual(len(notes), 3)

    def test_a_cast_resolves_only_abilities_the_unit_is_seen_to_have(self):
        self.world.catalog.abilities["Atst"] = {
            "levels": {"1": {"name": "Observed spell", "orders": [{"kind": "cast", "name": "test", "order_id": 123}]}}
        }
        hall = self.obs["units"][0]
        hall["abilities"] = [{"ability_id": "Atst", "level": 1}]
        actions, notes = self.orders.parse("cast #1 Observed spell", self.obs)
        self.assertEqual(notes, [])
        self.assertEqual(actions, [{"unit_id": 1, "command": "cast", "arguments": {"order": "test"}}])
        hall["abilities"] = []
        actions, notes = self.orders.parse("cast #1 Observed spell", self.obs)
        self.assertEqual(actions, [])
        self.assertEqual(len(notes), 1)

    def test_orders_replace_current_work_unless_queued(self):
        actions, notes = self.orders.parse("build u3 Farm at 700 800\nlumber u3", self.obs)
        self.assertEqual(notes, [])
        self.assertEqual([a["command"] for a in actions], ["build", "harvest"])
        self.assertTrue(all(not a["arguments"].get("queued") for a in actions))
        actions, notes = self.orders.parse("queue move u3 at 100 200\nqueue build u3 Farm\nqueue lumber u3", self.obs)
        self.assertEqual(notes, [])
        self.assertEqual([a["command"] for a in actions], ["move", "build", "harvest"])
        self.assertTrue(all(a["arguments"]["queued"] for a in actions))
        building = observation()
        building["units"] += [own(5, "hhou", state="constructing", state_seconds=35.0, hp=30)]
        building["units"][2]["order"] = dict(name="repair", target_id=5, x=0, y=0)
        actions, _ = self.orders.parse("gold #3 #4", building)
        self.assertEqual([a["arguments"].get("queued", False) for a in actions], [False, False])

    def test_nothing_is_queued_behind_gathering_because_gathering_never_ends(self):
        actions, notes = self.orders.parse("gold u3\nqueue build u3 Farm", self.obs)
        self.assertEqual([a["command"] for a in actions], ["harvest"])
        self.assertIn("gathering never ends", notes[0])
        actions, notes = self.orders.parse("queue build u4 Farm", self.obs)
        self.assertEqual((actions, len(notes)), ([], 1))
        actions, notes = self.orders.parse("build u4 Farm\nqueue lumber u4", self.obs)
        self.assertEqual(([a["command"] for a in actions], notes), (["build", "harvest"], []))

    def test_groups_are_handed_over_until_changed_disbanded_or_ordered_directly(self):
        line = "group scouts #3 #4 at 900 100: look at the enemy base, run if attacked"
        actions, notes = self.orders.parse(line, self.obs)
        self.assertEqual(notes, [])
        self.assertEqual(
            [(a["unit_id"], a["command"], a["arguments"]) for a in actions],
            [(3, "move", {"x": 900.0, "y": 100.0}), (4, "move", {"x": 900.0, "y": 100.0})],
        )
        self.assertEqual(self.world.control.groups["scouts"]["ids"], {3, 4})
        before = dict(self.world.control.versions)
        actions, _ = self.orders.parse(line, self.obs)
        self.assertEqual(actions, [])  # unchanged: they are already on their way
        self.assertEqual(dict(self.world.control.versions), before)  # and micro's answers still stand
        self.orders.parse(line.replace("look at", "retreat from"), self.obs)
        self.assertTrue(all(self.world.control.versions[uid] > before[uid] for uid in (3, 4)))
        self.orders.parse("gold #4", self.obs)
        self.assertEqual(self.world.control.groups["scouts"]["ids"], {3})  # a direct order takes the unit back
        _, notes = self.orders.parse("disband scouts\ndisband nobody\ngroup #3: no name", self.obs)
        self.assertEqual((self.world.control.groups, len(notes)), ({}, 2))

    def test_a_group_can_attack_move_to_its_destination(self):
        actions, notes = self.orders.parse("group creeps #3 #4 attack at 900 100: clear the camp", self.obs)
        self.assertEqual(notes, [])
        self.assertEqual({(a["command"], a["arguments"]["x"]) for a in actions}, {("attack", 900.0)})
        self.assertTrue(self.world.control.groups["creeps"]["attack"])
        again, _ = self.orders.parse("group creeps #3 #4 at 900 100: clear the camp", self.obs)
        self.assertEqual({a["command"] for a in again}, {"move"})  # switching to walking re-issues the order

    def test_direct_attacks_take_units_out_of_groups_without_delegating(self):
        self.orders.parse("group combat #3 #4: Fight", self.obs)
        for line in ("attack #3 #4 on #50", "attack #3 #4 at 800 900"):
            with self.subTest(line=line):
                actions, notes = self.orders.parse(line, self.obs)
                self.assertEqual(notes, [])
                self.assertEqual([a["command"] for a in actions], ["attack", "attack"])
                self.assertEqual(self.world.control.groups, {})

    def test_a_cast_keeps_the_unit_in_its_group_and_micro_waits_for_it(self):
        self.world.catalog.abilities["Atst"] = {
            "levels": {"1": {"name": "Observed spell", "orders": [{"kind": "cast", "name": "test", "order_id": 123}]}}
        }
        self.obs["units"][2]["abilities"] = [{"ability_id": "Atst", "level": 1}]
        self.orders.parse("group combat #3 #4: Fight", self.obs)
        actions, notes = self.orders.parse("cast #3 Observed spell", self.obs)
        self.assertEqual((notes, [a["command"] for a in actions]), ([], ["cast"]))
        self.assertEqual(self.world.control.groups["combat"]["ids"], {3, 4})
        units = {u["unit_id"]: u for u in self.obs["units"]}
        control = self.world.control
        self.assertEqual(control.busy(units, 31), {3})
        units[3]["order"] = {"name": "test"}  # still channeling after the minimum wait
        self.assertEqual(control.busy(units, 40), {3})
        units[3]["order"] = None
        self.assertEqual(control.busy(units, 41), set())

    def test_a_summon_joins_its_summoners_group(self):
        self.orders.parse("group combat #3: Fight", self.obs)
        self.world.update(observation(events=[dict(kind="summon", unit_id=3, summoned_id=60, type_id="hwat")]))
        self.assertEqual(self.world.control.groups["combat"]["ids"], {3, 60})

    def test_rally_targets_resources_or_a_point_alongside_training(self):
        for order, aim in (
            ("Gold", {"target_id": 50}),
            ("lumber", {"target_id": 70}),
            ("at 100 200", {"x": 100.0, "y": 200.0}),
        ):
            with self.subTest(order=order):
                actions, notes = self.orders.parse(f"train #1 Peasant\nrally #1 {order}", self.obs)
                self.assertEqual(notes, [])
                self.assertEqual(
                    actions,
                    [
                        {"unit_id": 1, "command": "train", "arguments": {"type_id": "hpea"}},
                        {"unit_id": 1, "command": "cast", "arguments": {"order": "setrally", **aim}},
                    ],
                )

    def test_rally_requires_a_producer_and_a_visible_resource(self):
        for order in ("rally #3 gold", "rally #1 food", "rally #99 gold"):
            with self.subTest(order=order):
                actions, notes = self.orders.parse(order, self.obs)
                self.assertEqual(actions, [])
                self.assertEqual(len(notes), 1)
        for resource, field in (("gold", "visible_enemies"), ("lumber", "destructables")):
            with self.subTest(resource=resource):
                actions, notes = self.orders.parse(f"rally #1 {resource}", {**self.obs, field: []})
                self.assertEqual(actions, [])
                self.assertEqual(len(notes), 1)

    def test_production_is_sent_in_the_order_chosen_and_legality_is_left_to_the_engine(self):
        self.world.catalog.units["htow"]["researches"] = ["Rhde"]
        for text, expected in (
            ("upgrade #1 Keep\ntrain #1 Peasant\ntrain #2 Footman", [(1, "hkee"), (1, "hpea"), (2, "hfoo")]),
            ("train #1 Peasant\nupgrade #1 Keep", [(1, "hpea"), (1, "hkee")]),
            ("research #1 Defend\nupgrade #1 Keep", [(1, "Rhde"), (1, "hkee")]),
        ):
            with self.subTest(text=text):
                actions, notes = self.orders.parse(text, self.obs)
                self.assertEqual([(a["unit_id"], a["arguments"]["type_id"]) for a in actions], expected)
                self.assertEqual(notes, [])
        busy = observation(units=[own(1, "htow", state="upgrading"), own(2, "hbar", queue=["hfoo"] * 6)])
        actions, notes = self.orders.parse("train #1 Peasant\ntrain #2 Footman x2\nresearch #2 Defend", busy)
        self.assertEqual(
            [(a["unit_id"], a["command"]) for a in actions], [(1, "train"), (2, "train"), (2, "train"), (2, "research")]
        )
        self.assertEqual(notes, [])

    def test_production_has_its_own_queue_and_does_not_accept_queue_prefix(self):
        actions, notes = self.orders.parse("train b1 Peasant x2\nresearch b2 Defend", self.obs)
        self.assertEqual(notes, [])
        self.assertEqual([a["command"] for a in actions], ["train", "train", "research"])
        self.assertTrue(all("queued" not in a["arguments"] for a in actions))
        for command in ("train b1 Peasant", "upgrade b1 Keep", "research b2 Defend", "cancel b1", "rally b1 gold"):
            with self.subTest(command=command):
                actions, notes = self.orders.parse(f"queue {command}", self.obs)
                self.assertEqual(actions, [])
                self.assertEqual(len(notes), 1)

    def test_cancel_targets_the_building_and_requires_no_worker_stop(self):
        obs = observation(
            units=[own(3, "hpea"), own(5, "hhou", state="constructing")],
            player=dict(gold=2000, lumber=2000, food_used=5, food_cap=100),
        )
        self.world.update(obs)
        actions, notes = self.orders.parse("cancel b5\nbuild u3 Barracks at 700 800", obs)
        self.assertEqual(notes, [])
        self.assertEqual(actions[0], {"unit_id": 5, "command": "cast", "arguments": {"order": "cancel"}})
        self.assertEqual(actions[1]["command"], "build")
        actions, notes = self.orders.parse("cancel u3", obs)
        self.assertEqual(actions, [])
        self.assertEqual(len(notes), 1)

    def test_batch_budget_does_not_veto_the_models_build_orders(self):
        obs = {**self.obs, "player": dict(gold=160, lumber=20, food_used=5, food_cap=12)}
        actions, notes = self.orders.parse("build #3 Barracks\n\nbuild #4 Farm", obs)
        self.assertEqual([a["arguments"]["type_id"] for a in actions], ["hbar", "hhou"])
        self.assertEqual(notes, [])


class BuildSites(unittest.TestCase):
    """Macro names an anchor; the native build action searches for the exact site."""

    def test_a_build_without_a_site_is_anchored_at_the_hall_or_else_the_builder(self):
        obs = observation(units=[own(2, "hbar", x=-1800), own(3, "hpea"), own(1, "htow")])
        memory = world()
        memory.update(obs)
        orders = Orders(memory)
        for uid in range(100, 103):  # the base growing does not move the anchor
            actions, notes = orders.parse("build #3 Farm", obs)
            self.assertEqual(notes, [])
            self.assertEqual(actions[0]["arguments"], {"type_id": "hhou", "x": 0, "y": 0, "auto_place": True})
            obs["units"].insert(0, own(uid, "hhou", x=-500, y=500))
        homeless = observation(units=[own(3, "hpea", x=2000, y=3000)], visible_enemies=[], destructables=[])
        actions, notes = Orders(world()).parse("build #3 Farm", homeless)
        self.assertEqual(notes, [])
        self.assertEqual((actions[0]["arguments"]["x"], actions[0]["arguments"]["y"]), (2000, 3000))

    def test_a_site_can_be_a_point_near_an_object_or_on_a_target(self):
        obs, memory = observation(), world()
        memory.update(obs)
        orders = Orders(memory)
        mine = orders.references.name(obs["visible_enemies"][0])
        for line, arguments in (
            ("build #3 Farm at 4352 -3200", {"x": 4352, "y": -3200, "auto_place": True}),
            ("build #3 Farm near 700 800", {"x": 700.0, "y": 800.0, "auto_place": True}),
            (f"build #3 Farm near {mine}", {"x": 300.0, "y": 0.0, "auto_place": True}),
            ("build #3 Farm on #50", {"target_id": 50, "x": 300.0, "y": 0.0}),  # the engine judges the target
        ):
            with self.subTest(line=line):
                actions, notes = orders.parse(line, obs)
                self.assertEqual(notes, [])
                self.assertEqual(actions[0]["arguments"], {"type_id": "hhou", **arguments})


if __name__ == "__main__":
    unittest.main()
