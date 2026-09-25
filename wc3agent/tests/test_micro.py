"""Which units micro controls, which choices each one gets, and what its request shows."""

import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fixtures import action, answer, catalog, control, fight, fighting_catalog, own, state
from wc3agent.game.abilities import describe_abilities
from wc3agent.game.catalog import Catalog
from wc3agent.game.maneuvers import maneuvers, pickups
from wc3agent.game.policies import creep_escapes, low_hero, spell_targets, stragglers, summons_unit
from wc3agent.micro.agent import MicroAgent
from wc3agent.micro.candidates import unit_candidates
from wc3agent.micro.memory import MicroMemory
from wc3agent.micro.names import Names
from wc3agent.micro.request import build_request, map_response


def micro_agent(test, game_catalog=None):
    micro = MicroAgent(game_catalog or fighting_catalog(), model="jev", key="fake")
    test.addCleanup(micro.close)
    return micro


class Choices(unittest.TestCase):
    def setUp(self):
        self.catalog = fighting_catalog()

    def options(self, obs, index=0):
        return unit_candidates(obs["units"][index], obs, {"abilities": []}, self.catalog)

    def test_health_does_not_restrict_a_fighters_tactics(self):
        obs = fight()
        healthy = self.options(obs, index=1)
        obs["units"][1]["hp"] = 20
        hurt = self.options(obs, index=1)
        self.assertTrue({"attack_50", "close_in", "back_off", "behind_line"} <= set(healthy))
        self.assertEqual(healthy, hurt)

    def test_an_attack_move_near_enemies_must_pick_a_target_and_targets_carry_facts(self):
        obs = fight()
        obs["units"][1]["order"] = {"name": "attack", "x": 900, "y": 0}  # an attack-move, no target
        options = self.options(obs, index=1)
        self.assertNotIn("keep", options)
        self.assertRegex(
            options["attack_50"]["meaning"], r"hp, (melee|ranged) \(attack range \d+\), \d+ away, 0 of ours"
        )
        obs["visible_enemies"][0]["x"] = 5000  # nothing in reach: walking on is a fine choice
        self.assertIn("keep", self.options(obs, index=1))

    def test_only_the_toggle_that_changes_something_is_offered_once_its_state_is_known(self):
        reference = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        obs = fight()
        footman = obs["units"][1]

        def ability(raw):
            data = reference.abilities[raw]["levels"]["1"]
            return dict(ability_id=raw, name=data["name"], orders=data["orders"], ready=True, missing_requirements=[],
                        targets=data.get("targets", []), level=1)  # fmt: skip

        cap = {"abilities": [ability("Adef"), ability("Ahea")]}
        memory = MicroMemory(reference)
        keys = set(unit_candidates(footman, obs, cap, reference, toggles=memory.toggles.get(11, {})))
        self.assertTrue({"defend_Adef", "healon_Ahea", "healoff_Ahea"} <= keys)  # state unknown: both autocast ways
        self.assertNotIn("undefend_Adef", keys)
        memory.record(30.0, [
            {"unit_id": 11, "command": "cast", "arguments": {"order": "defend"}},
            {"unit_id": 11, "command": "cast", "arguments": {"order": "healon"}},
        ])  # fmt: skip
        self.assertEqual(memory.toggles[11], {"defend": {"active": True}, "heal": {"autocast": True}})
        keys = set(unit_candidates(footman, obs, cap, reference, toggles=memory.toggles[11]))
        self.assertTrue({"undefend_Adef", "healoff_Ahea"} <= keys)
        self.assertFalse({"defend_Adef", "healon_Ahea"} & keys)

    def test_a_summon_is_offered_only_with_an_enemy_near(self):
        reference = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        self.assertEqual(
            {raw: summons_unit(reference, raw) for raw in ("AHwe", "AOsf", "AEfn", "AOsw", "AHbz")},
            {"AHwe": True, "AOsf": True, "AEfn": True, "AOsw": False, "AHbz": False},  # wards and spells are not
        )
        data = reference.abilities["AHwe"]["levels"]["1"]
        cap = {"abilities": [dict(ability_id="AHwe", name=data["name"], orders=data["orders"], ready=True,
                                  missing_requirements=[], targets=data.get("targets", []), level=1)]}  # fmt: skip
        obs = fight()  # a Grunt 300 away
        self.assertIn("waterelemental_AHwe", unit_candidates(obs["units"][0], obs, cap, reference))
        obs["visible_enemies"] = []  # walking to the camp: the elemental would run out before the fight
        self.assertNotIn("waterelemental_AHwe", unit_candidates(obs["units"][0], obs, cap, reference))

    def test_only_enemies_within_a_short_step_are_attack_options_and_fight_here_is_offered(self):
        obs = fight()
        obs["visible_enemies"].append({**obs["visible_enemies"][0], "unit_id": 51, "x": 1500.0})  # far behind the grunt
        options = self.options(obs, index=1)  # a footman at x=200: range 90 plus a 450 step reaches x=740
        self.assertIn("attack_50", options)
        self.assertNotIn("attack_51", options)
        self.assertEqual(
            options["fight_here"]["action"], {"unit_id": 11, "command": "attack", "arguments": {"x": 300.0, "y": 0.0}}
        )

    def test_a_back_line_unit_steps_back_only_while_an_enemy_melee_unit_is_on_it(self):
        obs = fight()  # the archmage (a caster hero) at x=0, a grunt (melee) at x=300
        self.assertFalse({"back_off", "behind_line"} & set(self.options(obs)))
        obs["visible_enemies"][0]["x"] = 200.0  # the grunt is on it
        self.assertIn("back_off", self.options(obs))

    def test_attacking_the_current_target_is_keep_not_a_second_option(self):
        obs = fight()
        obs["units"][1]["order"] = {"name": "attack", "target_id": 50, "x": 0, "y": 0}
        options = self.options(obs, index=1)
        self.assertIn("keep", options)
        self.assertNotIn("attack_50", options)

    def test_portal_is_available_at_any_health_but_requires_a_destination(self):
        healthy, hurt = self.options(fight()), self.options(fight(hero_hp=120))
        self.assertIn("item_slot_0_home", healthy)
        self.assertEqual(hurt["item_slot_0_home"]["action"]["arguments"], {"slot": 0, "target_id": 1})
        obs = fight()
        obs["home"] = {}
        self.assertNotIn("item_slot_0_home", self.options(obs))
        obs = fight(hero_hp=120)  # neutral creeps only: walk away and come back instead
        obs["visible_enemies"] = [{**e, "owner": 12} for e in obs["visible_enemies"]]
        self.assertNotIn("item_slot_0_home", self.options(obs))

    def test_retreat_moves_point_away_from_the_local_fight(self):
        obs = fight()
        moves = maneuvers(obs["units"][0], obs["units"], obs["visible_enemies"], self.catalog)
        self.assertLess(moves["back_off"]["x"], 0)  # the grunt is at x=300: away is west
        self.assertLess(moves["behind_line"]["x"], 200)  # our footmen stand at x=200, the enemy beyond them
        self.assertNotIn("behind_line", self.options(obs))  # the grunt (range 90) cannot reach the archmage 300 away
        obs["units"][0]["x"] = 150  # now it can
        before = self.options(obs)["behind_line"]
        obs["units"].append(own(30, "hfoo", x=20000))
        self.assertEqual(self.options(obs)["behind_line"], before)  # a distant ally does not move the line

    def test_a_hero_is_offered_loot_within_reach_and_tomes_even_with_full_pockets(self):
        obs = fight()
        obs["items"] = [
            dict(item_id=90, type_id="tint", x=100.0, y=0.0),
            dict(item_id=91, type_id="stwp", x=5000.0, y=0.0),
        ]
        obs["inventory"] = [dict(unit_id=10, slot=i, type_id="stwp", charges=1) for i in range(6)]
        self.assertEqual([p["item_id"] for p in pickups(obs["units"][0], obs, self.catalog)], [90])
        self.assertEqual(pickups(obs["units"][1], obs, self.catalog), [])  # only heroes carry items
        self.assertEqual(
            self.options(obs)["pickup_90"]["action"],
            {"unit_id": 10, "command": "smart", "arguments": {"target_id": 90}},
        )

    def test_a_group_destination_is_offered_only_out_of_contact_and_never_a_rejoin(self):
        obs = fight()
        obs["visible_enemies"] = []
        obs["army_groups"] = {
            "retreat": {
                "ids": {10},
                "instruction": "Disengage",
                "center": {"x": 1500, "y": 0},
                "at": {"x": 4000, "y": 0},
            }
        }
        options = self.options(obs)
        self.assertEqual(options["objective_retreat"]["action"]["command"], "move")
        self.assertFalse(any(key.startswith("regroup") for key in options))  # no "Rejoin", ever
        obs["army_groups"]["retreat"]["attack"] = True  # macro chose `attack at X Y`
        self.assertEqual(self.options(obs)["objective_retreat"]["action"]["command"], "attack")
        obs = fight()  # a grunt within reach: walking off to the destination only loses damage
        obs["army_groups"] = {
            "retreat": {"ids": {10}, "instruction": "Disengage", "center": {"x": 0, "y": 0}, "at": {"x": 4000, "y": 0}}
        }
        self.assertNotIn("objective_retreat", self.options(obs))

    def test_destination_choices_disappear_once_the_unit_has_arrived_or_is_heading_there(self):
        obs = fight()
        obs["visible_enemies"] = []
        obs["units"].append(own(1, "htow"))
        obs["home"] = {"unit_id": 1, "x": 0, "y": 0}
        obs["army_groups"] = {
            "defense": {"ids": {10}, "instruction": "Defend", "center": {"x": 0, "y": 0}, "at": {"x": 0, "y": 0}}
        }
        navigation = {"objective_defense", "recover_home"}
        obs["units"][0].update(x=600, y=40)
        self.assertTrue(navigation <= self.options(obs).keys())
        obs["units"][0].update(x=250)  # a 512-wide hall stops units about here
        self.assertFalse(navigation & self.options(obs).keys())
        obs["units"][0].update(x=600, order={"name": "move", "x": 0, "y": 0})
        self.assertIn("keep", self.options(obs))
        self.assertFalse(navigation & self.options(obs).keys())

    def test_moves_that_went_nowhere_are_withheld_until_the_unit_moves(self):
        memory = MicroMemory(self.catalog)
        obs = fight()
        memory.ingest(obs)
        hero = obs["units"][0]
        memory.record(obs["game_time_seconds"], [action("move", x=900.0, y=0.0) | {"unit_id": hero["unit_id"]}])
        later = fight()
        later["game_time_seconds"] = obs["game_time_seconds"] + 1
        memory.ingest(later)  # still idle where it was told to leave from
        self.assertEqual(memory.dead_ends[hero["unit_id"]]["targets"], [(900.0, 0.0)])
        options = unit_candidates(later["units"][0], later, {}, self.catalog, [(900.0, 0.0)])
        self.assertFalse(any(o["action"] and o["action"]["arguments"].get("x") == 900.0 for o in options.values()))
        moved = fight()
        moved["game_time_seconds"] = later["game_time_seconds"] + 1
        moved["units"][0].update(x=hero["x"] + 300)
        memory.ingest(moved)
        self.assertNotIn(hero["unit_id"], memory.dead_ends)

    def test_worker_toggles_and_economic_spells_are_never_micro_options(self):
        reference = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")

        def live(aid):
            return dict(ability_id=aid, level=1, mana_cost=0, cooldown_seconds=0, cooldown_remaining=0)

        for raw in ("hpea", "hmil"):
            with self.subTest(raw=raw):
                obs = state()
                obs["units"] = [
                    own(1, "htow", abilities=[live("Amic")]),
                    own(3, raw, x=100, abilities=[live("Ahar"), live("Amil")]),
                ]
                obs["visible_enemies"].append(own(90, "ogru", owner=1, x=200))
                caps = describe_abilities(reference, obs, {})
                options = unit_candidates(obs["units"][1], obs, caps["3"], reference)
                self.assertFalse({"militia", "militiaoff"} & options.keys())
                self.assertFalse(
                    any(
                        v["action"]
                        and (
                            v["action"]["command"] == "harvest"
                            or v["action"]["arguments"].get("order") in ("militia", "militiaoff", "harvest")
                        )
                        for v in options.values()
                    )
                )


