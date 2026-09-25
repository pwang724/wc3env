"""Economy for any race: workers bring in gold from the starting mine and lumber from the nearest tree."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View
from wc3env.protocol import Action

MINES = ("ngol", "ugol", "egol")  # neutral, haunted (Undead), entangled (Night Elf)


class Economy(ScenarioAgent):
    def __init__(self, gold_worker: str, lumber_worker: str, hall: str):
        super().__init__()
        start = {}

        def mine(v: View):
            start.update(gold=v.gold, lumber=v.lumber)
            home = v.first(hall)
            mines = [u for u in v.units + v.enemies if u["type_id"] in MINES]
            target = v.nearest(home["x"], home["y"], mines)
            return [Action(w["unit_id"], "smart", {"target_id": target["unit_id"]}) for w in v.own(gold_worker)[:2]]

        def chop(v: View):
            worker = v.own(lumber_worker)[-1]
            tree = v.nearest(worker["x"], worker["y"], v.p["destructables"])
            return [Action(worker["unit_id"], "harvest", {"target_id": tree["id"]})]

        self.steps = [
            Step("started", lambda v: [], lambda v: bool(v.first(hall) and v.own(gold_worker)), timeout=3),
            Step("gold", mine, lambda v: v.gold > start["gold"], timeout=60),
            Step("lumber", chop, lambda v: v.lumber > start["lumber"], timeout=90),
        ]
