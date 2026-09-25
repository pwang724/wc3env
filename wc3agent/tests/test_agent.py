"""The macro loop: when replies land, what the next request sees, and handing units to micro."""

import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fixtures import fight, fighting_catalog, observation, own, state, world
from wc3agent.agent import Agent, Step
from wc3agent.game.catalog import Catalog
from wc3agent.game.featurize import describe
from wc3agent.macro.memory import MacroMemory


class AgentTest(unittest.TestCase):
    def test_a_stepped_agent_waits_for_the_turn_and_a_realtime_one_lands_it_when_it_arrives(self):
        answered = threading.Event()

        class Model:
            model = "fake"

            def complete(self, system, messages):
                answered.wait(5)
                return "Two Footmen, then gather.\ntrain #2 Footman x2\ngold #3", {"seconds": 0.5, "usage": {}}

        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        resources = dict(gold=1000, lumber=1000, food_used=5, food_cap=100)
        step = agent.act(observation(player=resources), wait=False)  # realtime: asked now, nothing lands yet
        self.assertEqual((step.records, step.actions), ([], []))
        answered.set()
        agent.thinking[0].result()
        step = agent.act(observation(game_time_seconds=31.0, player=resources), wait=False)
        self.assertEqual([r["kind"] for r in step.records], ["macro"])
        self.assertEqual((step.records[0]["at_game_time"], step.records[0]["landed_at_game_time"]), (30.0, 31.0))
        self.assertEqual(
            [(a["unit_id"], a["command"]) for a in step.actions], [(2, "train"), (2, "train"), (3, "harvest")]
        )
        later = agent.act(observation(game_time_seconds=32.0), wait=False)
        self.assertEqual(later.records, [])  # next request cannot start before 35 or before submission
        self.assertEqual(later.actions, [])
        agent.submitted(step, [{"index": 0, "reason": "queue_full"}], 31.0)
        self.assertIn("queue_full", agent.macro_memory.notes[0])
        agent.close()

        stepped = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        step = stepped.act(observation(player=resources), wait=True)  # stepped: lands on the step it was asked
        self.assertEqual([r["kind"] for r in step.records], ["macro"])
        self.assertEqual(len(step.actions), 3)
        stepped.close()

    def test_events_and_feedback_arriving_while_thinking_reach_the_next_request(self):
        answered = threading.Event()
        requests = []
        # Capture the data at the request boundary without depending on rendered prose or names.
        formatter = patch(
            "wc3agent.agent.describe",
            side_effect=lambda memory, obs: json.dumps({"events": memory.events, "notes": memory.notes}),
        )
        formatter.start()
        self.addCleanup(formatter.stop)

        class Model:
            def complete(self, system, messages):
                requests.append(json.loads(messages[-1]["content"]))
                answered.wait(2)
                return "Keep the current orders.", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        self.addCleanup(answered.set)
        old = dict(kind="train_finish", type_id="hfoo", unit_id=2, trained_id=40)
        new = {**old, "trained_id": 41}
        agent.macro_memory.notes.append("old feedback")
        agent.act(observation(events=[old]))
        agent.macro_memory.notes.append("late feedback")
        agent.act(observation(game_time_seconds=31, events=[new]))
        answered.set()
        agent.thinking[0].result(timeout=2)
        landed = agent.act(observation(game_time_seconds=32))
        self.assertEqual(landed.records[0]["told"], ["old feedback"])
        agent.submitted(landed, [], 32)
        agent.act(observation(game_time_seconds=42), wait=True)
        self.assertEqual(
            requests,
            [{"events": [old], "notes": ["old feedback"]}, {"events": [new], "notes": ["late feedback"]}],
        )

    def test_human_feedback_starts_the_next_request_at_once_and_reaches_it(self):
        requests = []

        class Model:
            def complete(self, system, messages):
                requests.append(messages[-1]["content"])
                return "Keep the current orders.", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        first = agent.act(observation(game_time_seconds=30), wait=True)
        agent.submitted(first, [], 30)
        agent.tell("build a second Barracks now")
        agent.act(observation(game_time_seconds=31), wait=True)  # well before the usual 35
        self.assertEqual(len(requests), 2)
        self.assertIn("A HUMAN WATCHING THIS GAME SAYS", requests[1])
        self.assertIn("build a second Barracks now", requests[1])
        self.assertTrue(agent.feedback.empty())

    def test_builds_are_remembered_only_after_successful_submission(self):
        class Model:
            def complete(self, system, messages):
                return "build #3 Farm at 100 200", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        step = agent.act(observation(), wait=True)
        build = step.actions[0]
        self.assertEqual(build["command"], "build")
        self.assertEqual(agent.macro_memory.outcomes.pending, [])
        self.assertEqual(dict(agent.micro.memory.last_issued), {})
        agent.submitted(step, [{"index": 0, "reason": "bad_arguments"}], 30)
        self.assertEqual(agent.macro_memory.outcomes.pending, [])
        self.assertEqual(dict(agent.micro.memory.last_issued), {})
        self.assertIn("bad_arguments", agent.macro_memory.notes[-1])
        agent.submitted(Step(actions=[build], turns=[1]), [], 45)
        self.assertEqual(agent.macro_memory.outcomes.pending[0]["ordered_at"], 45)
        self.assertEqual(agent.micro.memory.last_issued[3]["submitted_at"], 45)

    def test_all_macro_orders_are_sent_together_without_truncation_or_replay(self):
        class Model:
            def complete(self, system, messages):
                return "\n".join([f"move #3 at {x} 100" for x in range(80)] + ["research #2 Defend"]), {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        step = agent.act(observation(player=dict(gold=1000, lumber=1000, food_used=5, food_cap=100)), wait=True)
        self.assertEqual([a["command"] for a in step.actions], ["move"] * 80 + ["research"])
        self.assertEqual(agent.act(observation(game_time_seconds=31)).actions, [])

    def test_slow_macro_replies_restart_after_submission_with_fresh_state_without_another_delay(self):
        started, answered = threading.Event(), threading.Event()
        requests, active = [], []

        class Model:
            def complete(self, system, messages):
                active.append(True)
                self_outer.assertEqual(len(active), 1)
                requests.append(json.loads(messages[-1]["content"]))
                started.set()
                answered.wait(2)
                active.pop()
                return "move #3 at 900 100", {"seconds": 12}

        self_outer = self
        w = world()
        agent = Agent(w.catalog, w.map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        self.addCleanup(answered.set)
        with (
            patch.object(agent.micro, "act", return_value=([], [])),
            patch(
                "wc3agent.agent.describe",
                side_effect=lambda memory, obs: json.dumps(
                    {"time": obs["game_time_seconds"], "order": obs["units"][2]["order"]}
                ),
            ),
        ):
            agent.act(observation(game_time_seconds=30))
            self.assertTrue(started.wait(2))
            first_future = agent.thinking[0]
            for now in (35, 39):
                self.assertEqual(agent.act(observation(game_time_seconds=now)).records, [])
                self.assertIs(agent.thinking[0], first_future)
            self.assertEqual(len(requests), 1)
            answered.set()
            first_future.result(timeout=2)
            landed = agent.act(observation(game_time_seconds=42))
            self.assertEqual(landed.records[0]["landed_at_game_time"], 42)
            # A caller must acknowledge the ready orders before another request can start.
            agent.act(observation(game_time_seconds=42.1))
            self.assertEqual(len(requests), 1)
            self.assertIsNone(agent.thinking)
            agent.submitted(landed, [], 42.2)
            acknowledged = observation(game_time_seconds=42.2)
            acknowledged["units"][2]["order"] = {"name": "move", "target_id": 0, "x": 900, "y": 100}
            following = agent.act(acknowledged, wait=True)
        self.assertEqual(following.records[0]["at_game_time"], 42.2)
        self.assertEqual([r["time"] for r in requests], [30, 42.2])
        self.assertEqual(requests[1]["order"], acknowledged["units"][2]["order"])

    def test_nothing_gets_micro_control_while_the_first_macro_reply_is_pending(self):
        answered = threading.Event()

        class Model:
            def complete(self, system, messages):
                answered.wait(2)
                return "", {"seconds": 0}

        agent = Agent(fighting_catalog(), world().map, Model(), micro_model="jev", micro_key="fake")
        self.addCleanup(agent.close)
        self.addCleanup(answered.set)
        agent.macro_memory.update(observation())
        obs = fight(hero_hp=20)  # a threatened, hurt hero and an idle worker
        obs["units"].append(own(30, "hpea"))
        with patch("wc3agent.micro.agent.timed_call") as call:
            self.assertEqual(agent.act(obs).actions, [])
            obs["game_time_seconds"] += 1.25
            self.assertEqual(agent.act(obs).actions, [])
            self.assertEqual(agent.macro_memory.control.groups, {})
            call.assert_not_called()

    def test_startup_and_later_steps_preserve_only_explicit_delegation(self):
        class Model:
            def complete(self, system, messages):
                return "move #10 at 900 100", {"seconds": 0}

        agent = Agent(fighting_catalog(), world().map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        scout = {"ids": {11}, "instruction": "Scout", "at": None}
        agent.macro_memory.update(observation())
        agent.macro_memory.control.groups = {"scout": scout}
        obs = fight()
        step = agent.act(obs, wait=True)
        self.assertEqual(agent.macro_memory.control.groups, {"scout": scout})
        agent.submitted(step, [], 30)
        obs["game_time_seconds"] = 31
        obs["units"].append(own(16, "hfoo"))
        agent.act(obs)
        self.assertEqual(agent.macro_memory.control.groups, {"scout": scout})

    def test_a_macro_move_takes_the_unit_from_micro_and_micro_does_not_take_it_back(self):
        class Model:
            def complete(self, system, messages):
                return "move #10 at 4000 0", {"seconds": 0}

        agent = Agent(fighting_catalog(), world().map, Model(), micro_model="jev", micro_key="fake")
        self.addCleanup(agent.close)
        obs = fight(hero_hp=40)
        obs["units"] = [u for u in obs["units"] if u["unit_id"] == 10]
        agent.macro_memory.update(state())
        agent.macro_memory.control.groups = {"army": {"ids": {10}, "instruction": "Fight"}}
        with patch("wc3agent.micro.agent.timed_call") as call:
            step = agent.act(obs, wait=True)
            self.assertEqual(step.actions, [{"unit_id": 10, "command": "move", "arguments": {"x": 4000, "y": 0}}])
            agent.submitted(step, [], obs["game_time_seconds"])
            obs["game_time_seconds"] += 0.25
            obs["units"][0]["order"] = {"name": "move", "x": 4000, "y": 0}
            self.assertEqual(agent.act(obs, wait=True).actions, [])
            call.assert_not_called()


class SkillPoints(unittest.TestCase):
    def test_a_point_macro_leaves_unspent_follows_the_heros_standard_build(self):
        catalog = Catalog.load(Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json")
        replies = iter(["learn archmage1 Summon Water Elemental", ""])

        class Model:
            def complete(self, system, messages):
                return next(replies), {"seconds": 0}

        agent = Agent(catalog, world().map, Model(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        hero = own(10, "Hamg", hero=True, level=3)
        step = agent.act(observation(units=[own(1, "htow"), own(3, "hpea"), hero]), wait=True)
        # Macro spent one of three points on Water Elemental; code spends one more on the build's next step.
        self.assertEqual([a["arguments"]["ability_id"] for a in step.actions], ["AHwe", "AHab"])
        self.assertIn("learned Brilliance Aura", agent.macro_memory.notes[-1])


class Nudges(unittest.TestCase):
    """Warnings code adds to the observation: a hero with no job, and an enemy nobody has seen."""

    def memory(self):
        return MacroMemory(fighting_catalog(), world().map)

    def test_a_hero_idle_for_five_seconds_is_marked_no_job(self):
        memory = self.memory()
        hero = own(10, "Hamg", hero=True, level=1, hp=450, max_hp=450, mana=200, max_mana=285)
        lines = {}
        for now in (30.0, 33.0, 36.0):
            obs = observation(game_time_seconds=now, units=[own(1, "htow"), hero])
            memory.update(obs)
            lines[now] = next(
                line for line in describe(memory, obs).splitlines() if "archmage1" in line and "hp" in line
            )
        self.assertNotIn("NO JOB", lines[33.0])
        self.assertIn("idle for 6s: NO JOB", lines[36.0])
        walking = {**hero, "order": dict(name="move", x=0, y=0)}
        obs = observation(game_time_seconds=40.0, units=[own(1, "htow"), walking])
        memory.update(obs)
        self.assertNotIn("NO JOB", describe(memory, obs))

    def test_an_enemy_nobody_has_seen_asks_for_a_scout(self):
        memory = self.memory()
        obs = observation(game_time_seconds=30.0, visible_enemies=[])
        memory.update(obs)
        self.assertNotIn("Nobody has seen the enemy", describe(memory, obs))  # too early to pull a worker
        obs = observation(game_time_seconds=150.0, visible_enemies=[])
        memory.update(obs)
        self.assertIn("Nobody has seen the enemy yet", describe(memory, obs))
        grunt = fight()["visible_enemies"][0]
        obs = observation(game_time_seconds=160.0, visible_enemies=[grunt])
        memory.update(obs)
        self.assertNotIn("Nobody has seen the enemy", describe(memory, obs))
        obs = observation(game_time_seconds=260.0, visible_enemies=[])
        memory.update(obs)
        self.assertIn("Nobody has seen the enemy for 100s", describe(memory, obs))

    def test_an_idle_unit_in_no_group_is_called_out(self):
        memory = self.memory()
        footman = own(11, "hfoo", hp=420, max_hp=420)
        obs = observation(units=[own(1, "htow"), footman])
        memory.update(obs)
        self.assertIn("IDLE AND IN NO GROUP: footman1", describe(memory, obs))
        memory.control.delegate("army", {11}, "creep camp 2", None)
        self.assertNotIn("IDLE AND IN NO GROUP", describe(memory, obs))