class Delegation(unittest.TestCase):
    def test_group_members_get_micro_choices_and_proposals_are_not_submission_history(self):
        micro = micro_agent(self)
        groups = {"main": {"ids": {10, 11, 12}, "instruction": "fight"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Attack")):
            actions, records = micro.act(fight(), control(groups), None, {})
        self.assertEqual({a["unit_id"] for a in actions}, {10, 11, 12})
        self.assertIn(10, {u["unit_id"] for u in micro.memory.previous["units"]})
        self.assertEqual(micro.memory.last_issued, {})  # proposals have not been submitted
        self.assertEqual([a for r in records for a in r["proposed_actions"]], actions)

    def test_only_explicit_members_are_controlled_but_nearby_allies_are_context(self):
        micro = micro_agent(self)
        groups = {"main": {"ids": {10, 11}, "instruction": "fight"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Attack")) as call:
            actions, _ = micro.act(fight(), control(groups), None, {})
        self.assertEqual({a["unit_id"] for a in actions}, {10, 11})
        hero_request = next(c.args[0] for c in call.call_args_list if "archmage1" in c.args[0]["questions"])
        self.assertIn("footman2", hero_request["state"]["your_side"]["Footman"]["members"])
        self.assertNotIn("footman2", hero_request["questions"])

    def test_ungrouped_units_never_get_default_orders_even_when_idle_hurt_or_threatened(self):
        micro = micro_agent(self)
        obs = fight(hero_hp=20)
        obs["units"].append(own(30, "hpea"))
        obs["items"] = [dict(item_id=90, type_id="tint", x=50, y=0)]
        with patch("wc3agent.micro.agent.timed_call") as call:
            self.assertEqual(micro.act(obs, control({}), None, {}), ([], []))
            call.assert_not_called()

    def test_a_delegated_worker_leaves_its_job_and_gets_no_economic_choices(self):
        obs = state()
        obs["units"][-1]["order"] = {"name": "repair", "target_id": 20}
        groups = {"scout": {"ids": {3}, "instruction": "Find the enemy without engaging", "at": {"x": 3000, "y": 0}}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Move to scout")) as call:
            actions, records = micro_agent(self).act(obs, control(groups), 1, {})
        self.assertEqual(actions, [action("move", x=3000, y=0)])
        self.assertEqual(records[0]["dropped_actions"], [])
        request = call.call_args.args[0]
        self.assertNotIn("economy", request["state"])
        choices = next(iter(request["questions"].values()))["criteria"]
        self.assertFalse(any("Gather" in choice or "Call to Arms" in choice for choice in choices))

    def test_a_hero_collects_a_drop_at_once_even_mid_fight(self):
        obs = fight(hero_hp=20)
        obs["items"] = [dict(item_id=90, type_id="tint", x=50, y=0)]
        groups = {"survive": {"ids": {10}, "instruction": "Stay alive and retreat"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Back off")) as call:
            actions, _ = micro_agent(self).act(obs, control(groups), None, {})
        self.assertIn({"unit_id": 10, "command": "smart", "arguments": {"target_id": 90}}, actions)
        self.assertFalse(any("archmage1" in c.args[0]["questions"] for c in call.call_args_list))

        obs = fight()
        obs["visible_enemies"] = []
        obs["items"] = [dict(item_id=90, type_id="tint", x=50, y=0)]
        groups = {"loot": {"ids": {10}, "instruction": "Collect the nearby items"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Pick up")):
            actions, _ = micro_agent(self).act(obs, control(groups), None, {})
        self.assertEqual(actions, [{"unit_id": 10, "command": "smart", "arguments": {"target_id": 90}}])

    def test_an_item_use_event_without_an_item_type_is_still_described(self):
        obs = fight()
        obs["events"] = [dict(kind="item_use", unit_id=10, type_id="")]  # a consumed item can vanish first
        groups = {"army": {"ids": {10}, "instruction": "Fight"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Back off")) as call:
            micro_agent(self).act(obs, control(groups), None, {})
        timeline = call.call_args.args[0]["state"]
        self.assertIn("used an item", str(timeline))

    def test_a_hero_with_no_enemy_near_collects_loot_whatever_its_objective(self):
        obs = fight()
        obs["visible_enemies"] = []
        obs["items"] = [dict(item_id=90, type_id="tint", x=50, y=0), dict(item_id=91, type_id="stwp", x=400, y=0)]
        groups = {"home": {"ids": {10}, "instruction": "Return to base and recover", "at": {"x": -4000, "y": 0}}}
        micro = micro_agent(self)
        with patch("wc3agent.micro.agent.timed_call") as call:
            actions, _ = micro.act(obs, control(groups), None, {})
            self.assertEqual(actions, [{"unit_id": 10, "command": "smart", "arguments": {"target_id": 90}}])
            obs["game_time_seconds"] += 1.0  # still walking there: not re-sent yet, and Jev is not asked
            self.assertEqual(micro.act(obs, control(groups), None, {})[0], [])
            call.assert_not_called()
        obs["items"] = obs["items"][1:]  # the tome is taken; the scroll is next while pockets have room
        obs["game_time_seconds"] += 1.0
        with patch("wc3agent.micro.agent.timed_call"):
            actions, _ = micro.act(obs, control(groups), None, {})
        self.assertEqual(actions, [{"unit_id": 10, "command": "smart", "arguments": {"target_id": 91}}])

    def test_idle_assigned_units_are_asked_under_their_own_groups_objective(self):
        obs = fight()
        obs["visible_enemies"] = []
        groups = {"scout": {"ids": {11}, "instruction": "Scout without engaging", "at": {"x": 4000, "y": 0}}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Move to scout")) as call:
            actions, _ = micro_agent(self).act(obs, control(groups), None, {})
        self.assertEqual(actions, [{"unit_id": 11, "command": "move", "arguments": {"x": 4000, "y": 0}}])
        self.assertEqual(call.call_args.args[0]["state"]["objective"], "Scout without engaging")

        obs = fight(hero_hp=150)
        obs["visible_enemies"] = []
        for unit in obs["units"][1:]:
            unit["order"] = {"name": "move", "x": 3000, "y": 0}
        groups = {
            "recover": {"ids": {10}, "instruction": "Wait safely while healing"},
            "army": {"ids": {11, 12}, "instruction": "Attack", "at": {"x": 4000, "y": 0}},
        }
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Wait for now")) as call:
            actions, _ = micro_agent(self).act(obs, control(groups), None, {})
        self.assertEqual(actions, [])
        request = next(c.args[0] for c in call.call_args_list if "archmage1" in c.args[0]["questions"])
        self.assertEqual(request["state"]["objective"], "Wait safely while healing")
        self.assertFalse(any("Reinforce army" in label for label in request["questions"]["archmage1"]["criteria"]))

    def test_a_group_walking_to_or_waiting_at_its_destination_with_nothing_around_is_not_asked(self):
        micro = micro_agent(self)
        obs = fight()
        obs["visible_enemies"] = []
        obs["units"][1]["order"] = {"name": "move", "x": 3000, "y": 0}
        obs["units"][2].update(x=3050, y=0)
        groups = {"army": {"ids": {11, 12}, "instruction": "Hold there", "at": {"x": 3000, "y": 0}}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Wait for now")) as call:
            micro.act(obs, control(groups), None, {})
        self.assertEqual(call.call_count, 0)
        obs["units"][2]["order"] = {"name": "move", "x": 0, "y": 900}  # walking elsewhere: the model decides
        obs["game_time_seconds"] += 2
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Wait for now")) as call:
            micro.act(obs, control(groups), None, {})
        self.assertEqual(call.call_count, 1)


class Request(unittest.TestCase):
    def setUp(self):
        self.catalog, self.obs, self.memory = fighting_catalog(), fight(), MicroMemory()
        self.memory.ingest(self.obs)

    def request(self, ids, instruction):
        return build_request(
            self.obs,
            ids,
            instruction,
            memory=self.memory,
            names=Names(self.catalog),
            capabilities={},
            catalog=self.catalog,
            model="jev",
        )

    def test_the_request_carries_the_instruction_and_one_question_per_controlled_unit(self):
        request, menu = self.request({11, 12}, "Kill the Grunt.")
        self.assertEqual(request["state"]["objective"], "Kill the Grunt.")
        self.assertEqual(len(request["questions"]), 2)
        answers = {
            menu.question_names[uid]: {"choice": next(label for label, key in labels.items() if key == "attack_50")}
            for uid, labels in menu.choice_keys.items()
        }
        _, selected = map_response(answers, menu)
        self.assertEqual({a["action"]["command"] for a in selected}, {"attack"})

    def test_allies_targets_damage_and_recent_pressure_are_individually_visible(self):
        self.obs["units"][1]["order"] = {"name": "attack", "target_id": 50}
        self.obs["game_time_seconds"] += 2
        self.obs["units"][1]["hp"] = 390.5
        self.obs["events"] = [{"kind": "attacked", "unit_id": 11, "attacker_id": 50}]
        self.memory.ingest(self.obs)
        state = self.request({11}, "Fight together")[0]["state"]
        footman = state["you_control"]["footman1"]
        self.assertEqual(footman["current_order"], "attacking grunt1")
        self.assertIn("lost 29.5 and gained 0 in the last 2s", footman["hp"])
        self.assertEqual(footman["recent_attackers"]["grunt1"]["seconds_ago"], 0)
        self.assertIn("archmage1", state["your_side"]["Archmage"]["members"])
        self.assertEqual(state["enemies"]["Grunt"]["base_stats"]["damage_per_second"], 20.5)
        self.assertEqual(state["enemies"]["Grunt"]["role"], "melee")
        self.assertNotIn("current_order", state["enemies"]["Grunt"]["members"]["grunt1"])


class RecoveryShops(unittest.TestCase):
    def setUp(self):
        self.catalog = fighting_catalog()
        self.catalog.items["sreg"] = dict(
            name="Scroll of Regeneration",
            gold=100,
            lumber=0,
            stock_start_seconds=0,
            requires=[],
            description="Recover outside combat.",
            usable=True,
            perishable=True,
            target_form="none",
            abilities=[dict(ability_id="AIsl", targets=[])],
        )
        self.catalog.items["phea"] = {**catalog().items["phea"], "requires": ["hkee"]}
        self.catalog.units["hvlt"]["sells_items"] = ["sreg", "phea"]
        self.obs = fight(hero_hp=100)
        self.obs["visible_enemies"] = []
        self.obs["units"] = [self.obs["units"][0], own(20, "hvlt", x=1000)]

    def options(self, **fields):
        obs = {**self.obs, "shops": [self.obs["units"][1]], "tech": {}, **fields}
        return unit_candidates(obs["units"][0], obs, {}, self.catalog)

    def act(self, choice):
        groups = {"recover": {"ids": {10}, "instruction": "Recover at the shop"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer(choice)) as call:
            actions, _ = self.micro.act(self.obs, control(groups), None, {})
        return actions, call

    def test_shop_choices_require_proximity_inventory_gold_tech_and_stock_time(self):
        self.assertIn("visit_shop_20", self.options())
        self.assertFalse(any(key.startswith("buy_") for key in self.options()))
        self.obs["units"][0]["x"] = 900
        self.assertIn("buy_20_sreg", self.options())
        self.assertNotIn("buy_20_phea", self.options(tech={"hkee": 1}))
        self.assertNotIn("buy_20_phea", self.options(game_time_seconds=500))
        self.assertIn("buy_20_phea", self.options(game_time_seconds=500, tech={"hkee": 1}))
        self.assertFalse(any(key.startswith("buy_") for key in self.options(player={"gold": 0, "lumber": 0})))
        full = [dict(unit_id=10, slot=i, type_id="stwp", charges=1) for i in range(6)]
        self.assertFalse(any(key.startswith("buy_") for key in self.options(inventory=full)))

    def test_an_idle_hero_can_visit_buy_then_use_recovery_without_combat(self):
        self.micro = micro_agent(self, self.catalog)
        actions, _ = self.act("Visit Arcane Vault")
        self.assertEqual(actions, [{"unit_id": 10, "command": "move", "arguments": {"x": 1000, "y": 0}}])
        self.obs["game_time_seconds"] += 10
        self.obs["units"][0]["x"] = 900
        actions, _ = self.act("Buy Scroll of Regeneration")
        self.assertEqual(
            actions, [{"unit_id": 10, "command": "buy", "arguments": {"shop_id": 20, "item_type_id": "sreg"}}]
        )
        self.obs["game_time_seconds"] += 1
        self.obs["inventory"].append(dict(unit_id=10, slot=1, type_id="sreg", charges=1))
        actions, _ = self.act("Use Scroll of Regeneration")
        self.assertEqual(actions, [{"unit_id": 10, "command": "use_item", "arguments": {"slot": 1}}])

    def test_only_finished_shops_of_our_side_allies_or_neutrals_are_offered(self):
        for owner, relation, fields, offered in (
            (24, "enemy", {}, False),
            (0, "self", {"state": "constructing"}, False),
            (15, "neutral", {}, True),
            (13, "ally", {}, True),
        ):
            with self.subTest(relation=relation, **fields):
                self.obs["units"][1] = own(20, "hvlt", x=100, owner=owner, **fields)
                self.obs["players"] = [{"id": 0, "relation": "self"}, {"id": owner, "relation": relation}]
                self.micro = micro_agent(self, self.catalog)
                _, call = self.act("Wait for now")
                self.assertEqual(bool(call.call_args.args[0]["state"]["shops"]), offered)


if __name__ == "__main__":
    unittest.main()


class Stragglers(unittest.TestCase):
    """Code sends grouped units that fell behind back to their group; Jev is not asked about them."""

    def unit(self, uid, x, y=0.0, **fields):
        return own(uid, "hfoo", x=x, y=y, **{"hp": 420, "max_hp": 420, **fields})

    def test_who_is_sent_back_and_who_is_left_alone(self):
        rest = [self.unit(11, 0), self.unit(12, 100)]
        grunt = dict(unit_id=50, x=150.0, y=0.0)  # the rest of the group is fighting it
        cases = {
            "idle far behind the fight": (self.unit(20, 2000), None, [grunt], True),
            "walking to the destination while the rest fights": (
                self.unit(20, 2000, order=dict(name="attack", x=3000, y=0)),
                {"x": 3000, "y": 0},
                [grunt],
                True,
            ),
            "walking to the destination, nothing to fight": (
                self.unit(20, 2000, order=dict(name="attack", x=3000, y=0)),
                {"x": 3000, "y": 0},
                [],
                False,
            ),
            "already heading back to the group": (
                self.unit(20, 2000, order=dict(name="attack", x=50, y=0)),
                None,
                [grunt],
                False,
            ),
            "attacking something": (self.unit(20, 2000, order=dict(name="attack", target_id=51)), None, [grunt], False),
            "fighting an enemy of its own": (
                self.unit(20, 2000),
                None,
                [grunt, dict(unit_id=51, x=2100.0, y=0.0)],
                False,
            ),
            "badly hurt": (self.unit(20, 2000, hp=100), None, [grunt], False),
            "a hero": (self.unit(20, 2000, hero=True), None, [grunt], False),
            "close enough": (self.unit(20, 700), None, [grunt], False),
        }
        for name, (unit, at, hostile, sent) in cases.items():
            with self.subTest(name):
                back = stragglers([(rest + [unit], at)], hostile)
                self.assertEqual(20 in back, sent)
                if sent:
                    self.assertEqual(back[20], (50.0, 0.0))

    def test_micro_attack_moves_a_straggler_to_its_group_without_asking_jev(self):
        obs = fight()
        obs["units"][2].update(x=2500, y=0)  # footman2 wandered off while the grunt fights the rest
        groups = {"army": {"ids": {10, 11, 12}, "instruction": "Fight"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Wait for now")) as call:
            actions, _ = micro_agent(self).act(obs, control(groups), None, {})
        self.assertIn({"unit_id": 12, "command": "attack", "arguments": {"x": 100.0, "y": 0.0}}, actions)
        self.assertFalse(any("footman2" in c.args[0]["questions"] for c in call.call_args_list))


class CreepEscape(unittest.TestCase):
    """A badly hurt unit being hit by creeps steps out of their reach; losing units to creeps never pays."""

    def test_who_steps_out_and_who_stays(self):
        creep = dict(unit_id=60, x=300.0, y=0.0)
        hurt = own(11, "hfoo", hp=100, max_hp=420)
        cases = {
            "hurt and being hit": (hurt, [creep], [], {11: 29.0}, True),
            "hurt but not hit lately": (hurt, [creep], [], {11: 20.0}, False),
            "healthy": (own(11, "hfoo", hp=300, max_hp=420), [creep], [], {11: 29.0}, False),
            "no creep in reach": (hurt, [dict(unit_id=60, x=2000.0, y=0.0)], [], {11: 29.0}, False),
            "an enemy player is here": (hurt, [creep], [dict(unit_id=50, x=200.0, y=0.0)], {11: 29.0}, False),
        }
        for name, (unit, creeps, enemies, damage, out) in cases.items():
            with self.subTest(name):
                steps = creep_escapes([unit], creeps, enemies, damage, 30.0)
                self.assertEqual(11 in steps, out)
                if out:
                    self.assertEqual(steps[11], (-500.0, 0.0))  # straight away from the creep

    def test_micro_steps_a_dying_footman_away_from_creeps_without_asking_jev(self):
        obs = fight()
        obs["players"].append(dict(id=24, kind="neutral", relation="enemy"))
        obs["visible_enemies"][0].update(owner=24, x=300.0, y=0.0)  # the Grunt stands in for a creep here
        footman = obs["units"][1]
        micro = micro_agent(self)
        micro.memory.ingest(obs)
        footman.update(hp=100)  # took 320 damage since the last observation
        obs["game_time_seconds"] += 1
        groups = {"army": {"ids": {10, 11, 12}, "instruction": "creep camp 2"}}
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Wait for now")) as call:
            actions, _ = micro.act(obs, control(groups), None, {})
        step = next(a for a in actions if a["unit_id"] == 11)
        self.assertEqual(step["command"], "move")
        self.assertLess(step["arguments"]["x"], footman["x"])  # away from the creep at x=300
        self.assertFalse(any("footman1" in c.args[0]["questions"] for c in call.call_args_list))


class SpellCopies(unittest.TestCase):
    def setUp(self):
        self.reference = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")

    def test_a_creeps_copy_of_a_spell_is_marked_whichever_is_seen_first(self):
        names = Names(self.reference)
        self.assertEqual(names.ability("ACpu"), "Purge (creep)")  # a creep's Purge came first in a game
        self.assertEqual(names.ability("Aprg"), "Purge")  # our Shaman's is still plain Purge
        self.assertEqual(names.ability("AIls"), "Lightning Shield (item)")

    def test_purge_never_targets_our_own_summons(self):
        shaman = own(1, "oshm", mana=200, max_mana=200)
        wolf, grunt = own(2, "osw1"), own(3, "ogru")
        enemy_wolf = own(4, "osw1", owner=1)
        kept = spell_targets(shaman, {"ability_id": "Aprg"}, [wolf, grunt, enemy_wolf], self.reference)
        self.assertEqual([v["unit_id"] for v in kept], [3, 4])

    def test_a_nearly_dead_hero_near_the_enemy_army_is_low(self):
        chieftain = own(1, "Otch", hp=82, max_hp=800, hero=True)
        grunt = own(9, "ogru", x=500.0, owner=1)
        self.assertTrue(low_hero(chieftain, [grunt]))
        self.assertFalse(low_hero(chieftain, []))  # alone, or with creeps only, it may fight on
        self.assertFalse(low_hero({**chieftain, "hp": 400}, [grunt]))
