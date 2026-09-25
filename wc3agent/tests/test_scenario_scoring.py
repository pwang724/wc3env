"""Scenario metric evaluation, completion and scripted opponents."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_fixtures import observation, own, scenario_fixture, world
from wc3agent.game.catalog import Catalog
from wc3agent.scenarios.scenario import Scenario, names

DATA = Path(__file__).parents[1] / "src" / "wc3agent" / "game" / "data"


class Scenarios(unittest.TestCase):
    def test_every_definition_loads_and_names_known_metrics(self):
        w = world()
        w.update(observation())
        for name in names():
            scenario = Scenario(name)
            scenario.catalog, scenario.map, scenario.home = w.catalog, w.map, w.home
            scenario.saw({**observation(), "score": {}})
            for check in scenario.checks:
                if not check["metric"].startswith("camp_cleared"):  # the test map has no camps
                    scenario.metric(check["metric"], {**observation(), "score": {}})

    def test_checks_are_scored_from_what_was_seen(self):
        scenario = scenario_fixture(
            checks=[
                {"metric": "items_picked_up", "op": ">=", "value": 4},
                {"metric": "items_carried", "op": ">=", "value": 2},
                {"metric": "unspent_skill_points", "op": "==", "value": 0},
            ],
            finish={"metric": "items_picked_up", "op": ">=", "value": 4},
        )
        w = world()
        w.update(observation())
        scenario.catalog, scenario.map, scenario.home = w.catalog, w.map, w.home
        seen = observation(
            events=[dict(kind="item_pickup", unit_id=3, item_id=90, type_id="phea")] * 4, inventory=[{}, {}]
        )
        scenario.saw({**seen, "score": {}})
        results = {c["metric"]: c for c in scenario.score({**seen, "score": {}})}
        self.assertTrue(results["items_picked_up"]["ok"] and results["items_carried"]["ok"])
        self.assertIsNone(results["unspent_skill_points"]["measured"])  # no hero: nothing to measure, so not a pass
        self.assertFalse(results["unspent_skill_points"]["ok"])
        self.assertTrue(scenario.finished({**seen, "score": {}}))  # four pickups: the goal is met, the game can stop

    def test_a_defeat_is_scored_from_the_last_observation_before_it(self):
        scenario = scenario_fixture(checks=[{"metric": "hero_alive", "op": "==", "value": True}])
        w = world()
        w.update(observation())
        scenario.catalog, scenario.map, scenario.home = w.catalog, w.map, w.home
        alive = observation(units=[own(1, "htow"), own(10, "hpea", hero=True, level=1)], result="")
        scenario.saw(alive)
        defeat = observation(units=[], result="defeat")  # the ended game lists none of our units
        scenario.saw(defeat)
        self.assertTrue(scenario.score(defeat)[0]["ok"])

    def test_a_scenario_finishes_early_only_once_its_threshold_is_met(self):
        scenario = scenario_fixture(finish={"metric": "items_bought", "op": ">=", "value": 3})
        w = world()
        w.update(observation())
        scenario.catalog, scenario.map, scenario.home = w.catalog, w.map, w.home
        scenario.tags["hero"] = [3]
        sale = dict(kind="item_sold", unit_id=9, buyer_id=3, type_id="phea")
        stranger = dict(kind="item_sold", unit_id=9, buyer_id=4, type_id="phea")
        scenario.saw({**observation(events=[sale, sale, stranger]), "score": {}})
        self.assertEqual(scenario.metric("items_bought", observation()), 2)
        self.assertFalse(scenario.finished({**observation(), "score": {}}))
        scenario.saw({**observation(events=[sale]), "score": {}})
        self.assertTrue(scenario.finished({**observation(), "score": {}}))

    def test_a_scenario_can_play_on_after_its_goal(self):
        scenario = scenario_fixture(finish="items_picked_up", finish_after_seconds=15)
        w = world()
        w.update(observation())
        scenario.catalog, scenario.map, scenario.home = w.catalog, w.map, w.home
        scenario.saw({**observation(events=[dict(kind="item_pickup", unit_id=3, item_id=90)]), "score": {}})
        at = lambda t: {**observation(), "game_time_seconds": t, "score": {}}  # noqa: E731
        self.assertFalse(scenario.finished(at(40)))
        self.assertFalse(scenario.finished(at(54)))
        self.assertTrue(scenario.finished(at(55)))
        self.assertEqual(scenario.metric("seconds", at(55)), 40)  # time to the goal, not the extra play


class OpponentsAndMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.load(DATA / "reference.json")

    def test_raid_opponent_waits_then_targets_workers_then_the_hall(self):
        s = scenario_fixture(opponent="raid", opponent_after_seconds=5)
        s.catalog, s.home = self.catalog, {"x": 0.0, "y": 0.0}
        s.tags = {"enemy": [900, 901]}
        s.started_at = 0.0
        units = [
            {"unit_id": 1, "type_id": "opeo", "structure": False, "hp": 250, "x": 100.0, "y": 200.0},
            {"unit_id": 2, "type_id": "opeo", "structure": False, "hp": 250, "x": 300.0, "y": 400.0},
            {"unit_id": 3, "type_id": "ogru", "structure": False, "hp": 700, "x": -1400.0, "y": -900.0},
        ]
        self.assertEqual(s.opponent_actions({"game_time_seconds": 4.0, "units": units}), [])
        orders = s.opponent_actions({"game_time_seconds": 10.0, "units": units})
        self.assertEqual([o["unit_id"] for o in orders], [900, 901])
        self.assertEqual(orders[0]["arguments"], {"x": 200.0, "y": 300.0})  # the Peons, not the Grunt
        s.last_opponent_order = -1e9
        orders = s.opponent_actions({"game_time_seconds": 20.0, "units": units[2:]})  # Peons are inside Burrows
        self.assertEqual(orders[0]["arguments"], {"x": 0.0, "y": 0.0})  # the hall

    def test_metrics_accumulate_observed_time_and_count_current_units(self):
        s = scenario_fixture()
        s.catalog, s.home = self.catalog, {"x": 0.0, "y": 0.0}
        s.map = SimpleNamespace(camps=[])

        def obs(t, units):
            return {
                "game_time_seconds": t,
                "units": units,
                "events": [],
                "visible_enemies": [],
                "inventory": [],
                "player": {"gold": 0, "lumber": 0, "food_used": 5, "food_cap": 10},
                "players": [],
                "score": {},
            }

        ancient = {
            "unit_id": 7,
            "type_id": "eaom",
            "structure": True,
            "hp": 1000,
            "max_hp": 1000,
            "hero": False,
            "order": None,
            "state": None,
        }
        wisp = {
            "unit_id": 8,
            "type_id": "ewsp",
            "structure": False,
            "hp": 120,
            "max_hp": 120,
            "hero": False,
            "order": None,
        }
        well = {
            "unit_id": 9,
            "type_id": "emow",
            "structure": True,
            "hp": 300,
            "max_hp": 600,
            "hero": False,
            "order": None,
            "state": None,
        }
        s.saw(obs(0.0, [ancient, wisp, well]))
        s.saw(obs(10.0, [{**ancient, "structure": False}, well]))  # uprooted; the Wisp is out of sight
        s.saw(obs(25.0, [ancient, wisp, well]))
        last = obs(25.0, [ancient, wisp, well])
        self.assertEqual(s.metric("uprooted_seconds", last), 15.0)
        self.assertEqual(s.metric("fewest_workers", last), 0)
        self.assertEqual(s.metric("present_seconds:ewsp", last), 10.0)
        self.assertEqual(s.metric("count:eaom", last), 1)
        self.assertEqual(s.metric("count:emow", last), 1)
        self.assertEqual(s.metric("food_cap", last), 10)
        self.assertEqual(s.metric("structure_health_percent", last), 50)
