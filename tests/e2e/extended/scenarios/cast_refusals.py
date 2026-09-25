"""Casts the engine refuses: without mana and during cooldown nothing fires and no mana is spent."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View, stage
from wc3env.protocol import Action

HERO, ABILITY, ORDER = "Hamg", "AHwe", "waterelemental"
QUIET_TICKS = 4


class CastRefusals(ScenarioAgent):
    def __init__(self):
        super().__init__()
        mark = {}

        def hero(v: View):
            return v.first(HERO)

        def casts(v: View) -> int:
            return sum(e["kind"] == "spell_effect" and e.get("ability_id") == ABILITY for e in v.seen_events)

        def cast(mana):
            def act(v: View):
                mark.update(tick=v.tick, casts=casts(v))
                me = hero(v)["unit_id"]
                return [stage("mana", unit_id=me, value=mana), Action(me, "cast", {"order": ORDER})]

            return act

        def refused(v: View) -> bool:
            return v.tick >= mark["tick"] + QUIET_TICKS and casts(v) == mark["casts"]

        self.steps = [
            Step("hero_spawned", lambda v: [], lambda v: hero(v) is not None, timeout=5),
            Step(
                "learn",
                lambda v: [Action(hero(v)["unit_id"], "learn", {"ability_id": ABILITY})],
                lambda v: v.happened("hero_learn", ability_id=ABILITY),
                timeout=5,
            ),
            Step("no_mana", cast(0), refused, timeout=8),
            Step("control_casts", cast(250), lambda v: casts(v) == 1, timeout=8),
            Step("on_cooldown", cast(250), refused, timeout=8),
            Step("mana_kept", lambda v: [], lambda v: hero(v)["mana"] >= 240, timeout=2),
        ]
