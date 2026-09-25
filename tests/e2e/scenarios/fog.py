"""Staged: fog of war as the game reports it (IsUnitVisible for player 0)."""

from __future__ import annotations

from tests.e2e.scenarios import ids

from .base import ScenarioAgent, Step, stage

GRUNT = "ogru"


class Fog(ScenarioAgent):
    def __init__(self):
        super().__init__()
        self.steps = [
            Step("far_grunts_hidden", lambda v: [], lambda v: len(v.visible(GRUNT)) == 0, timeout=3),
            Step(
                "scout_reveals",
                lambda v: [stage("spawn", type_id=ids.FOOTMAN, player=0, x=-3700, y=-2000)],
                lambda v: len(v.visible(GRUNT)) >= 6,
                timeout=5,
            ),
            Step(
                "kill_in_view",
                lambda v: [stage("kill", unit_id=v.visible(GRUNT)[0]["unit_id"])],
                lambda v: v.happened("death") and len(v.visible(GRUNT)) <= 5,
                timeout=5,
            ),
            Step(
                "spawn_out_of_view_hidden",
                lambda v: [stage("spawn", type_id=GRUNT, player=1, x=-4000, y=3000, n=4)],
                lambda v: len(v.visible(GRUNT)) <= 5,
                timeout=3,
            ),
            Step(
                "scout_dies_vision_lost",
                lambda v: [stage("kill", unit_id=v.own(ids.FOOTMAN)[0]["unit_id"])],
                lambda v: len(v.visible(GRUNT)) == 0,
                timeout=8,
            ),
        ]
