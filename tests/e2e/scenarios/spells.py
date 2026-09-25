"""Staged: a spell cast and its effects on mana and units."""

from __future__ import annotations

from tests.e2e.scenarios import ids
from wc3env.protocol import Action

from .base import ScenarioAgent, Step, View


def hero(v: View):
    return v.first(ids.ARCHMAGE)


class Spells(ScenarioAgent):
    def __init__(self):
        super().__init__()
        self.steps = [
            Step("hero_spawned", lambda v: [], lambda v: hero(v) is not None and hero(v)["max_mana"] > 0, timeout=5),
            Step(
                "learn",
                lambda v: [Action(hero(v)["unit_id"], "learn", {"ability_id": ids.SUMMON_WATER_ELEMENTAL})],
                lambda v: v.happened("hero_learn"),
                timeout=5,
            ),
            Step(
                "cast",
                lambda v: [Action(hero(v)["unit_id"], "cast", {"order": "waterelemental"})],
                lambda v: (
                    v.happened("spell_effect")
                    and bool(v.own(ids.WATER_ELEMENTAL))
                    and hero(v)["mana"] < hero(v)["max_mana"]
                ),
                timeout=8,
            ),
            Step(
                "elemental_fights",
                lambda v: (
                    [
                        Action(u["unit_id"], "attack", {"target_id": v.visible("oshm")[0]["unit_id"]})
                        for u in v.own(ids.WATER_ELEMENTAL) + [hero(v)]
                    ]
                    if v.visible("oshm")
                    else []
                ),
                lambda v: v.happened("death"),
                timeout=40,
                every=4,
            ),
        ]
