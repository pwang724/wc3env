"""Staged fight for observation parity: armies spawned by staging ops (the config's
`setup`) clash next to the human base. Exercises hp loss, deaths, mana use, a leveled hero,
a ground item and units out of sight, in about a minute of game time."""

from __future__ import annotations

from tests.e2e.scenarios import ids
from wc3env.protocol import Action

from .base import ScenarioAgent, Step, View

GRUNT = "ogru"


class StagedFight(ScenarioAgent):
    def __init__(self):
        super().__init__()
        self.enemy_start = 0

        def armies_spawned(v: View):
            return len(v.own(ids.FOOTMAN)) >= 12 and len(v.visible(GRUNT)) >= 12 and v.first(ids.ARCHMAGE) is not None

        def level_hero(v: View):
            h = v.first(ids.ARCHMAGE)
            return [Action(h["unit_id"], "learn", {"ability_id": ids.SUMMON_WATER_ELEMENTAL})]

        def attack(v: View):
            grunts = v.visible(GRUNT)
            if not self.enemy_start:
                self.enemy_start = len(grunts)
            if not grunts:  # re-issued every 10 ticks: the enemy may be dead or out of sight by then
                return []
            tx, ty = grunts[0]["x"], grunts[0]["y"]
            orders = [Action(u["unit_id"], "attack", {"x": tx, "y": ty}) for u in v.own(ids.FOOTMAN)]
            h = v.first(ids.ARCHMAGE)
            if h:
                orders.append(Action(h["unit_id"], "cast", {"order": "waterelemental"}))
            return orders

        def fought(v: View):
            """A real fight, whichever side is winning: the game's random seed differs per launch, so
            the outcome does. Six deaths seen, and either side's grunt or footman count halved."""
            deaths = sum(1 for e in v.seen_events if e["kind"] == "death")
            return deaths >= 6 and (len(v.visible(GRUNT)) <= self.enemy_start // 2 or len(v.own(ids.FOOTMAN)) <= 6)

        self.steps = [
            Step("armies_spawned", lambda v: [], armies_spawned, timeout=5),
            Step("fog_hides_far_grunts", lambda v: [], lambda v: len(v.visible(GRUNT)) < 18, timeout=1),
            Step("hero_learns", level_hero, lambda v: v.happened("hero_learn"), timeout=5),
            Step("fight", attack, fought, timeout=90, every=10),
        ]
