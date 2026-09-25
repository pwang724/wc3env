"""Exercise the real agent/runner loop with a deterministic in-memory session."""

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from agent_fixtures import observation, world
from wc3agent.agent import Agent, Step
from wc3agent.play import MeleeConfig, advance, play
from wc3agent.recording import RunLog

from wc3env import GameConfig
from wc3env.protocol import Observation, normalize_actions, validate_actions


class Model:
    model = "fake"

    def complete(self, system, messages):
        return "train #2 Footman x2", {"seconds": 300.0, "usage": {}}


class Session:
    def __init__(self, config):
        self.config = config
        self.now = 30.0
        self.closed = False
        self.steps = []
        self.obs = observation(protocol_version=1, sequence=0)
        self.obs["player"].update(gold=1000, lumber=1000, food_cap=100)

    def reset(self):
        return {0: self.obs}

    def step(self, actions, ms=None):
        validate_actions(Observation.from_dict(self.obs), normalize_actions(actions[0]))
        duration = self.config.step_ms if ms is None else ms
        self.steps.append((actions, duration))
        self.now += duration / 1000
        self.obs = observation(protocol_version=1, sequence=len(self.steps), game_time_seconds=self.now)
        self.obs["player"].update(gold=1000, lumber=1000, food_cap=100)
        return {0: self.obs}, False, {"rejected": {0: []}}

    def debug(self, op, **args):
        pass

    def save_replay(self, path):
        return path

    def close(self):
        self.closed = True


class PlayTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)
        fixture = world()
        self.sessions = []

        def create(config):
            session = Session(config)
            self.sessions.append(session)
            return session

        for target, replacement in (
            ("wc3agent.play.GameSession", create),
            ("wc3agent.play.environment", lambda: {}),
            ("wc3agent.play.Catalog.load", lambda path: fixture.catalog),
            ("wc3agent.play.MapInfo.load", lambda path: fixture.map),
        ):
            mock = patch(target, replacement)
            mock.start()
            self.addCleanup(mock.stop)
        self.config = MeleeConfig(out=self.out, max_game_minutes=0.05)

    def test_a_build_records_the_site_the_engine_chose_not_the_anchor(self):
        obs = observation()
        session = Mock()
        session.config = GameConfig()
        session.step.return_value = (
            {0: obs},
            False,
            {"rejected": {0: []}, "placements": {0: [{"index": 1, "x": 448, "y": 320}]}},
        )
        actions = [
            {"unit_id": 3, "command": "stop", "arguments": {}},
            {"unit_id": 4, "command": "build", "arguments": {"type_id": "hhou", "x": 0, "y": 0, "auto_place": True}},
        ]
        returned, rejected = advance(session, actions)
        self.assertEqual(returned, obs)
        self.assertEqual(rejected, [])
        self.assertEqual(actions[1]["arguments"]["x"], 448)
        self.assertEqual(actions[1]["arguments"]["y"], 320)
        self.assertEqual(actions[0]["arguments"], {})

    def test_a_line_typed_in_the_game_chat_reaches_the_next_macro_request(self):
        requests = []

        class Listener(Model):
            def complete(self, system, messages):
                requests.append(messages[-1]["content"])
                return super().complete(system, messages)

        step = Session.step

        def typed_once(session, actions, ms=None):
            result = step(session, actions, ms)
            if len(session.steps) == 1:
                session.obs["chat"] = ["build a second Barracks"]
            return result

        with patch.object(Session, "step", typed_once):
            play(self.config, model=Listener())
        self.assertNotIn("build a second Barracks", requests[0])
        self.assertIn("A HUMAN WATCHING THIS GAME SAYS", requests[1])
        self.assertIn("build a second Barracks", requests[1])

    def test_model_latency_does_not_advance_stepped_time_and_batches_are_valid(self):
        result = play(self.config, model=Model())
        session = self.sessions[0]
        self.assertEqual([ms for _, ms in session.steps], [1000, 1000, 1000])
        self.assertEqual(result["game_seconds"], 33)
        self.assertEqual([a["command"] for a in session.steps[0][0][0]], ["train", "train"])
        self.assertEqual(session.steps[1][0][0], [])
        self.assertTrue(session.closed)
        calls = [json.loads(line) for line in (self.out / "calls.jsonl").read_text().splitlines()]
        submissions = [json.loads(line) for line in (self.out / "actions.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls[0]["actions"]), 2)
        self.assertEqual([s["at_game_time"] for s in submissions], [30, 31, 32])
        self.assertEqual(len(submissions[0]["actions"]), 2)
        outcomes = [json.loads(line) for line in (self.out / "outcomes.jsonl").read_text().splitlines()]
        self.assertEqual([o["status"] for o in outcomes], ["submitted", "submitted", "not_observed", "not_observed"])
        self.assertTrue(all(o["turn"] == 1 for o in outcomes))
        self.assertEqual(calls[0]["request"]["messages"][-1]["content"], calls[0]["observation"])

    def test_macro_orders_reach_the_game_before_micro_sees_the_updated_groups(self):
        class Commands:
            def complete(self, system, messages):
                return "move #3 at 900 100\ngroup scouts #4 at 900 100: scout the base", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Commands(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        agent.macro_memory.control.groups = {"old": {"ids": {3, 4}, "instruction": "old task", "at": None}}
        with patch.object(agent.micro, "act", return_value=([], [])) as micro:
            step = agent.act(observation(), wait=True)
            self.assertFalse(micro.call_args.kwargs["start"])  # no new questions before these orders land
        self.assertEqual([(a["unit_id"], a["command"]) for a in step.actions], [(3, "move"), (4, "move")])
        self.assertEqual(set(agent.macro_memory.control.groups), {"scouts"})
        self.assertEqual(agent.macro_memory.control.groups["scouts"]["ids"], {4})
        agent.submitted(step, [], 30)
        acknowledged = observation(game_time_seconds=31)
        acknowledged["units"][2]["order"] = {"name": "move", "target_id": 0, "x": 900, "y": 100}
        acknowledged["units"][3]["order"] = {"name": "move", "target_id": 0, "x": 900, "y": 100}

        def decide(obs, control, hall, tech, **context):
            self.assertIs(obs, acknowledged)
            self.assertEqual(control.groups["scouts"]["instruction"], "scout the base")
            self.assertEqual(agent.micro.memory.last_issued[4]["action"], step.actions[1])
            return [{"unit_id": 4, "command": "stop", "arguments": {}}], []

        with patch.object(agent.micro, "act", side_effect=decide):
            following = agent.act(acknowledged)
        self.assertEqual(following.actions, [{"unit_id": 4, "command": "stop", "arguments": {}}])

    def test_realtime_sends_ready_orders_before_waiting_and_processes_each_observation(self):
        observed, waits = [], []
        action = {"unit_id": 3, "command": "stop", "arguments": {}}

        def decide(agent, obs, wait=False):
            self.assertFalse(wait)
            observed.append(obs["game_time_seconds"])
            return Step(actions=[action] if len(observed) == 1 else [])

        with patch.object(Agent, "act", decide), patch("wc3agent.agent.Event") as event:
            event.return_value.wait.side_effect = lambda _: waits.append(len(self.sessions[0].steps))
            play(replace(self.config, realtime=True), model=Model())
        self.assertEqual(waits, [1, 2])  # the ready first order was submitted before any wait
        self.assertEqual(observed, [30, 31, 32])
        self.assertEqual(self.sessions[0].steps[0][0][0], [action])

    def test_macro_reply_during_micro_is_applied_on_the_next_step(self):
        answered = threading.Event()

        class Commands:
            def complete(self, system, messages):
                answered.wait(2)
                return "stop #3", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Commands(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        self.addCleanup(answered.set)
        move = {"unit_id": 3, "command": "move", "arguments": {"x": 900, "y": 100}}

        def decide(*args, **context):
            if micro.call_count > 1:
                return [], []
            answered.set()
            agent.thinking[0].result(timeout=2)
            return [move], []

        with patch.object(agent.micro, "act", side_effect=decide) as micro:
            first = agent.act(observation())
            self.assertEqual(first.actions, [move])
            agent.submitted(first, [], 30)
            following = agent.act(observation(game_time_seconds=31))
            self.assertFalse(micro.call_args.kwargs["start"])  # no new questions before the reply's orders land
        self.assertEqual(following.actions, [{"unit_id": 3, "command": "stop", "arguments": {}}])

    def test_idle_workers_do_not_receive_orders_that_neither_model_issued(self):
        class Commands:
            def complete(self, system, messages):
                return "", {"seconds": 0}

        w = world()
        agent = Agent(w.catalog, w.map, Commands(), micro_model="jev", micro_key="")
        self.addCleanup(agent.close)
        for now in (30, 40, 50):
            step = agent.act(observation(game_time_seconds=now), wait=True)
            self.assertEqual(step.actions, [])
            agent.submitted(step, [], now)

    def test_game_is_closed_whatever_fails_and_a_model_error_is_recorded(self):
        original = Agent.close

        def fail_after_closing(agent):
            original(agent)
            raise RuntimeError("agent cleanup failed")

        for name, failure in (
            ("summary", patch.object(RunLog, "close", side_effect=OSError("disk full"))),
            ("cleanup", patch.object(Agent, "close", fail_after_closing)),
            ("model", patch.object(Model, "complete", side_effect=RuntimeError("model failed"))),
        ):
            with self.subTest(failure=name):
                with failure, self.assertRaises((OSError, RuntimeError)):
                    play(replace(self.config, out=self.out / name), model=Model())
                self.assertTrue(self.sessions[-1].closed)
        self.assertTrue((self.out / "cleanup" / "summary.json").is_file())
        summary = json.loads((self.out / "model" / "summary.json").read_text())
        self.assertEqual((summary["result"], summary["error"]), ("error", "model failed"))
