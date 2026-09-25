import unittest

from tests.e2e.scenarios.base import ScenarioAgent, Step
from wc3env.protocol import Action, Observation


def obs(tick, gold=500, events=()):
    return Observation.from_dict(
        {
            "protocol_version": 1,
            "sequence": tick,
            "game_time_seconds": float(tick),
            "player": {"gold": gold, "lumber": 0, "food_used": 0, "food_cap": 0},
            "units": [{"unit_id": 1, "type_id": "hpea", "x": 0, "y": 0}],
            "visible_enemies": [],
            "events": list(events),
            "result": "",
        }
    )


class TwoStepScenario(ScenarioAgent):
    def __init__(self):
        super().__init__()
        self.steps = [
            Step("wait_gold", lambda v: [Action(1, "stop", {})], lambda v: v.gold > 500, timeout=3),
            Step("wait_event", lambda v: [], lambda v: v.happened("death", unit_id=9), timeout=2),
        ]


class ScenarioTest(unittest.TestCase):
    def test_steps_run_in_order_and_record_checks(self):
        s = TwoStepScenario()
        self.assertEqual(len(s.act(obs(1))), 1)  # entered step 1, actions issued once
        self.assertEqual(s.act(obs(2)), [])  # still waiting
        s.act(obs(3, gold=600))  # step 1 passes, step 2 entered
        s.act(obs(4, events=[{"kind": "death", "unit_id": 9, "type_id": "hpea", "owner": 0}]))
        self.assertTrue(s.finished)
        self.assertEqual([c.status for c in s.checks], ["pass", "pass"])
        self.assertTrue(s.summary()["passed"])

    def test_timeout_fails_step_and_continues(self):
        s = TwoStepScenario()
        for t in range(1, 10):
            s.act(obs(t))
        self.assertEqual(s.checks[0].status, "fail")
        self.assertIn("timeout", s.checks[0].note)
        self.assertEqual(s.checks[1].status, "fail")
        self.assertFalse(s.summary()["passed"])

    def test_step_skip_and_retry(self):
        calls = []

        class S(ScenarioAgent):
            def __init__(self):
                super().__init__()
                self.steps = [
                    Step("skipped", lambda v: [], lambda v: False, skip=lambda v: True),
                    Step("retry", lambda v: calls.append(v.tick) or [], lambda v: len(calls) >= 3, every=2, timeout=10),
                ]

        s = S()
        for t in range(1, 9):
            s.act(obs(t))
        self.assertEqual([c.status for c in s.checks], ["skipped", "pass"])
        self.assertEqual(calls, [1, 3, 5])
        self.assertTrue(s.summary()["passed"])


if __name__ == "__main__":
    unittest.main()
