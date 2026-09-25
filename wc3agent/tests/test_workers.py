"""Workers as macro sees and commands them: who counts as one, where they are, what they are doing."""

import unittest
from pathlib import Path

from agent_fixtures import action, fighting_catalog, own, state, worker, world
from wc3agent.agent import Agent
from wc3agent.game.catalog import Catalog
from wc3agent.game.featurize import describe, worker_lines
from wc3agent.game.orders import Orders
from wc3agent.game.policies import hero_builds, skill_to_learn
from wc3agent.game.workers import harvest_kinds, is_worker
from wc3agent.micro.memory import MicroMemory


class Workers(unittest.TestCase):
    def test_harvest_kinds_follow_live_abilities(self):
        for ability, kinds in (
            ("Ahar", ({"ngol"}, True)),
            ("Aaha", ({"ugol"}, False)),
            ("Awha", ({"egol"}, True)),
            ("Ahrl", (set(), True)),
        ):
            with self.subTest(ability=ability):
                self.assertEqual(harvest_kinds(worker(ability=ability)), kinds)
        ghoul = own(9, "ugho", abilities=[{"ability_id": "Ahrl"}])
        self.assertTrue(is_worker(ghoul, fighting_catalog()))

    def test_worker_history_survives_a_mine_visit_and_ends_at_death(self):
        memory = MicroMemory()
        queued = [action("build", type_id="hhou", x=100, y=200), action("harvest", target_id=70, queued=True)]
        memory.record(1, queued)
        absent = state(2)
        absent["units"] = [own(1, "htow")]
        memory.ingest(absent)
        memory.ingest(state(3))
        orders = memory.context(state(3))["unit_history"]["3"]["submitted_orders"]
        self.assertEqual([entry["action"] for entry in orders], queued)
        memory.ingest(state(4, events=[{"kind": "death", "unit_id": 3}]))
        self.assertNotIn(3, memory.submitted_orders)


class CallToArms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")

    def observation(self, raw="hpea"):
        def live(aid):
            return dict(ability_id=aid, level=1, mana_cost=0, cooldown_seconds=0, cooldown_remaining=0)

        obs = state()
        obs["units"] = [
            own(1, "htow", abilities=[live("Amic")]),
            own(3, raw, x=100, abilities=[live("Ahar"), live("Amil")], order={"name": "harvest", "target_id": 50}),
        ]
        obs["visible_enemies"].append(own(90, "ogru", owner=1, x=200))
        return obs

    def test_macro_resolves_the_hall_and_worker_versions_of_call_to_arms(self):
        w = world()
        w.catalog = self.catalog
        w.references.catalog = self.catalog
        obs = self.observation()
        w.update(obs)
        orders = Orders(w)
        for command, expected, observation in (
            ("cast #1 Call to Arms", "townbellon", obs),
            ("cast #3 Call to Arms", "militia", obs),
            ("cast #1 Back to Work", "townbelloff", self.observation("hmil")),
            ("cast #3 Back to Work", "militiaoff", self.observation("hmil")),
        ):
            with self.subTest(command=command):
                actions, notes = orders.parse(command, observation)
                self.assertEqual(notes, [])
                self.assertEqual([a["arguments"]["order"] for a in actions], [expected])


class CodeDefaults(unittest.TestCase):
    def test_an_unspent_archmage_point_goes_to_water_elemental_then_brilliance_aura(self):
        catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        archmage = own(10, "Hamg", hero=True, level=1)
        self.assertEqual(skill_to_learn(catalog, archmage, {}), "AHwe")
        archmage["level"] = 2
        self.assertEqual(skill_to_learn(catalog, archmage, {"AHwe": 1}), "AHab")
        blademaster = own(11, "Obla", hero=True, level=1)
        self.assertEqual(skill_to_learn(catalog, blademaster, {}), "AOwk")  # listed order where no preference

    def test_every_hero_build_is_legal_and_followed(self):
        catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        for raw, build in hero_builds().items():
            with self.subTest(hero=raw):
                learned = {}
                for level, ability in enumerate(build, 1):
                    self.assertEqual(skill_to_learn(catalog, own(10, raw, hero=True, level=level), learned), ability)
                    learned[ability] = learned.get(ability, 0) + 1
        self.assertEqual(hero_builds()["Ofar"][:2], ["AOsf", "AOcl"])  # Feral Spirit, then Chain Lightning

    def test_gold_workers_beyond_five_walking_to_a_mine_are_sent_to_lumber(self):
        agent = Agent(fighting_catalog(), world().map, None, micro_model="jev", micro_key="fake")
        self.addCleanup(agent.close)
        obs = state()
        mining = {"name": "harvest", "target_id": 50}
        walking = [worker(uid, x=100, order=dict(mining)) for uid in range(3, 8)]
        carrying = worker(8, x=100, order={"name": "returnresources", "target_id": 1})
        obs["units"] = [own(1, "htow"), *walking, carrying]
        obs["inside"] = [dict(unit_id=9, order=dict(mining))]
        agent.macro_memory.gathering[8], agent.macro_memory.gold_mine[8] = "gold", 50
        agent.macro_memory.update(obs)
        moves = agent.cap_gold_workers(obs, [])
        self.assertEqual(len(moves), 2)  # seven on the mine; the carrier and the one inside stay
        self.assertTrue({m["unit_id"] for m in moves} <= {3, 4, 5, 6, 7})
        self.assertEqual({m["arguments"]["target_id"] for m in moves}, {70})
        self.assertTrue(any("to lumber" in note for note in agent.macro_memory.notes))
        # Macro's own orders this turn count toward the mine but are not overridden.
        ordered = [{"unit_id": uid, "command": "harvest", "arguments": {"target_id": 50}} for uid in range(3, 8)]
        self.assertEqual(agent.cap_gold_workers(obs, ordered), [])


