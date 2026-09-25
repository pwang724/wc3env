"""Facts derived from observations, shared by the models and scenario scoring."""

from math import hypot

from .strength import observed_strength


def learnable(catalog, hero, learned):
    """Skills the hero can put a point into now, in the order the hero lists them: below their
    maximum rank, and with the hero level each next rank requires."""
    skills = catalog.units[hero["type_id"]]["potential_hero_abilities"]
    return [
        raw
        for raw in skills
        if learned.get(raw, 0) < catalog.abilities[raw].get("max_level", 3)
        and hero["level"]
        >= max(1, catalog.abilities[raw].get("required_level", 1))
        + learned.get(raw, 0) * (catalog.abilities[raw].get("level_skip") or 2)
    ]


def fight_summary(catalog, own, enemies):
    """Approximate strength, without prescribing whether to fight."""
    mine, theirs = round(observed_strength(own, catalog)), round(observed_strength(enemies, catalog))
    return {"your_strength": mine, "enemy_strength": theirs}


def cleared_camps(mapinfo, obs):
    """Camps currently in reach of an own unit with no living creeps nearby."""
    hostile = [u for u in obs["visible_enemies"] if u["owner"] == 12]
    mobile = [u for u in obs["units"] if not u["structure"]]
    return {
        camp["number"]
        for camp in mapinfo.camps
        if any(hypot(u["x"] - camp["x"], u["y"] - camp["y"]) < 450 for u in mobile)
        and not any(hypot(u["x"] - camp["x"], u["y"] - camp["y"]) < 900 for u in hostile)
    }


def learned_skills(catalog, obs):
    """Hero skill levels directly observed in the current abilities."""
    return {
        u["unit_id"]: {
            a["ability_id"]: a["level"]
            for a in u.get("abilities", [])
            if a["ability_id"] in catalog.units[u["type_id"]]["potential_hero_abilities"]
        }
        for u in obs["units"]
        if u["hero"] and u["type_id"] in catalog.units
    }
