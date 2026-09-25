"""Asynchronous micro replies: ordering, pacing and discarding answers that went stale while deciding."""

import unittest
from concurrent.futures import Future
from unittest.mock import patch

from agent_fixtures import answer, control, fight, fighting_catalog, own, state, world
from wc3agent.agent import Agent
from wc3agent.micro.agent import MAX_CALLS, MicroAgent


class Calls:
    def __init__(self):
        self.calls = []

    def submit(self, fn, request, key, record):
        future = Future()
        future.set_running_or_notify_cancel()
        record["request"] = request
        self.calls.append((future, request, key, record))
        return future

    def complete(self, index, choice="Attack"):
        future, request, key, record = self.calls[index]
        answer(choice)(request, key, record)
        future.set_result(record["response"])


class AsyncMicro(unittest.TestCase):
    def setUp(self):
        self.micro = MicroAgent(fighting_catalog(), model="jev", key="fake")
        self.addCleanup(self.micro.close)
        self.calls = Calls()
        patched = patch.object(self.micro.executor, "submit", self.calls.submit)
        patched.start()
        self.addCleanup(patched.stop)
        clock_patch = patch("wc3agent.micro.agent.monotonic", return_value=30.0)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.groups = {
            "hero": {"ids": {10}, "instruction": "Fight"},
            "footmen": {"ids": {11, 12}, "instruction": "Fight"},
        }
        self.control = control()

    def act(self, obs=None, groups=None, wall_time=None):
        obs = obs or fight()
        self.clock.return_value = obs["game_time_seconds"] if wall_time is None else wall_time
        control(self.groups if groups is None else groups, into=self.control)
        return self.micro.act(obs, self.control, None, {}, wait=False)

    def test_fast_group_lands_and_restarts_from_fresh_state_while_slow_group_waits(self):
        self.assertEqual(self.act(), ([], []))
        self.assertEqual(len(self.calls.calls), 2)
        self.calls.complete(1)
        self.assertTrue(self.micro.ready.is_set())
        obs = fight()
        obs["game_time_seconds"] = 30.25
        actions, records = self.act(obs)
        self.assertEqual({a["unit_id"] for a in actions}, {11, 12})
        self.assertEqual(records[0]["landed_at_game_time"], 30.25)
        self.assertFalse(self.calls.calls[0][0].done())
        self.assertEqual(len(self.calls.calls), 2)  # don't decide again from the pre-order snapshot
        obs = fight()
        obs["game_time_seconds"] = 30.5
        obs["units"][1]["hp"] = 75
        self.act(obs)
        self.assertEqual(len(self.calls.calls), 2)
        obs["game_time_seconds"] = 31
        self.act(obs)
        request = self.calls.calls[2][1]
        self.assertEqual(request["state"]["time_seconds"], 31)
        self.assertIn("75", str(request["state"]["you_control"]))
        for _ in range(20):
            self.act(obs)
        self.assertEqual(len(self.calls.calls), 3)  # one outstanding call per group/type

    def test_realtime_asks_follow_the_wall_clock_and_stepped_asks_follow_game_time(self):
        self.act()
        self.calls.complete(1)
        obs = {**fight(), "game_time_seconds": 32}
        actions, _ = self.act(obs, wall_time=30.25)
        self.assertEqual({a["unit_id"] for a in actions}, {11, 12})  # replies are not delayed
        self.act(obs, wall_time=30.99)
        self.assertEqual(len(self.calls.calls), 2)
        self.act(obs, wall_time=31)
        self.assertEqual(len(self.calls.calls), 3)
        with patch("wc3agent.micro.agent.timed_call", side_effect=answer("Attack")):
            micro = MicroAgent(fighting_catalog(), model="jev", key="fake")
            self.addCleanup(micro.close)
            for now in (30, 30.5, 31):
                micro.act({**fight(), "game_time_seconds": now}, control(self.groups), None, {})
        self.assertEqual(micro.calls, 4)  # both groups at 30 and 31, with the wall clock unchanged

    def test_a_changed_objective_discards_old_answers_even_if_changed_back(self):
        self.act()
        changed = {**self.groups, "footmen": {"ids": {11, 12}, "instruction": "Retreat"}}
        self.act(groups=changed)
        self.calls.complete(1)
        actions, records = self.act()
        self.assertEqual(actions, [])
        self.assertEqual(len(records[0]["dropped_actions"]), 2)

    def test_current_cast_does_not_veto_the_controllers_chosen_interrupt(self):
        self.act()
        self.calls.complete(0)
        obs = fight()
        obs["units"][0]["order"] = {"name": "blizzard", "x": 300, "y": 0}
        actions, records = self.act(obs)
        self.assertEqual([a["command"] for a in actions], ["attack"])
        self.assertEqual(records[0]["dropped_actions"], [])

    def test_direct_macro_retreat_removes_control_and_cannot_be_readmitted(self):
        self.act()
        self.calls.complete(0)
        self.control.touch({10})
        groups = {"footmen": self.groups["footmen"]}
        obs = fight(hero_hp=40)
        obs["units"][0]["order"] = {"name": "move", "x": 4000, "y": 0}
        actions, records = self.act(obs, groups)
        self.assertEqual(actions, [])
        self.assertEqual(records[0]["dropped_actions"][0]["reason"], "superseded by new macro orders")
        # Arriving or losing an order must not create a fallback combat group.
        obs["game_time_seconds"] += 2
        obs["units"][0]["order"] = None
        self.act(obs, groups)
        self.assertEqual(len(self.calls.calls), 2)

    def test_item_answer_cannot_use_a_different_item_that_replaced_the_slot(self):
        obs = fight()
        obs["units"].append(own(1, "htow"))
        groups = {"hero": self.groups["hero"]}
        self.micro.act(obs, control(groups), 1, {}, wait=False)
        self.calls.complete(0, "Use Scroll of Town Portal")
        self.micro.catalog.items["other"] = {**self.micro.catalog.items["stwp"], "name": "Different item"}
        obs["inventory"][0]["type_id"] = "other"
        actions, records = self.micro.act(obs, control(groups), 1, {}, wait=False)
        self.assertEqual(actions, [])
        self.assertEqual(records[0]["dropped_actions"][0]["reason"], "inventory slot changed while deciding")

    def test_missing_target_is_discarded_while_other_enemy_keeps_group_active(self):
        self.act()
        self.calls.complete(1)
        obs = fight()
        obs["visible_enemies"][0]["unit_id"] = 51
        actions, records = self.act(obs)
        self.assertEqual(actions, [])
        self.assertEqual({d["reason"] for d in records[0]["dropped_actions"]}, {"target no longer available"})

    def test_pending_limit_is_bounded_and_waiting_groups_get_a_turn(self):
        obs = fight()
        obs["units"] = [own(uid, "hfoo") for uid in range(100, 100 + MAX_CALLS + 1)]
        groups = {str(u["unit_id"]): {"ids": {u["unit_id"]}, "instruction": "Fight"} for u in obs["units"]}
        self.act(obs, groups)
        self.act(obs, groups)
        self.assertEqual(len(self.calls.calls), MAX_CALLS)
        self.calls.complete(0, "Wait")  # the grunt is beyond a footman's reach, so no attack option
        self.act(obs, groups)
        self.assertEqual(len(self.calls.calls), MAX_CALLS + 1)
        self.assertIn(str(100 + MAX_CALLS), self.calls.calls[-1][3]["control_group"])

    def test_failure_does_not_hold_other_groups_and_finishing_logs_unapplied_answers(self):
        self.act()
        self.calls.calls[0][0].set_exception(RuntimeError("failed fake"))
        self.calls.complete(1)
        records = self.micro.finish(fight())
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["error"], "failed [REDACTED]")
        self.assertEqual({d["reason"] for d in records[1]["dropped_actions"]}, {"run ended"})
        self.assertEqual(records[1]["actions"], [])
        self.assertEqual(self.micro.pending, {})

    def test_macro_group_change_without_game_orders_supersedes_completed_jev_answer(self):
        class Model:
            def complete(self, system, messages):
                return "group footmen #11 #12: Retreat", {"seconds": 0}

        agent = Agent(fighting_catalog(), world().map, Model(), micro_model="jev", micro_key="fake")
        self.addCleanup(agent.close)
        agent.macro_memory.update(state())
        agent.micro.close()
        agent.micro = self.micro
        self.control = control(self.groups, into=agent.macro_memory.control)
        self.act()
        self.calls.complete(0)
        self.calls.complete(1)
        step = agent.act(fight(), wait=True)
        self.assertEqual({a["unit_id"] for a in step.actions}, {10})
        stale = next(r for r in step.records if r.get("control_group") == "footmen / Footman")
        self.assertEqual(len(stale["dropped_actions"]), 2)
