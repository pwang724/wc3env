"""Stable entity names across observations, macro orders and micro requests."""

import json
import unittest

from agent_fixtures import catalog, fight, fighting_catalog, observation, own, world
from wc3agent.game.featurize import describe, worker_lines
from wc3agent.game.orders import Orders
from wc3agent.game.references import References
from wc3agent.micro.memory import MicroMemory
from wc3agent.micro.names import Names
from wc3agent.micro.request import build_request, map_response


class EntityNames(unittest.TestCase):
    def test_names_survive_absence_and_death_without_renumbering_or_reuse(self):
        refs = References(catalog())
        first, second = own(15507, "hpea"), own(15485, "hpea")
        refs.remember(observation(units=[first, second]))
        self.assertEqual((refs.name(first), refs.name(second)), ("peasant2", "peasant1"))
        refs.remember(observation(units=[]))
        refs.remember(observation(units=[first], events=[dict(kind="death", unit_id=15485, type_id="hpea")]))
        third = own(16000, "hpea")
        refs.remember(observation(units=[third, first]))
        self.assertEqual((refs.name(first), refs.name(second), refs.name(third)), ("peasant2", "peasant1", "peasant3"))
        self.assertEqual(refs.resolve("PEASANT2", {}), 15507)
        fresh = References(catalog())
        fresh.remember(observation(units=[third]))
        self.assertEqual(fresh.name(third), "peasant1")

    def test_every_entity_kind_shares_one_unambiguous_namespace(self):
        obs = observation(visible_enemies=[own(8, "hpea", owner=1), own(50, "ngol", owner=15)])
        refs = References(catalog())
        refs.remember(obs)
        expected = {
            1: "townhall1",
            2: "barracks1",
            3: "peasant1",
            4: "peasant2",
            8: "peasant3",
            50: "goldmine1",
            70: "tree1",
            90: "potionofhealing1",
        }
        self.assertEqual(refs.name_by_id, expected)
        for uid, name in expected.items():
            self.assertEqual(refs.resolve(name, {}), uid)
        # A pickup retains the ground item's identity, independent of its carrier.
        refs.remember(observation(items=[], events=[dict(kind="item_pickup", item_id=90, unit_id=3, type_id="phea")]))
        self.assertEqual(refs.name(90), "potionofhealing1")
        self.assertEqual(refs.name(3), "peasant1")

    def test_upgrade_and_owner_changes_preserve_names_and_show_current_type(self):
        refs = References(catalog())
        refs.remember(observation())
        keep = own(1, "hkee", owner=1)
        refs.remember(observation(units=[], visible_enemies=[keep]))
        self.assertEqual(refs.name(keep), "townhall1")
        self.assertEqual(refs.label(keep), "townhall1 (Keep)")
        self.assertEqual(refs.resolve("townhall1", {1: keep}), 1)

    def test_event_only_entities_get_names_without_renaming_the_producer(self):
        refs = References(catalog())
        refs.remember(observation(events=[dict(kind="train_finish", unit_id=2, trained_id=40, type_id="hfoo")]))
        self.assertEqual(refs.name(2), "barracks1")
        self.assertEqual(refs.name(40), "footman1")
        refs.remember(observation(units=[own(40, "hfoo")]))
        self.assertEqual(refs.name(40), "footman1")

    def test_macro_commands_resolve_names_for_actors_targets_items_and_groups(self):
        w = world()
        obs = observation(
            game_time_seconds=500,
            player=dict(gold=1000, lumber=1000, food_used=5, food_cap=30),
            units=[
                own(1, "htow"),
                own(2, "hbar"),
                own(3, "hpea", hero=True),
                own(4, "hpea"),
                own(5, "hhou"),
                own(9, "hvlt"),
                own(12, "hkee"),
            ],
        )
        w.update(obs)
        orders = Orders(w)
        actions, notes = orders.parse(
            "train townhall1 Peasant x2\ngold peasant2\nmove peasant1 at 10 20\nqueue repair peasant1 on farm1\ntake peasant1 potionofhealing1\nbuy peasant1 from arcanevault1 Potion of Healing\nattack peasant2 on tree1",
            obs,
        )
        self.assertEqual(notes, [])
        self.assertEqual([a["unit_id"] for a in actions], [1, 1, 4, 3, 3, 3, 3, 4])
        self.assertEqual(
            [a["arguments"].get("target_id", a["arguments"].get("shop_id")) for a in actions[2:]],
            [50, None, 5, 90, 9, 70],
        )
        self.assertTrue(actions[4]["arguments"]["queued"])
        moves, notes = orders.parse("group scouts1 peasant1 peasant99 peasant2 at 100 200: scout", obs)
        self.assertEqual({a["unit_id"] for a in moves}, {3, 4})
        self.assertEqual(w.control.groups["scouts1"]["ids"], {3, 4})
        self.assertIn("unknown entity name 'peasant99'", notes[0])

    def test_observations_show_names_instead_of_native_ids(self):
        w = world()
        obs = observation(
            units=[
                own(15507, "hpea", order=dict(name="smart", target_id=14805)),
                own(14805, "hhou", state="constructing", state_seconds=35, hp=30),
                own(15000, "htow"),
            ]
        )
        w.update(obs)
        self.assertEqual(worker_lines(w, [obs["units"][0]], obs), ["  building farm1: peasant1"])
        text = describe(w, obs)
        self.assertNotIn("15507", text)
        self.assertNotIn("14805", text)

    def test_micro_uses_the_shared_names_even_when_its_first_view_is_a_subset(self):
        cat, obs = fighting_catalog(), fight()
        refs = References(cat)
        refs.remember(obs)
        # Micro first sees footman2; numbering must not restart at footman1.
        obs["units"] = [u for u in obs["units"] if u["unit_id"] != 11]
        obs["items"] = [dict(item_id=90, type_id="tint", x=10, y=0)]
        memory = MicroMemory()
        memory.ingest(obs)
        request, menu = build_request(
            obs, {10, 12}, "Fight", memory=memory, names=Names(cat, refs), capabilities={}, catalog=cat, model="jev"
        )
        self.assertEqual(set(request["questions"]), {"archmage1", "footman2"})
        self.assertEqual(set(request["state"]["you_control"]), {"archmage1", "footman2"})
        self.assertEqual(refs.name(obs["items"][0]), "tomeofintelligence1")
        self.assertIn("Attack grunt1", menu.choice_keys[12])
        answers = {"archmage1": {"choice": "Attack grunt1"}, "footman2": {"choice": "Attack grunt1"}}
        _, chosen = map_response(answers, menu)
        self.assertEqual([o["action"]["arguments"]["target_id"] for o in chosen], [50, 50])
        self.assertNotIn("unit_reference", json.dumps(request))


if __name__ == "__main__":
    unittest.main()


class ReusedIds(unittest.TestCase):
    def test_a_dead_units_id_given_to_a_new_unit_gets_a_new_name(self):
        from pathlib import Path

        from wc3agent.game.catalog import Catalog
        from wc3agent.game.references import References

        catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        refs = References(catalog)
        wolf = dict(unit_id=77, type_id="osw1", hp=300, structure=False)
        refs.remember(dict(units=[wolf], events=[]))
        self.assertEqual(refs.name(77), "spiritwolf1")
        refs.remember(dict(units=[], events=[dict(kind="death", unit_id=77, type_id="osw1")]))
        lodge = dict(unit_id=77, type_id="osld", hp=500, structure=True)
        refs.remember(dict(units=[lodge], events=[]))
        self.assertEqual(refs.name(77), "spiritlodge1")
        hero = dict(unit_id=5, type_id="Ofar", hp=400, structure=False)
        refs.remember(dict(units=[hero], events=[]))
        refs.remember(dict(units=[], events=[dict(kind="death", unit_id=5, type_id="Ofar")]))
        refs.remember(dict(units=[hero], events=[]))  # revived: the same name
        self.assertEqual(refs.name(5), "farseer1")
