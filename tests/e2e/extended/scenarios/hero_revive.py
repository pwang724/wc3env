"""A hero trained at an altar dies and is revived there: refused while the death resolves, then paid for."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View, stage
from wc3env.protocol import Action

ALTAR, HERO = "halt", "Hamg"
QUIET_TICKS = 4


class HeroRevive(ScenarioAgent):
    def __init__(self):
        super().__init__()
        mark = {}

        def revive(v: View):
            mark.setdefault("gold", v.gold)
            mark["asked"] = v.tick
            return [Action(v.first(ALTAR)["unit_id"], "revive", {"target_id": mark["hero"]})]

        def kill(v: View):
            mark["hero"] = v.first(HERO)["unit_id"]
            return [stage("kill", unit_id=mark["hero"])]

        def too_early(v: View) -> bool:
            return v.tick >= mark["asked"] + QUIET_TICKS and v.gold == mark["gold"] and v.first(HERO) is None

        def back(v: View) -> bool:
            hero = v.first(HERO)
            return hero is not None and hero["unit_id"] == mark["hero"] and hero["hp"] > 0 and v.gold < mark["gold"]

        self.steps = [
            Step("altar_ready", lambda v: [], lambda v: v.first(ALTAR) is not None, timeout=5),
            Step(
                "train",
                lambda v: [Action(v.first(ALTAR)["unit_id"], "train", {"type_id": HERO})],
                lambda v: v.happened("train_finish", type_id=HERO) and v.first(HERO) is not None,
                timeout=90,
            ),
            Step("dies", kill, lambda v: v.happened("death", type_id=HERO) and v.first(HERO) is None, timeout=5),
            Step("refused_while_dying", revive, too_early, timeout=8),
            Step("revived", revive, back, timeout=150, every=10),
        ]
