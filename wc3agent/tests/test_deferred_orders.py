"""Orders for units out of sight wait for them, but not past replacement, death or ownership loss."""

import unittest
from unittest.mock import patch

from agent_fixtures import observation, own, world
from wc3agent.agent import Agent
from wc3agent.game.orders import Orders


class DeferredOrders(unittest.TestCase):
    def setUp(self):
        self.world = world()
        self.obs = observation(player=dict(gold=2000, lumber=2000, food_used=5, food_cap=100))
        self.world.update(self.obs)
        self.orders = Orders(self.world)

    def without(self, *ids, **fields):
        obs = observation(
            units=[u for u in self.obs["units"] if u["unit_id"] not in ids], player=self.obs["player"], **fields
        )
        self.world.update(obs)
        return obs

    def test_missing_worker_does_not_block_visible_workers_and_releases_only_once(self):
        missing = self.without(4)
        actions, notes = self.orders.parse("lumber #3 #4 #999", missing, turn=2)
        self.assertEqual([a["unit_id"] for a in actions], [3])
        self.assertEqual(len(notes), 2)
        self.assertEqual(self.orders.release(missing)[0], [])
        self.world.update(self.obs)
        actions, _, origins = self.orders.release(self.obs)
        self.assertEqual(actions, [{"unit_id": 4, "command": "harvest", "arguments": {"target_id": 70}}])
        self.assertEqual(origins, [2])
        self.assertEqual(self.orders.release(self.obs)[0], [])

    def test_deferred_build_then_gather_keeps_sequence_and_uses_current_resources(self):
        missing = self.without(4)
        self.orders.parse("build u4 Barracks at 700 800\nqueue lumber u4", missing, turn=3)
        returned = observation(
            player=self.obs["player"], destructables=[dict(id=71, type_id="LTlt", x=10, y=0, hp=50, resource="lumber")]
        )
        self.world.update(returned)
        actions, _, origins = self.orders.release(returned)
        self.assertEqual([a["command"] for a in actions], ["build", "harvest"])
        self.assertNotIn("queued", actions[0]["arguments"])
        self.assertEqual(actions[1]["arguments"], {"target_id": 71, "queued": True})
        self.assertEqual(origins, [3, 3])

    def test_later_queued_orders_append_to_deferred_ones_and_keep_each_origin(self):
        missing = self.without(3, 4)
        self.orders.parse("build u3 Farm at 700 800", missing, turn=1)
        self.orders.parse("queue build u3 Barracks at 900 800\nqueue lumber u3", missing, turn=2)
        self.world.update(self.obs)
        actions, _, origins = self.orders.release(self.obs)
        self.assertEqual([a["command"] for a in actions], ["build", "build", "harvest"])
        self.assertEqual([a["arguments"].get("queued", False) for a in actions], [False, True, True])
        self.assertEqual(origins, [1, 2, 2])
        self.world.submitted(actions, 31, turns=origins)
        self.assertEqual(
            [(o["action"]["command"], o["turn"]) for o in self.world.outcomes.pending],
            [("build", 1), ("build", 2), ("harvest", 2)],
        )

    def test_a_new_direct_order_or_group_replaces_the_whole_deferred_sequence(self):
        for replacement, command, turn in (
            ("gold #4", "harvest", 2),
            ("group scouts #4 at 900 100: scout", "move", 2),
            ("queue lumber u4\ngold u4", "harvest", 1),  # even later in the same reply
        ):
            with self.subTest(replacement=replacement):
                missing = self.without(4)
                self.orders.parse("build u4 Farm at 700 800\nqueue lumber u4", missing, turn=1)
                self.orders.parse(replacement, missing, turn=turn)
                self.world.update(self.obs)
                actions, _, origins = self.orders.release(self.obs)
                self.assertEqual(([a["command"] for a in actions], origins), ([command], [turn]))

    def test_a_deferred_build_is_anchored_like_an_immediate_one(self):
        missing = self.without(4)
        actions, _ = self.orders.parse("build #3 Barracks\nbuild #4 Farm", missing, turn=1)
        self.world.submitted(actions, 30, [1] * len(actions))
        later = observation(game_time_seconds=30.2, player=self.obs["player"])
        self.world.update(later)
        deferred, _, _ = self.orders.release(later)
        first, second = actions[0]["arguments"], deferred[0]["arguments"]
        self.assertTrue(first["auto_place"] and second["auto_place"])
        self.assertEqual((first["x"], first["y"]), (second["x"], second["y"]))
        self.assertEqual([a["unit_id"] for a in deferred], [4])

    def test_partial_group_moves_survivors_and_defers_missing_member(self):
        self.orders.parse("group scouts #3 #4: scout", self.obs)
        self.without(3, 4)
        self.assertEqual(self.world.control.groups["scouts"]["ids"], {3, 4})  # absence alone keeps membership
        missing = self.without(4)
        self.world.update({**missing, "events": [dict(kind="death", unit_id=3)]})
        current = observation(units=[own(1, "htow"), own(5, "hfoo")])
        self.world.update(current)
        line = "group army #3 #4 #5 at 900 100: defend"
        actions, notes = self.orders.parse(line, current, turn=4)
        self.assertEqual([a["unit_id"] for a in actions], [5])
        self.assertTrue(notes)
        self.assertEqual(self.world.control.groups["army"]["ids"], {4, 5})
        self.assertEqual(self.orders.parse(line, current, turn=5)[0], [])
        self.assertIn(4, self.world.control.deferred)  # repeating a destination must not lose its pending move
        returned = observation(units=[own(4, "hpea"), own(5, "hfoo")])
        self.world.update(returned)
        actions, _, origins = self.orders.release(returned)
        self.assertEqual([a["unit_id"] for a in actions], [4])
        self.assertEqual(origins, [4])
        self.assertEqual(self.world.control.groups["army"]["ids"], {4, 5})

    def test_group_change_and_disband_cancel_pending_movement(self):
        missing = self.without(4)
        self.orders.parse("group army #3 #4 at 900 100: defend", missing)
        self.orders.parse("group army #3 at 900 100: defend", missing)
        self.assertNotIn(4, self.world.control.deferred)
        self.orders.parse("group army #3 #4 at 900 100: defend", missing)
        self.orders.parse("disband army", missing)
        self.assertEqual(self.world.control.deferred, {})

    def test_death_cancels_orders_and_dead_units_cannot_be_commanded(self):
        missing = self.without(4)
        self.orders.parse("move #4 at 10 20", missing)
        self.world.update({**missing, "events": [dict(kind="death", unit_id=4)]})
        self.assertEqual(self.world.control.deferred, {})
        self.world.update(self.obs)
        self.assertEqual(self.orders.release(self.obs)[0], [])  # revival does not restore them
        self.assertEqual(len(self.orders.parse("gold #4", self.obs)[0]), 1)
        dead = observation(units=[own(3, "hpea", hp=0), own(4, "hpea")])
        self.world.update(dead)
        actions, notes = self.orders.parse("gold #3 #4", dead)
        self.assertEqual([a["unit_id"] for a in actions], [4])
        self.assertTrue(notes)

    def test_ownership_loss_cancels_intent_and_group_membership(self):
        missing = self.without(4)
        self.orders.parse("group scouts #4 at 10 20: scout", missing)
        changed = {**missing, "visible_enemies": [*missing["visible_enemies"], own(4, "hpea", owner=1)]}
        self.world.update(changed)
        self.assertEqual((self.world.control.deferred, self.world.control.groups), ({}, {}))
        actions, notes = self.orders.parse("gold #4", changed)
        self.assertEqual(actions, [])
        self.assertTrue(notes)

    def test_targets_are_not_actors_and_are_revalidated_on_release(self):
        self.obs["units"][2]["hero"] = True
        self.world.update(self.obs)
        actions, notes = self.orders.parse("take #3 #90\nattack #3 #4 on #50", self.obs)
        self.assertEqual(notes, [])
        self.assertEqual([a["unit_id"] for a in actions], [3, 3, 4])
        self.assertEqual(actions[0]["arguments"], {"target_id": 90})
        missing = self.without(4)
        self.orders.parse("attack #4 on #50", missing)
        returned = observation(visible_enemies=[])
        self.world.update(returned)
        actions, notes, _ = self.orders.release(returned)
        self.assertEqual(actions, [])
        self.assertTrue(notes)
        self.assertEqual(self.world.control.deferred, {})


