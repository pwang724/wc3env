"""Macro conversation retention and handling of unusable replies."""

import unittest

from wc3agent.macro.agent import MacroAgent
from wc3agent.macro.conversation import Conversation


class MacroTest(unittest.TestCase):
    def test_truncation_keeps_the_pinned_goal_and_the_latest_turns(self):
        talk = Conversation(token_limit=60, keep_turns=2, pinned="THIS GAME IS A TEST: pick up the items.")
        for i in range(6):
            talk.append(f"obs {i} " + "x" * 80, f"reply {i}")
        sent = talk.messages("now")
        self.assertEqual(len(talk.turns), 6)
        self.assertGreater(talk.first, 0)
        self.assertEqual(sent[0]["content"], "THIS GAME IS A TEST: pick up the items.")
        self.assertEqual(sent[-2]["content"], "reply 5")
        self.assertEqual(sent[-1], {"role": "user", "content": "now"})

    def test_empty_completion_is_visible_feedback_instead_of_a_successful_idle_decision(self):
        agent = MacroAgent(None, "system", lambda text, obs: ([], []), Conversation())
        record = {"response": {"choices": [{"finish_reason": "length"}]}}
        turn = agent.finish({}, "current state", "", record)
        self.assertEqual(turn.actions, [])
        self.assertEqual(len(turn.notes), 1)
        self.assertIn("length", turn.notes[0])
