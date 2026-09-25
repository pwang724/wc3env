"""Orders the hook accepts but the engine refuses: no gold, unmet tech, no food.

Acceptance only means queued (docs/specs/actions.md); these checks pin what happens next:
the order must leave no `train_start` and must not spend resources.
"""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View, stage
from wc3env.protocol import Action

BARRACKS, FOOTMAN, KNIGHT = "hbar", "hfoo", "hkni"
QUIET_TICKS = 5


class Rejections(ScenarioAgent):
    def __init__(self):
        super().__init__()
        mark = {}

        def starts(v: View) -> int:
            return sum(e["kind"] == "train_start" for e in v.seen_events)

        def train(type_id, *staging):
            def act(v: View):
                mark.update(tick=v.tick, starts=starts(v))
                return [*staging, Action(v.first(BARRACKS)["unit_id"], "train", {"type_id": type_id})]

            return act

        def refused(v: View) -> bool:
            return v.tick >= mark["tick"] + QUIET_TICKS and starts(v) == mark["starts"]

        self.steps = [
            Step("barracks_ready", lambda v: [], lambda v: v.first(BARRACKS) is not None, timeout=5),
            Step("control_trains", train(FOOTMAN), lambda v: starts(v) == 1 and v.gold < 5000, timeout=5),
            Step("control_finishes", lambda v: [], lambda v: v.happened("train_finish", type_id=FOOTMAN), timeout=40),
            Step("no_gold", train(FOOTMAN, stage("resources", player=0, gold=0, lumber=0)), refused, timeout=8),
            Step("gold_untouched", lambda v: [], lambda v: v.gold == 0, timeout=2),
            Step(
                "tech_not_met", train(KNIGHT, stage("resources", player=0, gold=5000, lumber=5000)), refused, timeout=8
            ),
            Step("tech_gold_untouched", lambda v: [], lambda v: v.gold == 5000, timeout=2),
            Step(
                "fill_food",
                lambda v: [stage("spawn", type_id=FOOTMAN, player=0, x=4300.0, y=2600.0, n=8)],
                lambda v: v.food_used >= v.food_cap,
                timeout=5,
            ),
            Step("no_food", train(FOOTMAN), refused, timeout=8),
            Step("food_gold_untouched", lambda v: [], lambda v: v.gold == 5000, timeout=2),
        ]
