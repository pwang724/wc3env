"""Events the everyday suite does not reach: research, structure upgrade, shop sale, pickup, level, attacked."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View, stage
from wc3env.protocol import Action

HALL, KEEP, SMITH, SWORDS, HERO, SHOP, POTION, CLAWS = "htow", "hkee", "hbla", "Rhme", "Hamg", "ngme", "phea", "ratc"


class Events(ScenarioAgent):
    def __init__(self):
        super().__init__()

        def hero(v: View):
            return v.first(HERO)

        def ground_item(v: View):
            return next((i for i in v.p["items"] if i["type_id"] == CLAWS), None)

        def carried(v: View):
            return [s["type_id"] for s in v.p["inventory"] if s["unit_id"] == hero(v)["unit_id"]]

        self.steps = [
            Step("staged", lambda v: [], lambda v: bool(hero(v) and v.first(SMITH) and ground_item(v)), timeout=5),
            Step(
                "research",
                lambda v: [Action(v.first(SMITH)["unit_id"], "research", {"type_id": SWORDS})],
                lambda v: v.happened("research_finish", type_id=SWORDS),
                timeout=90,
            ),
            Step(
                "upgrade_hall",
                lambda v: [Action(v.first(HALL)["unit_id"], "train", {"type_id": KEEP})],
                lambda v: v.happened("upgrade_finish", type_id=KEEP) and bool(v.first(KEEP)),
                timeout=180,
            ),
            Step(
                "pickup",
                lambda v: [Action(hero(v)["unit_id"], "smart", {"target_id": ground_item(v)["item_id"]})],
                lambda v: v.happened("item_pickup", type_id=CLAWS) and CLAWS in carried(v),
                timeout=20,
            ),
            Step(
                "level_up",
                lambda v: [stage("level", unit_id=hero(v)["unit_id"], level=3)],
                lambda v: v.happened("hero_level", level=3) and hero(v)["level"] == 3,
                timeout=5,
            ),
            Step(
                "attacked",
                lambda v: [
                    Action(hero(v)["unit_id"], "attack", {"target_id": u["unit_id"]}) for u in v.visible("oshm")[:1]
                ],
                lambda v: v.happened("attacked"),
                timeout=30,
                every=10,
            ),
        ]
