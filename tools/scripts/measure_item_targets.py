"""Measure, in the game, how every usable item is aimed: at nothing, at a unit, or at a point.

The game data does not say (a Scroll of Healing lists targets but is instant; a ward lists none but
needs a point), so each item is given to a fresh hero and used every way until the game accepts it
(an `item_use` event). Writes wc3agent/src/wc3agent/game/data/item_targets.json.

    python -m tools.scripts.measure_item_targets
"""

from __future__ import annotations

import json
from pathlib import Path

from wc3env.env import GameConfig, WC3Env
from wc3env.protocol import Action
from wc3env.session import PlayerConfig

DATA = Path(__file__).resolve().parents[2] / "wc3agent/src/wc3agent/game/data"
HERO = "Hpal"
# Items the bench cannot exercise, with the reason; marked "assumed" in the output.
ASSUMED = {
    "Tiny Great Hall": "point", "Tiny Castle": "point", "Tiny Farm": "point", "Tiny Barracks": "point",
    "Tiny Blacksmith": "point", "Ivory Tower": "point",  # need buildable ground
    "Scroll of Resurrection": "none", "Tome of Retraining": "none",  # need dead units / learned skills
    "Wand of Neutralization": "unit", "Wand of Shadowsight": "unit",  # need a buffed / suitable enemy
}  # fmt: skip


def main():
    items = json.loads((DATA / "reference.json").read_text(encoding="utf-8"))["items"]
    wanted = sorted(
        raw for raw, item in items.items() if item["usable"] and item["class"] not in ("PowerUp", "Campaign")
    )
    env = WC3Env(
        GameConfig(
            map="(2)EchoIsles.w3x",
            players=(PlayerConfig(0, "human", "agent"), PlayerConfig(1, None, "computer")),
            step_ms=500,
            sound=False,
        )
    )
    found = {}
    try:
        obs = env.reset()
        rpc = env.session.game.rpc
        rpc.debug("speed", factor=16.0)
        rpc.debug("render", on=0)
        rpc.debug("ai", player=1, paused=1)
        hall = next(u for u in obs["units"] if u["structure"])
        x, y = hall["x"] - 900, hall["y"] - 600
        ally = rpc.debug("spawn", type_id="hfoo", player=0, x=x + 150, y=y, n=1)["unit_ids"][0]
        foe = None
        rpc.debug("hp", unit_id=ally, value=200)
        for index, raw in enumerate(wanted):
            if foe is None or not any(u["unit_id"] == foe for u in obs["visible_enemies"]):
                # A caster, so mana-draining and dispelling items have something to work on; replaced if it dies.
                foe = rpc.debug("spawn", type_id="hsor", player=1, x=x + 300, y=y, n=1)["unit_ids"][0]
            hero = rpc.debug("spawn", type_id=HERO, player=0, x=x, y=y, n=1)["unit_ids"][0]
            rpc.debug("level", unit_id=hero, level=10)
            rpc.debug("hp", unit_id=hero, value=300)
            rpc.debug("mana", unit_id=hero, value=50)
            rpc.debug("give", unit_id=hero, type_id=raw)
            obs, _, _ = env.step([])
            slot = next((i["slot"] for i in obs["inventory"] if i["unit_id"] == hero and i["type_id"] == raw), None)
            # A point before units: an order aimed at a unit also carries that unit's position, so an item
            # that wants a place would accept it and be misread as wanting a unit.
            attempts = [
                ("none", "", {}),
                ("point", "", {"x": x + 200.0, "y": y + 200.0}),
                ("point", "at own hall", {"x": hall["x"], "y": hall["y"]}),
                ("unit", "enemy", {"target_id": foe}),
                ("unit", "ally", {"target_id": ally}),
                ("unit", "self", {"target_id": hero}),
                ("unit", "own hall", {"target_id": hall["unit_id"]}),
            ]
            for form, target, arguments in attempts if slot is not None else []:
                obs, _, _ = env.step([Action(hero, "use_item", {"slot": slot, **arguments})])
                used = any(e["kind"] == "item_use" and e["unit_id"] == hero for e in obs["events"])
                if not used:  # the event can land a step after the order
                    obs, _, _ = env.step([])
                    used = any(e["kind"] == "item_use" and e["unit_id"] == hero for e in obs["events"])
                if used:
                    found[raw] = {"name": items[raw]["name"], "form": form, **({"target": target} if target else {})}
                    break
            else:
                found[raw] = {
                    "name": items[raw]["name"],
                    "form": None,
                    "note": "not given" if slot is None else "no use accepted",
                }
            if found[raw]["form"] is None and items[raw]["name"] in ASSUMED:
                found[raw] = {"name": items[raw]["name"], "form": ASSUMED[items[raw]["name"]], "assumed": True}
            rpc.debug("remove", unit_id=hero)
            print(f"{index + 1}/{len(wanted)} {raw} {found[raw]}", flush=True)
    finally:
        env.close()
    result = {
        "about": "How each usable item is aimed, measured in the game by tools/scripts/measure_item_targets.py: "
        "none = used at once, unit = needs a target unit (which kind was accepted), point = needs a place. "
        "form null: no way of using it was accepted in the test (it may need a situation the test lacks, such as a corpse).",
        "items": found,
    }
    (DATA / "item_targets.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    forms = [v["form"] for v in found.values()]
    print({form: forms.count(form) for form in set(forms)})


if __name__ == "__main__":
    main()
