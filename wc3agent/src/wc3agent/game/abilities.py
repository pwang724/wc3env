"""Turn the game's ability ids into things a model can read.

The observation says what each own unit has, by id and with live numbers: `AHbz, level 1, 75 mana,
6s cooldown`. The reference says what `AHbz` is: Blizzard, aimed at a point, what it does, what it
needs. This joins the two and adds whether the spell can be cast right now.
"""


def describe_abilities(catalog, obs, tech):
    """{str(unit id): {unit_id, order, abilities, observed_at}} for own living non-structure units.

    Each ability is the reference entry at its level plus the game's live numbers, `missing_requirements`
    (research or structures still needed, checked against `tech`) and `ready` (nothing missing, off
    cooldown, mana enough). Ids the reference has no entry for, such as movement and inventory, are skipped.
    """
    result = {}
    for unit in obs["units"]:
        if unit["structure"] or unit["hp"] <= 0:
            continue
        abilities = []
        for live in unit.get("abilities", []):
            if live["ability_id"] not in catalog.abilities:
                continue
            try:
                ability = {**catalog.ability(live["ability_id"], live["level"]), **live}
            except ValueError:  # a level the reference does not describe
                continue
            ability["missing_requirements"] = [
                r for r in ability["requires"] if tech.get(r, 0) < ability["requirement_counts"].get(r, 1)
            ]
            ability["ready"] = (
                not ability["missing_requirements"]
                and ability["cooldown_remaining"] <= 0
                and unit["mana"] >= ability["mana_cost"]
            )
            abilities.append(ability)
        result[str(unit["unit_id"])] = {
            "unit_id": unit["unit_id"],
            "order": str(unit["order"]["name"])
            if unit["order"]
            else None,  # an order the game has no name for arrives as its id
            "abilities": abilities,
            "observed_at": obs["game_time_seconds"],
        }
    return result
