"""Milestone 2: the complete Human worker loop with the real economy (no cheats).

gather gold -> gather lumber -> build a Farm -> build a Barracks -> train a Footman.
Build/train durations are checked against Warcraft's nominal times.
"""

from __future__ import annotations

from tests.e2e.scenarios import ids
from wc3env.protocol import Action

from .base import ScenarioAgent, Step, View

NOMINAL_SECONDS = {ids.FARM: 35, ids.BARRACKS: 60, ids.FOOTMAN: 20}


def _sign(view: View) -> float:
    """Bases are mirrored left/right on Echo Isles; flip x-offsets for the east start."""
    hall = view.first(ids.TOWN_HALL)
    return -1.0 if hall and hall["x"] > 0 else 1.0


def _spot(view: View, dx: float, dy: float) -> tuple[float, float]:
    hall = view.first(ids.TOWN_HALL)
    return hall["x"] + _sign(view) * dx, hall["y"] + dy


class WorkerLoop(ScenarioAgent):
    def __init__(self):
        super().__init__()
        self.start_gold = None
        self.start_lumber = None
        self.timing: dict[str, int] = {}
        self.workers: list[int] = []  # peasant ids fixed at the first tick; miners disappear inside the mine

        def gather_gold(v: View):
            self.start_gold, self.start_lumber = v.gold, v.lumber
            self.workers = [p["unit_id"] for p in v.own(ids.PEASANT)]
            hall = v.first(ids.TOWN_HALL)
            mine = v.nearest(hall["x"], hall["y"], v.visible(ids.GOLD_MINE))
            return [Action(uid, "smart", {"target_id": mine["unit_id"]}) for uid in self.workers[:2]]

        def gather_lumber(v: View):
            hall = v.first(ids.TOWN_HALL)
            # Echo Isles uses LTlt (Lordaeron summer trees). This map-specific
            # choice belongs in the scenario, not the observation protocol.
            tree = v.nearest(hall["x"], hall["y"], [d for d in v.p["destructables"] if d["type_id"] == "LTlt"])
            if tree is None:
                raise ValueError("worker_loop needs a visible LTlt tree")
            return [Action(uid, "harvest", {"target_id": tree["id"]}) for uid in self.workers[2:4]]

        def build(type_id, dx, dy):
            def act(v: View):
                x, y = _spot(v, dx, dy)
                return [Action(self.workers[4], "build", {"type_id": type_id, "x": x, "y": y})]

            return act

        def train_footman(v: View):
            return [Action(v.first(ids.BARRACKS)["unit_id"], "train", {"type_id": ids.FOOTMAN})]

        def finished(type_id):
            def done(v: View):
                if v.happened("construct_finish", type_id=type_id):
                    start = next(
                        e["tick"] for e in v.seen_events if e["kind"] == "construct_start" and e["type_id"] == type_id
                    )
                    end = next(
                        e["tick"] for e in v.seen_events if e["kind"] == "construct_finish" and e["type_id"] == type_id
                    )
                    self.timing[type_id] = end - start
                    return True
                return False

            return done

        def trained(type_id):
            def done(v: View):
                for e in v.seen_events:
                    if e["kind"] == "train_finish" and e["type_id"] == type_id:
                        self.timing[type_id] = e["tick"] - self._entered_tick
                        return True
                return False

            return done

        self.steps = [
            Step("gather_gold", gather_gold, lambda v: v.gold > self.start_gold, timeout=60),
            Step("gather_lumber", gather_lumber, lambda v: v.lumber > self.start_lumber, timeout=60),
            Step("build_farm", build(ids.FARM, 0, -640), finished(ids.FARM), timeout=90),
            Step("build_barracks", build(ids.BARRACKS, 768, -512), finished(ids.BARRACKS), timeout=120),
            Step("train_footman", train_footman, trained(ids.FOOTMAN), timeout=45),
            Step("food_accounts", lambda v: [], lambda v: v.food_used == 7 and v.food_cap == 18, timeout=3),
        ]

    def summary(self) -> dict:
        s = super().summary()
        s["timing_ticks"] = self.timing
        s["timing_ok"] = {
            k: abs(self.timing[k] - NOMINAL_SECONDS[k]) <= max(4, 0.15 * NOMINAL_SECONDS[k]) for k in self.timing
        }
        s["passed"] = s["passed"] and all(s["timing_ok"].values())
        return s
