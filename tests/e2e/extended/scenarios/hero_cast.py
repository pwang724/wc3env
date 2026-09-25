"""A caster learns a skill if needed and casts it: no target, a unit target or a ground point."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View
from wc3env.protocol import Action


class HeroCast(ScenarioAgent):
    def __init__(
        self,
        hero: str,
        ability: str,
        order: str,
        target: str | None = None,
        summon: str | None = None,
        at_point: bool = False,
        learn: bool = True,
        harms: bool = True,
    ):
        """`target` is an enemy type to cast on; `at_point` aims at its position instead of the unit.

        With `harms` its hp or mana must drop. `summon` is a type that must appear. Heroes `learn` first.
        """
        super().__init__()
        before = {}

        def me(v: View):
            return v.first(hero)

        def victim(v: View):
            found = v.visible(target)
            return max(found, key=lambda u: u["mana"]) if found else None

        def cast(v: View):
            arguments = {"order": order}
            if target:
                if victim(v) is None:
                    return []
                before.setdefault("victim", dict(victim(v)))
                if at_point:
                    arguments.update(x=before["victim"]["x"], y=before["victim"]["y"])
                else:
                    arguments["target_id"] = before["victim"]["unit_id"]
            return [Action(me(v)["unit_id"], "cast", arguments)]

        def landed(v: View):
            if not v.happened("spell_effect", ability_id=ability) or me(v)["mana"] >= me(v)["max_mana"]:
                return False
            if summon and not (v.own(summon) and v.happened("summon", type_id=summon)):
                return False
            if target and harms:
                now = next((u for u in v.enemies if u["unit_id"] == before["victim"]["unit_id"]), None)
                hurt = now is None or now["hp"] < before["victim"]["hp"] or now["mana"] < before["victim"]["mana"]
                return hurt
            return True

        self.steps = [
            Step("hero_spawned", lambda v: [], lambda v: me(v) is not None and me(v)["max_mana"] > 0, timeout=5),
            Step(
                "learn",
                lambda v: [Action(me(v)["unit_id"], "learn", {"ability_id": ability})],
                lambda v: v.happened("hero_learn", ability_id=ability),
                timeout=5,
                skip=lambda v: not learn,
            ),
            Step("cast", cast, landed, timeout=12, every=4),
        ]
