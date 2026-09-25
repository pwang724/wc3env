"""A production queue: three units ordered back to back start and finish one at a time."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View
from wc3env.protocol import Action

BARRACKS, FOOTMAN, COST = "hbar", "hfoo", 135


class TrainQueue(ScenarioAgent):
    def __init__(self):
        super().__init__()

        def count(v: View, kind: str) -> int:
            return sum(e["kind"] == kind and e.get("type_id") == FOOTMAN for e in v.seen_events)

        def order(v: View):
            return [Action(v.first(BARRACKS)["unit_id"], "train", {"type_id": FOOTMAN})] * 3

        def paid(n):
            return lambda v: v.gold <= 5000 - COST * n

        def one_at_a_time(v: View) -> bool:
            return count(v, "train_start") <= count(v, "train_finish") + 1

        self.steps = [
            Step("barracks_ready", lambda v: [], lambda v: v.first(BARRACKS) is not None, timeout=5),
            Step(
                "queue_3",
                order,
                lambda v: paid(3)(v) and one_at_a_time(v) and v.first(BARRACKS)["queue"] == [FOOTMAN] * 3,
                timeout=4,
            ),
            Step("first_done", lambda v: [], lambda v: count(v, "train_finish") == 1 and one_at_a_time(v), timeout=40),
            Step(
                "all_done",
                lambda v: [],
                lambda v: (
                    count(v, "train_finish") == 3 and len(v.own(FOOTMAN)) == 3 and v.first(BARRACKS)["queue"] == []
                ),
                timeout=80,
            ),
        ]
