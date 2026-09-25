"""Generate a reusable, versioned reference from the licensed installation."""

import csv
import re
from pathlib import Path

from wc3env.compatibility import verify_executable
from wc3env.settings import settings

from .artifacts import write_json
from .gamedata import RACES, read_all, slk
from .pathing import footprints
from .source_catalog import SourceCatalog, field, identifiers


def prepare_reference(output, game_dir=None):
    game_dir = Path(game_dir) if game_dir is not None else settings().game_dir
    verify_executable(game_dir / "Warcraft III.exe")  # the pinned build is the one the data describes
    catalog = SourceCatalog.load(game_dir)
    abilities = {}
    for raw, row in catalog.abilities.items():
        rank = max(
            [
                1,
                int(float(row.get("levels") or 1)),
                *[int(m[1]) for key in row if (m := re.fullmatch(r"Rng(\d+)", key))],
            ]
        )
        abilities[raw] = {
            "levels": {str(i): catalog.ability(raw, i) for i in range(1, rank + 1)},
            "max_level": int(float(row.get("levels") or 1)),
            # A hero may learn rank n+1 at hero level required_level + n * level_skip.
            "required_level": int(float(row.get("reqLevel") or 0)) if (row.get("reqLevel") or "").strip("-_ ") else 0,
            "level_skip": int(float(row.get("levelSkip") or 0)) if (row.get("levelSkip") or "").strip("-_ ") else 0,
        }
    buffs = {raw: {**catalog.buff(raw), "abilities": []} for raw in sorted(catalog.buffs)}
    for raw, ability in abilities.items():  # which abilities apply each buff, for its duration
        for buff in {b for level in ability["levels"].values() for b in identifiers(level["effects"].get("BuffID"))}:
            if buff in buffs:
                buffs[buff]["abilities"].append(raw)
    names = [r"Units\UpgradeData.slk"]
    names += [rf"Units\{race}{kind}.txt" for race in (*RACES, "Campaign") for kind in ("UnitFunc", "UpgradeFunc")]
    extra = read_all(names, game_dir)
    upgrades = slk(extra[r"Units\UpgradeData.slk"])
    upgrade_defs = {}
    for raw in sorted(set(upgrades) | set(catalog.upgrade_strings)):
        titles = next(csv.reader([field(catalog.upgrade_strings.get(raw, {}), "Name")]), [])
        upgrade_defs[raw] = {"names": [catalog.text(t) for t in titles], **catalog.upgrade(raw)}
    result = {
        "schema_version": 1,
        "units": {raw: catalog.unit(raw) for raw in sorted(catalog.units)},
        "footprints": footprints(catalog.units, game_dir),
        "abilities": abilities,
        "buffs": buffs,
        "items": {raw: catalog.item(raw) for raw in sorted(catalog.items)},
        "upgrades": upgrade_defs,
        "damage_multipliers": catalog.damage_multipliers,
    }
    write_json(output, result)
    print(
        f"Reference: {output} ({len(result['units'])} units, {len(abilities)} abilities, "
        f"{len(result['items'])} items, {len(upgrade_defs)} upgrades, {len(buffs)} buffs)"
    )
    return result
