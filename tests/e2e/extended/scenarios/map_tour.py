"""Any melee map: the roster matches the setup, workers mine the nearest gold, a scout crosses the map."""

from __future__ import annotations

import math

from tests.e2e.scenarios.base import ScenarioAgent, Step, View
from wc3env.protocol import Action

GOLD_MINE = "ngol"


class MapTour(ScenarioAgent):
    def __init__(self, players: int):
        super().__init__()
        start = {}

        def hall(v: View):
            return next(u for u in v.units if u["structure"])

        def workers(v: View):
            return [u for u in v.units if not u["structure"] and not u["hero"]]

        def roster_ok(v: View) -> bool:
            active = [p for p in v.p["players"] if p["kind"] == "player" and p["active"]]
            computers = [p for p in active if p["controller"] == "computer" and p["relation"] == "enemy"]
            b = v.p["map"]["bounds"]
            inside = b["min_x"] < hall(v)["x"] < b["max_x"] and b["min_y"] < hall(v)["y"] < b["max_y"]
            return len(active) == players and len(computers) == players - 1 and inside and bool(v.p["destructables"])

        def mine(v: View):
            start.update(gold=v.gold)
            target = v.nearest(hall(v)["x"], hall(v)["y"], v.visible(GOLD_MINE))
            return [Action(w["unit_id"], "smart", {"target_id": target["unit_id"]}) for w in workers(v)[:3]]

        def scout(v: View):
            unit = workers(v)[-1]
            start.update(scout=unit["unit_id"], x=unit["x"], y=unit["y"])
            return [Action(unit["unit_id"], "move", {"x": 0.0, "y": 0.0})]

        def travelled(v: View) -> bool:
            unit = next((u for u in v.units if u["unit_id"] == start["scout"]), None)
            return unit is not None and math.dist((unit["x"], unit["y"]), (start["x"], start["y"])) > 1500

        self.steps = [
            Step("roster", lambda v: [], roster_ok, timeout=3),
            Step("mine_gold", mine, lambda v: v.gold > start["gold"], timeout=60),
            Step("scout", scout, travelled, timeout=60),
        ]