class MacroView(unittest.TestCase):
    def agent(self, model):
        agent = Agent(fighting_catalog(), world().map, model, micro_model="jev", micro_key="fake")
        self.addCleanup(agent.close)
        return agent

    def test_temporarily_hidden_workers_remain_in_allocation_and_returns_show_the_phase(self):
        agent = self.agent(None)
        obs = state()
        obs["units"][1]["order"] = {"name": "harvest", "target_id": 50}
        agent.macro_memory.update(obs)
        hidden = {**obs, "game_time_seconds": 2, "units": obs["units"][:1]}
        agent.macro_memory.update(hidden)
        rows = worker_lines(
            agent.macro_memory, [u for u in hidden["units"] if is_worker(u, agent.macro_memory.catalog)], hidden
        )
        self.assertTrue(any("peasant1" in row and "unobserved" in row and "gold" in row for row in rows))
        self.assertIn("peasant1", describe(agent.macro_memory, hidden))
        obs["game_time_seconds"] = 3
        obs["units"][1]["order"] = {"name": "resumeharvesting", "target_id": 1}
        agent.macro_memory.update(obs)
        rows = worker_lines(
            agent.macro_memory, [u for u in obs["units"] if is_worker(u, agent.macro_memory.catalog)], obs
        )
        self.assertTrue(any("returning gold" in row and "townhall1" in row for row in rows))
        agent.macro_memory.update({**hidden, "game_time_seconds": 4, "events": [{"kind": "death", "unit_id": 3}]})
        self.assertFalse(
            any(
                "peasant1" in row
                for row in worker_lines(
                    agent.macro_memory, [u for u in hidden["units"] if is_worker(u, agent.macro_memory.catalog)], hidden
                )
            )
        )

    def test_a_worker_inside_the_mine_is_shown_there_and_a_gold_order_is_already_done(self):
        agent = self.agent(None)
        memory = agent.macro_memory
        memory.update(state())
        inside = {**state(), "game_time_seconds": 2, "units": state()["units"][:1]}
        inside["inside"] = [
            dict(unit_id=3, type_id="hpea", x=300, y=0, hp=220, order=dict(name="harvest", target_id=50))
        ]
        memory.update(inside)
        rows = worker_lines(memory, [], inside)
        self.assertTrue(
            any(row.startswith("  inside") and "gathering gold" in row and "peasant1" in row for row in rows)
        )
        self.assertFalse(any("unobserved" in row for row in rows))
        actions, notes = Orders(memory).parse("gold peasant1", inside)
        self.assertEqual(actions, [])
        self.assertIn("already inside the mine", notes[0])
        self.assertEqual(memory.control.deferred, {})

    def test_macro_structures_reference_only_current_builders(self):
        agent = self.agent(None)
        builders = [
            worker(3, order={"name": "repair", "target_id": 20}),
            worker(4, order={"name": "smart", "target_id": 20}),
        ]
        repairer = worker(5, order={"name": "repair", "target_id": 21})
        obs = state()
        obs["units"] = [
            own(1, "htow"),
            *builders,
            repairer,
            own(20, "hhou", state="constructing", state_seconds=35, hp=50),
            own(21, "hbar", hp=50),
        ]
        memory = agent.macro_memory
        memory.update(obs)

        def structure_rows():
            macro = next(line for line in describe(memory, obs).splitlines() if line.startswith("  farm1:"))
            return [macro]

        for row in structure_rows():
            self.assertIn("peasant1", row)
            self.assertIn("peasant2", row)
            self.assertNotIn("peasant3", row)
        workers = worker_lines(
            agent.macro_memory, [u for u in obs["units"] if is_worker(u, agent.macro_memory.catalog)], obs
        )
        self.assertTrue(any("building farm1" in row and "peasant1" in row and "peasant2" in row for row in workers))
        self.assertTrue(any("repairing barracks1" in row and "peasant3" in row for row in workers))

        # Old observed assignments must disappear when a worker leaves or is no longer visible.
        builders[0]["order"] = {"name": "harvest", "target_id": 70}
        obs["units"] = [u for u in obs["units"] if u["unit_id"] != 4]
        obs["game_time_seconds"] += 1
        memory.update(obs)
        for row in structure_rows():
            self.assertNotIn("peasant1", row)
            self.assertNotIn("peasant2", row)

    def test_a_worker_on_an_order_without_a_name_is_listed_by_its_id(self):
        agent = self.agent(None)
        obs = state()
        obs["units"] = [own(1, "htow"), worker(3, order={"name": 852008, "target_id": 1})]
        agent.macro_memory.update(obs)
        workers = [u for u in obs["units"] if is_worker(u, agent.macro_memory.catalog)]
        self.assertEqual(worker_lines(agent.macro_memory, workers, obs), ["  852008 on townhall1: peasant1"])

    def test_construction_counts_as_stopped_only_after_hit_points_stall_for_a_while(self):
        # An orc builder is inside the structure, so only rising hit points show the work goes on.
        memory = self.agent(None).macro_memory
        obs = state()
        farm = own(20, "hhou", state="constructing", state_seconds=35, hp=50)
        obs["units"] = [own(1, "htow"), farm]
        for dt, hp, stalled in (
            (0.1, 50, False),
            (0.1, 50, False),
            (0.8, 52, False),
            (1.9, 52, False),
            (0.2, 52, True),
        ):
            obs["game_time_seconds"] += dt
            farm["hp"] = hp
            memory.update(obs)
            self.assertEqual(20 in memory.stalled, stalled, (obs["game_time_seconds"], hp))


if __name__ == "__main__":
    unittest.main()
