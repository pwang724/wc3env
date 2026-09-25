"""Helpers over agent-visible observations, independent of any army composition."""

import math

NEUTRAL_PASSIVE = 15


def troops(obs):
    return [u for u in obs["units"] if not u["structure"] and u["hp"] > 0]


def opponents(obs):
    relations = {p["id"]: p.get("relation") for p in obs.get("players", [])}
    return [
        u
        for u in obs["visible_enemies"]
        if u["hp"] > 0 and not u["structure"] and relations.get(u["owner"], "enemy") == "enemy"
    ]


def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def town_hall(obs, catalog, preferred=None):
    """Our observed hall, the base anchor; exact building placement belongs to the game engine."""
    halls = [
        u
        for u in obs["units"]
        if u["hp"] > 0 and "TownHall" in catalog.units.get(u["type_id"], {}).get("classifications", [])
    ]
    return next((u for u in halls if u["unit_id"] == preferred), halls[0] if halls else None)


def targets(obs):
    """Ids of everything an order can currently aim at: living units, trees and ground items."""
    return (
        {u["unit_id"] for u in obs["units"] + obs["visible_enemies"] if u["hp"] > 0}
        | {d["id"] for d in obs["destructables"] if d["hp"] > 0}
        | {i["item_id"] for i in obs["items"]}
    )


def trading_shops(obs, catalog):
    """Finished shops we may trade with: our own, allied and neutral ones."""
    trading = {
        p["id"]
        for p in obs.get("players", [])
        if p.get("relation") in ("self", "ally", "neutral") and p.get("kind") != "neutral_hostile"
    }
    trading |= {obs.get("observer", 0), NEUTRAL_PASSIVE}
    return [
        u
        for u in obs["units"] + obs["visible_enemies"]
        if u["hp"] > 0
        and u.get("state") != "constructing"
        and catalog.units.get(u["type_id"], {}).get("sells_items")
        and u["owner"] in trading
    ]
