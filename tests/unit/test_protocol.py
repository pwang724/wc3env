"""Action snapshots preserve JSON types and fail before any part of a round is sent."""

import unittest

from tests.fakes import client
from wc3env.protocol import Action, Observation, ProtocolError, normalize_actions, validate_actions


class ProtocolTest(unittest.TestCase):
    def test_snapshot_is_independent_without_coercing_values(self):
        args = {"nested": [None, True, 1, 1.0, {"text": "\U0001f600"}]}
        action = normalize_actions([Action(1, "stop", args)])[0]
        self.assertEqual(action.arguments, args)
        self.assertIs(type(action.arguments["nested"][2]), int)
        self.assertIs(type(action.arguments["nested"][3]), float)
        args["nested"][4]["text"] = "changed"
        self.assertEqual(action.arguments["nested"][4]["text"], "\U0001f600")

    def test_values_that_json_cannot_carry_exactly_are_rejected(self):
        cyclic = []
        cyclic.append(cyclic)
        nested = 0
        # Arguments are at depth 5 within the RPC envelope.
        for _ in range(59):
            nested = [nested]
        normalize_actions([Action(1, "stop", {"value": nested})])
        for value in (
            (1, 2),
            {1: "integer key"},
            float("nan"),
            float("inf"),
            2**63,
            -(2**63) - 1,
            "\ud800",
            cyclic,
            [nested],
        ):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                normalize_actions([Action(1, "stop", {"value": value})])

    def test_host_allows_repeated_units_and_validates_queue_flags(self):
        c = client()
        c.create_game("m", [{"slot": 0, "control": "agent"}])
        obs = Observation.from_dict(c.observe(0))
        a = Action(1001, "move", {"x": 0, "y": 0})
        validate_actions(obs, [a, a])
        validate_actions(obs, [a, Action(1001, "move", {"x": 1, "y": 1, "queued": True})])
        for b in (Action(1001, "move", {"queued": 1}), Action(1001, "select", {"queued": True})):
            with self.subTest(b=b), self.assertRaises(ProtocolError):
                validate_actions(obs, [a, b])
