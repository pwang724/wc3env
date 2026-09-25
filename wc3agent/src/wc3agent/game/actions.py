"""Observable Warcraft target constraints; no tactical preferences."""

# Spells that heal one kind and hurt the other; the game refuses them on any other target (Death Coil on an
# enemy Lich). Value: the kind a friendly target must be; an enemy target must be the other kind.
LIFE_SIDED = {"AUdc": "undead", "ACdc": "undead", "AHhb": "living", "AIhl": "living"}
# Buffs whose game data names no side, so the game lets them land on enemies too (Unholy Frenzy went on an
# enemy Death Knight); only ours are worth it.
FRIEND_ONLY = {"Auhf"}


def target_matches(caster, target, ability, catalog):
    """Reject restrictions observable in the snapshot; the engine checks the rest."""
    tags = {tag.lower() for tag in ability.get("targets", [])}
    allied = target["owner"] == caster["owner"]  # live battle controls a single side
    if "enemy" in tags and not tags & {"friend", "allies", "self", "player"} and allied:
        return False
    if tags & {"friend", "allies", "player", "self"} and not tags & {"enemy", "neutral"} and not allied:
        return False
    # 'neutral' also admits friendly neutral units; it does not turn a friend-only spell hostile.
    if "friend" in tags and "enemy" not in tags and not allied:
        return False
    # Only spells listing 'self' reach their own caster: Invisibility (friend, no self) cannot hide the Sorceress.
    if caster["unit_id"] == target["unit_id"] and "self" not in tags:
        return False
    data = catalog.unit(target["type_id"])
    if ability.get("ability_id") in FRIEND_ONLY and not allied:
        return False
    heals = LIFE_SIDED.get(ability.get("ability_id"))
    if heals:
        undead = "undead" in {tag.lower() for tag in data.get("classifications", [])}
        if undead != ((heals == "undead") == allied):  # a friend of the kind it heals, or an enemy of the other
            return False
    flying = data.get("movement_type") == "fly"
    if ("ground" in tags and "air" not in tags and flying) or ("air" in tags and "ground" not in tags and not flying):
        return False
    if "organic" in tags and "mechanical" in {tag.lower() for tag in data.get("classifications", [])}:
        return False
    if "structure" in tags and not tags & {"ground", "air", "organic", "mechanical"} and not target["structure"]:
        return False
    if "nonstructure" in tags and target["structure"]:
        return False
    if "nonhero" in tags and target["hero"]:
        return False
    return True
