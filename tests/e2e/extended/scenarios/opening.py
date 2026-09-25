"""Any race's opening: a worker builds supply and production, which trains the first unit."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View
from wc3env.protocol import Action


class Opening(ScenarioAgent):
    def __init__(self, worker: str, hall: str, supply: str, production: str, unit: str):
        super().__init__()
        start = {}

        def remember(v: View):
            start.update(food_cap=v.food_cap, food_used=v.food_used)
            return []

        def build(type_id, dx, dy):
            def act(v: View):
                if v.happened("construct_start", type_id=type_id) or not v.own(worker):
                    return []  # re-issuing a build order restarts the construction
                hall_unit = v.first(hall)
                builder = v.own(worker)[-1]
                where = {"type_id": type_id, "x": hall_unit["x"] + dx, "y": hall_unit["y"] + dy}
                return [Action(builder["unit_id"], "build", where)]

            return act

        def built(type_id):
            return lambda v: v.happened("construct_finish", type_id=type_id) and bool(v.own(type_id))

        self.steps = [
            Step("rich", remember, lambda v: v.gold >= 5000 and bool(v.first(hall)), timeout=3),
            Step("supply", build(supply, 0, -600), built(supply), timeout=120, every=20),
            Step("food_cap_up", lambda v: [], lambda v: v.food_cap > start["food_cap"], timeout=5),
            Step("production", build(production, -700, -300), built(production), timeout=150, every=20),
            Step(
                "first_unit",
                lambda v: [Action(v.first(production)["unit_id"], "train", {"type_id": unit})],
                lambda v: v.happened("train_finish", type_id=unit) and bool(v.own(unit)),
                timeout=90,
            ),
            Step("food_used_up", lambda v: [], lambda v: v.food_used > start["food_used"], timeout=3),
        ]
