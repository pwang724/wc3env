"""A rough fighting value per unit, summed for armies and creep camps.

strength = sqrt(effective hit points x damage per second), so ten Footmen are worth ten times one
Footman and a unit with twice the health and twice the damage is worth twice as much.

From the game's data: hit points, armor (6% effective health per point), damage and attack period,
how well the unit's armor class holds up against the four common attack types (hero armor takes half
damage from pierce, magic and siege), and a hero's attributes at its level (25 hit points per
strength, 0.3 armor per agility, one damage per primary attribute point).

A guess, not data: what a hero's spells are worth (`HERO_SPELLS`). Nothing accounts for range, splash,
summons, auras, upgrades or control. It is a comparison aid, not a combat simulation; measuring
real fights in the environment is the way to calibrate it.
"""

from math import sqrt

ARMOR_FACTOR = 0.06  # Warcraft's damage reduction constant: each point adds 6% effective health
COMMON_ATTACKS = ("normal", "pierce", "magic", "siege")
HERO_SPELLS = (1.5, 0.15)  # a hero's value is multiplied by 1.5 at level 1 and 0.15 more per level: a guess


def hero_numbers(definition, level):
    """(hit points, armor, bonus damage) of a hero at `level`, from its attributes and their growth."""
    a, gained = definition["attributes"], max(level, 1) - 1
    points = {k: a[k] + a[k + "_per_level"] * gained for k in ("str", "agi", "int")}
    hp = definition["hp"] + 25 * a["str_per_level"] * gained
    armor = definition["armor"] - 2 + 0.3 * points["agi"]
    return hp, armor, a[a["primary"] + "_per_level"] * gained if a["primary"] else 0.0


def unit_strength(definition, multipliers, hp=None, level=0):
    damage, period = definition["damage"], definition["base_attack_period"]
    if not damage or period <= 0 or definition["structure"]:
        return 0.0
    health, armor, bonus = definition["hp"], definition["armor"], 0.0
    if definition["hero"] and definition.get("attributes"):
        health, armor, bonus = hero_numbers(definition, level)
    health = health if hp is None else hp
    taken = [
        multipliers[attack].get(definition["base_armor_class"], 1.0)
        for attack in COMMON_ATTACKS
        if attack in multipliers
    ]
    toughness = len(taken) / sum(taken) if taken and sum(taken) else 1.0
    value = sqrt(health * (1 + ARMOR_FACTOR * max(armor, 0)) * toughness * (sum(damage) / 2 + bonus) / period)
    if definition["hero"]:
        value *= HERO_SPELLS[0] + HERO_SPELLS[1] * (max(level, 1) - 1)
    return value


def observed_strength(units, catalog):
    """Units as observed: current hit points and hero level count, so a wounded army is weaker."""
    return sum(
        unit_strength(catalog.units[u["type_id"]], catalog.damage_multipliers, u["hp"], u.get("level", 0))
        for u in units
        if u["type_id"] in catalog.units
    )


def camp_strength(camp, catalog):
    return sum(
        unit_strength(catalog.units[c["type_id"]], catalog.damage_multipliers)
        for c in camp["creeps"]
        if c["type_id"] in catalog.units
    )
