"""Reports expose the actual conversation and distinguish observations from rejection."""

import json
import tempfile
import unittest
from pathlib import Path

from wc3agent.macro.agent import MacroAgent
from wc3agent.macro.conversation import Conversation
from wc3agent.report import macro_call, report


def call(turn=1):
    return dict(
        kind="macro",
        turn=turn,
        at_game_time=30,
        landed_at_game_time=31,
        latency_ms=1000,
        observation="obs",
        reply="train #2 Footman",
        actions=[],
        problems=[],
        told=[],
    )


class ReportTests(unittest.TestCase):
    def test_completed_session_without_model_calls_still_reports_result_and_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder)
            summary = dict(
                result="defeat",
                game_seconds=6.9,
                metrics=[dict(metric="hero_alive", measured=False, op="==", value=True, ok=False)],
            )
            (session / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            target = report(session)
            page = target.read_text(encoding="utf-8")
            self.assertEqual(target, session / "report.html")
            self.assertIn("defeat", page)
            self.assertIn("0:06.9", page)
            self.assertIn("hero_alive", page)
            self.assertNotIn('id="macro-turn-', page)
            self.assertNotIn('id="micro-call-', page)

    def test_missing_calls_do_not_hide_invalid_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder)
            for invalid in (session, session / "nonexistent"):
                with self.subTest(session=invalid), self.assertRaises(FileNotFoundError):
                    report(invalid)
            self.assertFalse((session / "report.html").exists())
            (session / "summary.json").write_text("not valid JSON", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                report(session)
            self.assertFalse((session / "report.html").exists())

    def test_recorded_request_preserves_pinned_goal_and_actual_truncation(self):
        class Model:
            def complete(self, system, messages):
                self.messages = messages
                return "reply", {}

        model = Model()
        conversation = Conversation(token_limit=60, keep_turns=1, pinned="test goal")
        for i in range(5):
            conversation.append(f"obs {i} " + "x" * 80, f"old reply {i}")
        macro = MacroAgent(model, "system", lambda r, o: ([], []), conversation)
        turn = macro.finish({}, *macro.think("new observation"))
        self.assertEqual(turn.record["request"]["messages"][1:], model.messages)
        page = macro_call({**call(), "request": turn.record["request"]})
        self.assertIn("test goal", page)
        self.assertIn("old reply 4", page)
        self.assertNotIn("old reply 0", page)

    def test_html_escapes_raw_messages_and_keeps_outcome_on_originating_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder)
            request = {"messages": [{"role": "user", "content": "<script>alert(1)</script>"}]}
            calls = [{**call(), "request": request}, call(2)]
            (session / "calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls), encoding="utf-8")
            outcome = dict(
                id=1,
                turn=1,
                ordered_at=31,
                at_game_time=35,
                label="train Footman at #2",
                status="not_observed",
                evidence="Producer was constructing; no queue entry.",
            )
            (session / "outcomes.jsonl").write_text(json.dumps(outcome), encoding="utf-8")
            page = report(session).read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)", page)
            self.assertIn("&lt;script&gt;", page)
            self.assertLess(page.index("Producer was constructing"), page.index('id="macro-turn-2"'))

    def test_async_micro_calls_stay_under_the_macro_turn_active_at_request_time(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder)
            # Micro replies arrive out of order, including across a macro reply.
            calls = [
                {**call(), "at_game_time": 10, "landed_at_game_time": 20},
                dict(kind="micro", call_index=0, turn=1, at_game_time=9, landed_at_game_time=21),
                dict(kind="micro", call_index=2, turn=1, at_game_time=35, landed_at_game_time=36),
                {**call(2), "at_game_time": 30, "landed_at_game_time": 40},
                dict(kind="micro", call_index=3, turn=2, at_game_time=41, landed_at_game_time=42),
                dict(kind="micro", call_index=1, turn=2, at_game_time=25, landed_at_game_time=45),
            ]
            (session / "calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls), encoding="utf-8")
            (session / "summary.json").write_text(json.dumps({"game_seconds": 50}), encoding="utf-8")
            page = report(session).read_text(encoding="utf-8")
            self.assertLess(page.index('id="micro-call-1"'), page.index('id="macro-turn-1"'))
            under_first = page[page.index('data-macro-turn="1"') : page.index('id="macro-turn-2"')]
            self.assertLess(under_first.index('id="micro-call-2"'), under_first.index('id="micro-call-3"'))
            self.assertNotIn('id="micro-call-4"', under_first)
            self.assertLess(page.index('data-macro-turn="2"'), page.index('id="micro-call-4"'))
            self.assertIn("0:09.0 → 0:21.0", page)
            self.assertIn("0:10.0 → 0:20.0", page)
            self.assertIn("0:25.0 → 0:45.0", page)
