"""Movement destinations from observed geometry; the model decides which tactic fits."""

from math import hypot

from .policies import ARRIVED, BACK_OFF_SECONDS, INVENTORY_SLOTS, LINE_GAP, NEAR_ENEMY, PICKUP_RANGE


def _mean(units):
    return {"x": sum(u["x"] for u in units) / len(units), "y": sum(u["y"] for u in units) / len(units)}


def _toward(origin, target, length):
    dx, dy = target["x"] - origin["x"], target["y"] - origin["y"]
    size = hypot(dx, dy) or 1.0
    return {"x": origin["x"] + dx / size * length, "y": origin["y"] + dy / size * length}


def _away(origin, threat, length):
    return _toward(origin, {"x": 2 * origin["x"] - threat["x"], "y": 2 * origin["y"] - threat["y"]}, length)


def maneuvers(unit, own, enemies, catalog):
    """{key: {x, y, label, meaning}} for one unit. `own` are the units fighting alongside it."""
    definition = catalog.units[unit["type_id"]]
    speed = definition["base_move_speed"]
    if speed <= 0:
        return {}
    near = [e for e in enemies if hypot(e["x"] - unit["x"], e["y"] - unit["y"]) < NEAR_ENEMY] or enemies
    moves = {}

    def offer(key, point, label, meaning):
        if hypot(point["x"] - unit["x"], point["y"] - unit["y"]) > ARRIVED:
            moves[key] = {"x": round(point["x"], 1), "y": round(point["y"], 1), "label": label, "meaning": meaning}

    if near:
        threat = _mean(near)
        offer("back_off", _away(unit, threat, speed * BACK_OFF_SECONDS), "Back off from the enemies",
              "Step straight away from the nearby enemies.")  # fmt: skip
        front = [u for u in own if u["unit_id"] != unit["unit_id"] and not u["structure"]]
        if front:
            line = _mean(front)
            offer("behind_line", _away(line, threat, LINE_GAP), "Get behind nearby allies",
                  "Move behind our nearby allies, away from the enemies.")  # fmt: skip
        target = min(near, key=lambda e: hypot(e["x"] - unit["x"], e["y"] - unit["y"]))
        offer("close_in", _away(target, unit, 120.0), "Close in behind the nearest enemy",
              "Move past the nearest enemy to block its escape.")  # fmt: skip
    return moves


def pickups(unit, obs, catalog):
    """Items on the ground a hero could walk over and take: [{item_id, label, meaning}]."""
    if not unit["hero"]:
        return []
    carried = sum(entry["unit_id"] == unit["unit_id"] for entry in obs.get("inventory", []))
    found = []
    for item in obs.get("items", []):
        definition = catalog.items.get(item["type_id"], {})
        instant = definition.get("class") == "PowerUp"  # tomes and runes work on pickup and need no slot
        if hypot(item["x"] - unit["x"], item["y"] - unit["y"]) > PICKUP_RANGE or (
            carried >= INVENTORY_SLOTS and not instant
        ):
            continue
        name = definition.get("name", item["type_id"])
        found.append(
            {
                "item_id": item["item_id"],
                "label": f"Pick up {name}",
                "meaning": f"Walk over and take it. {definition.get('description', '')}".strip(),
            }
        )
    return found
