"""Silent refusals, delayed builders, and existing queues must not masquerade as success."""

import unittest

from agent_fixtures import observation, own, world
from wc3agent.agent import Agent


def action(command="train", uid=2, raw="hfoo", **args):
    return {"unit_id": uid, "command": command, "arguments": {"type_id": raw, **args}}


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.memory = world()
        self.tracker = self.memory.outcomes
        self.memory.update(observation())

    def test_existing_queue_does_not_confirm_new_training_and_feedback_is_consumed(self):
        self.memory.submitted([action()], 30, [4])
        self.memory.update(observation(game_time_seconds=34))
        row = self.tracker.events[-1]
        self.assertEqual((row["status"], row["turn"]), ("not_observed", 4))
        self.memory.consume()
        self.memory.update(observation(game_time_seconds=35))
        self.assertEqual(self.memory.notes, [])

    def test_one_new_queue_entry_confirms_only_one_training_order(self):
        for batches in ([[action(), action()]], [[action()], [action()]]):
            with self.subTest(submissions=len(batches)):
                self.setUp()
                for i, batch in enumerate(batches):
                    self.memory.submitted(batch, 30 + i / 2)
                obs = observation(game_time_seconds=31)
                obs["units"][1]["queue"] = ["hfoo", "hfoo"]
                self.memory.update(obs)
                self.assertEqual(len(self.tracker.pending), 1)
                self.memory.update({**obs, "game_time_seconds": 34})
                self.assertEqual([r["status"] for r in self.tracker.events[2:]], ["queued", "not_observed"])

    def test_completion_of_old_unit_plus_new_queue_entry_confirms_new_order(self):
        self.memory.submitted([action()], 30)
        self.memory.update(
            observation(
                game_time_seconds=31, events=[dict(kind="train_finish", unit_id=2, type_id="hfoo", trained_id=99)]
            )
        )
        self.assertEqual(self.tracker.events[-1]["status"], "queued")

    def test_walking_builder_stays_pending_then_new_structure_confirms_it(self):
        build = action("build", 3, "hhou", x=100, y=200)
        self.memory.submitted([build], 30)
        obs = observation(game_time_seconds=45)
        obs["units"][2]["order"] = dict(name="hhou", target_id=0, x=100, y=200)
        self.memory.update(obs)
        self.assertEqual(len(self.tracker.pending), 1)
        obs["units"].append(own(6, "hhou", x=100, y=200, state="constructing"))
        self.memory.update({**obs, "game_time_seconds": 46})
        self.assertEqual(self.tracker.events[-1]["status"], "started")

    def test_existing_building_does_not_confirm_a_failed_build(self):
        obs = observation(units=[own(1, "htow"), own(3, "hpea"), own(6, "hhou", x=100, y=200)])
        self.memory.update(obs)
        self.memory.submitted([action("build", 3, "hhou", x=100, y=200)], 30)
        self.memory.update({**obs, "game_time_seconds": 34})
        self.assertEqual(self.tracker.events[-1]["status"], "not_observed")

    def test_shift_queued_orders_wait_behind_an_active_job_but_not_behind_gathering(self):
        self.memory.submitted(
            [action("build", 4, "hhou", x=100, y=200, queued=True), action("harvest", 4, target_id=70, queued=True)],
            30,
            [1, 1],
        )
        building = observation(game_time_seconds=60)
        building["units"][3]["order"] = dict(name="hbar", target_id=0, x=500, y=500)
        self.memory.update(building)
        self.assertEqual(len(self.tracker.pending), 2)
        self.assertEqual(self.memory.notes, [])
        self.memory.update(observation(game_time_seconds=61))  # gathering its mine again: the build was dropped
        self.assertEqual(self.tracker.pending, [])  # the gather took effect; the build never will
        self.assertIn("No new structure", self.memory.notes[0])

    def test_one_structure_confirms_one_build_credited_to_the_worker_seen_building_it(self):
        self.memory.submitted([action("build", 3, "hhou", x=100, y=200), action("build", 4, "hhou", x=100, y=200)], 30)
        obs = observation(game_time_seconds=31)
        obs["units"].append(own(6, "hhou", x=100, y=200, state="constructing"))
        obs["units"][3]["order"] = dict(name="repair", target_id=6, x=100, y=200)  # worker 4 builds it
        self.memory.update(obs)
        last = lambda: (self.tracker.events[-1]["action"]["unit_id"], self.tracker.events[-1]["status"])  # noqa: E731
        self.assertEqual(last(), (4, "started"))
        self.memory.update({**obs, "game_time_seconds": 34})
        self.assertEqual(last(), (3, "not_observed"))

    def test_research_upgrade_and_environment_rejection_are_recorded(self):
        self.memory.submitted(
            [action("research", 2, "Rhde"), action("train", 1, "hkee"), action()],
            30,
            [2, 2, 2],
            rejected=[{"index": 2, "reason": "not_owned"}],
        )
        obs = observation(game_time_seconds=31)
        obs["units"][0]["state"] = "upgrading"
        obs["units"][1]["queue"] = ["hfoo", "Rhde"]
        self.memory.update(obs)
        self.assertEqual(
            [r["status"] for r in self.tracker.events], ["submitted", "submitted", "rejected", "queued", "started"]
        )
        self.assertIn("not_owned", self.memory.notes[0])

    def test_replacement_closes_only_that_workers_earlier_pending_builds(self):
        self.memory.submitted(
            [
                action("build", 3, "hhou", x=100, y=200),
                action("build", 3, "hbar", x=500, y=200, queued=True),
                action("build", 4, "hhou", x=100, y=800),
            ],
            30,
            [2, 2, 2],
        )
        self.memory.submitted([{"unit_id": 3, "command": "harvest", "arguments": {"target_id": 70}}], 31, [3])
        replaced = [r for r in self.tracker.events if r["status"] == "superseded"]
        self.assertEqual([(r["id"], r["turn"], r["at_game_time"]) for r in replaced], [(1, 2, 31), (2, 2, 31)])
        self.assertEqual(
            [p["action"]["command"] for p in self.tracker.pending], ["build", "harvest"]
        )  # 4's, then 3's gathering
        self.assertEqual(len(self.memory.notes), 2)
        self.tracker.finish(180)
        self.assertEqual([r["id"] for r in self.tracker.events if r["status"] == "unconfirmed"], [3, 4])
        self.setUp()  # within one batch, the new queue survives
        self.memory.submitted(
            [
                action("build", 3, "hhou", x=100, y=200),
                action("build", 3, "hbar", x=500, y=200),
                action("build", 3, "hhou", x=900, y=200, queued=True),
            ],
            30,
        )
        self.assertEqual([p["id"] for p in self.tracker.pending], [2, 3])
        self.assertEqual([r["id"] for r in self.tracker.events if r["status"] == "superseded"], [1])

    def test_queued_rejected_and_non_order_actions_do_not_replace_pending_orders(self):
        self.memory.submitted([action("build", 3, "hhou", x=100, y=200), action()], 30)
        self.memory.submitted(
            [{"unit_id": 3, "command": "stop", "arguments": {}}], 31, rejected=[{"index": 0, "reason": "unknown_unit"}]
        )
        self.memory.submitted(
            [
                {"unit_id": 3, "command": "stop", "arguments": {"queued": True}},
                {"unit_id": 3, "command": "select", "arguments": {}},
            ],
            32,
        )
        self.memory.submitted(
            [{"unit_id": 2, "command": "cast", "arguments": {"order": "setrally", "x": 100, "y": 200}}], 33
        )  # a rally does not replace the producer's training
        self.assertEqual([p["id"] for p in self.tracker.pending], [1, 2])
        self.assertFalse(any(r["status"] == "superseded" for r in self.tracker.events))

    def last(self, t, **fields):
        self.memory.update(observation(game_time_seconds=t, **fields))
        return self.tracker.events[-1]

    def test_a_buy_is_confirmed_only_by_a_sale_to_the_buyer(self):
        shop = own(9, "hvlt", x=600)
        self.memory.update(observation(units=[own(3, "hpea"), shop]))
        buy = {"unit_id": 3, "command": "buy", "arguments": {"shop_id": 9, "item_type_id": "phea"}}
        self.memory.submitted([buy], 30, [4])
        row = self.last(34, units=[own(3, "hpea"), shop])
        self.assertEqual(row["status"], "not_observed")  # too far from the shop
        self.memory.submitted([buy], 34, [5])
        sale = dict(kind="item_sold", unit_id=9, buyer_id=3, type_id="phea")
        self.assertEqual(self.last(35, units=[own(3, "hpea"), shop], events=[sale])["status"], "bought")

    def test_macro_casts_and_item_uses_are_confirmed_by_their_events_or_the_item(self):
        cast = {"unit_id": 3, "command": "cast", "arguments": {"order": "blizzard", "x": 1, "y": 1}}
        self.memory.submitted([cast], 30, [4])
        effect = dict(kind="spell_effect", unit_id=3, ability_id="AHbz")
        self.assertEqual(self.last(31, events=[effect])["status"], "cast")
        potion = dict(unit_id=3, slot=0, type_id="phea", charges=1)
        self.memory.update(observation(inventory=[potion]))
        self.memory.submitted([{"unit_id": 3, "command": "use_item", "arguments": {"slot": 0}}], 31, [5])
        self.assertEqual(self.last(32, inventory=[])["status"], "used")
        rally = {"unit_id": 1, "command": "cast", "arguments": {"order": "setrally", "x": 1, "y": 1}}
        self.memory.submitted([rally, {**cast, "unit_id": 4}], 32, [None, None])  # rally, and micro's cast
        self.assertEqual(self.tracker.pending, [])

    def test_a_gather_order_that_leaves_the_worker_idle_is_reported_and_working_ones_are_quiet(self):
        self.memory.submitted([{"unit_id": 3, "command": "harvest", "arguments": {"target_id": 50}}], 30, [4])
        row = self.last(34)  # worker 3 stands idle
        self.assertEqual(row["status"], "not_observed")
        self.memory.consume()
        self.memory.submitted([{"unit_id": 4, "command": "harvest", "arguments": {"target_id": 50}}], 34, [5])
        self.assertEqual(self.last(35)["status"], "gathering")  # worker 4 is at the mine, harvesting
        self.assertEqual(self.memory.notes, [])

    def test_missing_training_reaches_the_next_real_macro_request(self):
        class Model:
            def complete(self, system, messages):
                return "train #2 Footman", {"seconds": 0}

        agent = Agent(self.memory.catalog, self.memory.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        first = agent.act(observation(), wait=True)
        agent.submitted(first, [], 30)
        agent.act(observation(game_time_seconds=34))
        second = agent.act(observation(game_time_seconds=40), wait=True)
        record = second.records[0]
        self.assertIn("not_observed", record["request"]["messages"][-1]["content"])
        self.assertEqual(record["request"]["messages"][-2]["content"], first.records[0]["reply"])
        self.assertEqual(len(record["told"]), 1)
