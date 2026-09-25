"""Shift-queued construction: one worker is given two build orders at once and finishes both."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View
from wc3env.protocol import Action

WORKER, HALL, FARM = "hpea", "htow", "hhou"
SPOTS = ((0, -600), (-700, -300))


class BuildQueue(ScenarioAgent):
    def __init__(self):
        super().__init__()

        def count(v: View, kind: str) -> int:
            return sum(e["kind"] == kind and e.get("type_id") == FARM for e in v.seen_events)

        def order_both(v: View):
            hall, builder = v.first(HALL), v.own(WORKER)[-1]
            return [
                Action(
                    builder["unit_id"],
                    "build",
                    {"type_id": FARM, "x": hall["x"] + dx, "y": hall["y"] + dy, "queued": queued},
                )
                for (dx, dy), queued in zip(SPOTS, (False, True))
            ]

        self.steps = [
            Step("rich", lambda v: [], lambda v: v.gold >= 5000 and bool(v.first(HALL)), timeout=3),
            Step("first_started", order_both, lambda v: count(v, "construct_start") == 1, timeout=30),
            Step("second_started", lambda v: [], lambda v: count(v, "construct_start") == 2, timeout=90),
            Step("both_finished", lambda v: [], lambda v: count(v, "construct_finish") == 2, timeout=90),
            Step("food_cap_up", lambda v: [], lambda v: v.food_cap >= 24, timeout=3),
        ]