class AgentDeferredOrders(unittest.TestCase):
    def test_returning_unit_dispatches_before_micro_and_preserves_origin_turn(self):
        class Model:
            def complete(self, system, messages):
                return "build u4 Farm at 700 800\nqueue lumber u4", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        agent.macro_memory.update(observation())
        missing = observation(units=[own(1, "htow"), own(3, "hpea")])
        self.assertEqual(agent.act(missing, wait=True).actions, [])
        with patch.object(agent.micro, "act", return_value=([], [])) as micro:
            step = agent.act(observation(game_time_seconds=31))
        self.assertFalse(micro.call_args.kwargs["start"])  # released orders are sent before micro decides again
        self.assertEqual([a["command"] for a in step.actions], ["build", "harvest"])
        self.assertEqual(step.records, [])  # no fabricated LLM call
        agent.submitted(step, [], 31)
        outcome = agent.macro_memory.outcomes.pending[0]
        self.assertEqual((outcome["turn"], outcome["ordered_at"]), (1, 31))

    def test_ready_macro_reply_replaces_deferred_order_before_release(self):
        class Model:
            def complete(self, system, messages):
                return "gold #4", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        agent.macro_memory.update(observation())
        missing = observation(units=[own(1, "htow")])
        agent.orders.parse("build u4 Farm at 700 800\nqueue lumber u4", missing, turn=0)
        step = agent.act(observation(), wait=True)
        self.assertEqual([a["command"] for a in step.actions], ["harvest"])
        self.assertEqual(step.actions[0]["arguments"]["target_id"], 50)
        self.assertEqual(agent.macro_memory.control.deferred, {})


if __name__ == "__main__":
    unittest.main()
