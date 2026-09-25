"""Every unit's combat role and the one-line stats Jev reads for its type.

Roles come from data/unit_roles.json, which covers every unit in the game (tools/prepare/unit_roles.py).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

ROLES = ("tank", "melee", "ranged", "caster", "siege", "air", "caster hero", "melee hero", "summon", "ward", "worker")
TABLE = Path(__file__).with_name("data") / "unit_roles.json"
RANGED = 300.0  # attack range from which a unit or hero fights from behind the line


@cache
def _table():
    return json.loads(TABLE.read_text(encoding="utf-8"))["roles"]


def role(catalog, raw):
    """The type's role from the table, which lists every unit in the game; `stats_role` for any other."""
    return _table().get(raw) or stats_role(catalog.units.get(raw, {}))


def stats_role(unit):
    """A role from a unit's stats, for units without a hand-picked one."""
    if unit.get("builds"):
        return "worker"
    if "ward" in (c.lower() for c in unit.get("classifications", [])):
        return "ward"  # Healing Ward, Stasis Trap, Sentry Ward, Serpent Ward, Goblin Land Mine
    reach = unit.get("base_attack_range", 0)
    if unit.get("hero"):
        return "caster hero" if reach >= RANGED else "melee hero"
    if unit.get("movement_type") == "fly":
        return "air"
    if reach >= 1000:
        return "siege"
    if unit.get("mana") and reach >= RANGED:
        return "caster"
    if reach >= RANGED:
        return "ranged"
    return "tank" if unit.get("hp", 0) >= 1000 else "melee"


def type_line(catalog, raw):
    """'Footman (melee): 420 hp, armor 2 heavy, 12-13 damage every 1.35s (9.3/s) normal, range 90, speed 270'."""
    unit = catalog.units.get(raw, {})
    damage = unit.get("damage") or [0, 0]
    period = unit.get("base_attack_period") or 0
    per_second = (sum(damage) / 2 / period) if period else 0
    armor_class = {"large": "heavy", "small": "light", "fort": "fortified", "none": "unarmored"}.get(
        unit.get("base_armor_class"), unit.get("base_armor_class")
    )
    attack = {"pierce": "piercing"}.get(unit.get("base_attack_type"), unit.get("base_attack_type"))
    return (
        f"{unit.get('name', raw)} ({role(catalog, raw)}): {unit.get('hp', '?')} hp, armor {unit.get('armor', 0):g} "
        f"{armor_class}, {damage[0]:g}-{damage[1]:g} damage every {period:g}s ({per_second:.1f}/s) {attack}, "
        f"range {unit.get('base_attack_range', 0):g}, speed {unit.get('base_move_speed', 0):g}"
    )
