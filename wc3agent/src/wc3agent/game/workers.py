"""Harvest capabilities; worker allocation is the macro model's decision."""

HARVEST = {
    "Ahar": ("ngol", True),
    "ANha": ("ngol", True),
    "Ahrl": (None, True),
    "Ahr2": (None, True),
    "Ahr3": (None, True),
    "Aaha": ("ugol", False),
    "Awha": ("egol", True),
    "Awh2": ("egol", True),
}
MINES = {"ngol", "ugol", "egol"}
MINE_CAPACITY = {"ugol": 5, "egol": 5}  # Acolytes or Wisps at once; more stand idle beside it


def harvest_kinds(unit):
    kinds = [HARVEST[a["ability_id"]] for a in unit.get("abilities", []) if a["ability_id"] in HARVEST]
    return {mine for mine, _ in kinds if mine}, any(lumber for _, lumber in kinds)


def is_worker(unit, catalog):
    return not unit["structure"] and (
        bool(catalog.units.get(unit["type_id"], {}).get("builds")) or any(harvest_kinds(unit))
    )
