"""Extract what a melee map places before the game starts: start locations, gold mines, creep camps, shops.

The source is the map's `war3mapUnits.doo` (The Frozen Throne layout, version 8). Names, levels and
what a building sells come from the reference, so the output is a standalone description of the map.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from wc3env.settings import settings

from .artifacts import digest, write_json
from .mpq import Archive

NEUTRAL_HOSTILE, NEUTRAL_PASSIVE = 12, 15
USEFUL_WITHOUT_STOCK = {"nmrk", "nfoh", "nmoo", "nwgt"}  # marketplace, fountains, way gate; huts are scenery
CAMP_RADIUS = 700.0  # creeps closer than this to any member belong to the same camp
ITEM_CLASSES = dict(
    zip("ijklmnop", ("any", "permanent", "charged", "powerup", "artifact", "purchasable", "campaign", "misc"))
)


class Reader:
    def __init__(self, data: bytes):
        self.data, self.at = data, 0

    def take(self, fmt: str):
        values = struct.unpack_from("<" + fmt, self.data, self.at)
        self.at += struct.calcsize("<" + fmt)
        return values if len(values) > 1 else values[0]

    def raw(self) -> str:
        value = self.data[self.at : self.at + 4]
        self.at += 4
        return value.decode("latin1")


def placed_units(data: bytes) -> list[dict]:
    r = Reader(data)
    if r.raw() != "W3do" or r.take("ii")[0] != 8:
        raise RuntimeError("war3mapUnits.doo is not The Frozen Throne version 8")
    units = []
    for _ in range(r.take("i")):
        type_id = r.raw()
        r.take("i")  # variation
        x, y, _z = r.take("fff")
        r.take("ffff")  # angle, scale
        r.take("B")  # flags
        owner = r.take("i")
        r.take("BB")
        r.take("ii")  # hp, mana
        r.take("i")  # map item table
        drops = []
        for _ in range(r.take("i")):
            drops.append([{"item": r.raw(), "chance": r.take("i")} for _ in range(r.take("i"))])
        gold = r.take("i")
        r.take("f")  # acquisition range
        r.take("iiii")  # hero level, strength, agility, intelligence
        for _ in range(r.take("i")):
            r.take("i4s")
        for _ in range(r.take("i")):
            r.take("4sii")
        kind = r.take("i")
        if kind == 0:
            r.take("i")
        elif kind == 1:
            r.take("ii")
        elif kind == 2:
            for _ in range(r.take("i")):
                r.take("4si")
        r.take("iii")  # colour, waygate, creation number
        units.append({"type_id": type_id, "x": x, "y": y, "owner": owner, "gold": gold, "drops": drops})
    return units


def drop_label(item: str, items: dict) -> str:
    """`YkI5`: a random level 5 charged item. Anything else is a fixed item."""
    if item[0] == "Y" and item[2] == "I":
        return f"random level {item[3]} {ITEM_CLASSES.get(item[1], 'any')} item"
    return items.get(item, {}).get("name", item)


def camps(creeps: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for creep in creeps:
        near = [
            g
            for g in groups
            if any((m["x"] - creep["x"]) ** 2 + (m["y"] - creep["y"]) ** 2 < CAMP_RADIUS**2 for m in g)
        ]
        merged = [creep] + [m for g in near for m in g]
        groups = [g for g in groups if g not in near] + [merged]
    return groups


def describe(units: list[dict], reference: dict) -> dict:
    catalog, items = reference["units"], reference["items"]

    def name(type_id):
        return catalog.get(type_id, {}).get("name", type_id)

    def at(u):
        return {"x": round(u["x"]), "y": round(u["y"])}

    result = {"start_locations": [], "gold_mines": [], "creep_camps": [], "neutral_buildings": []}
    for u in units:
        if u["type_id"] == "sloc":
            result["start_locations"].append({"player": u["owner"], **at(u)})
        elif u["type_id"] == "ngol":
            result["gold_mines"].append({**at(u), "gold": u["gold"]})
        elif u["owner"] == NEUTRAL_PASSIVE and catalog.get(u["type_id"], {}).get("structure"):
            info = catalog[u["type_id"]]
            if not (info["sells_items"] or info["sells_units"] or u["type_id"] in USEFUL_WITHOUT_STOCK):
                continue
            result["neutral_buildings"].append(
                {
                    "type_id": u["type_id"],
                    "name": info["name"],
                    **at(u),
                    "sells_items": info["sells_items"],
                    "sells_units": info["sells_units"],
                }
            )
    hostile = [u for u in units if u["owner"] == NEUTRAL_HOSTILE and u["type_id"] in catalog]
    for group in camps(hostile):
        levels = [catalog[u["type_id"]]["level"] for u in group]
        x, y = sum(u["x"] for u in group) / len(group), sum(u["y"] for u in group) / len(group)
        result["creep_camps"].append(
            {
                "x": round(x),
                "y": round(y),
                "total_level": sum(levels),
                "creeps": [
                    {"type_id": u["type_id"], "name": name(u["type_id"]), "level": level}
                    for u, level in sorted(zip(group, levels), key=lambda pair: -pair[1])
                ],
                "drops": [drop_label(s[0]["item"], items) for u in group for s in u["drops"] if s],
            }
        )
    result["creep_camps"].sort(key=lambda c: c["total_level"])
    for mine in result["gold_mines"]:
        guards = [c for c in result["creep_camps"] if (c["x"] - mine["x"]) ** 2 + (c["y"] - mine["y"]) ** 2 < 1200**2]
        mine["guard_level"] = sum(c["total_level"] for c in guards)
    return result


def prepare_map(output: Path, reference_path: Path, map_name: str, game_dir=None) -> dict:
    source = Path(game_dir or settings().game_dir) / "Maps" / "FrozenThrone" / map_name
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    with Archive(source) as archive:
        units = placed_units(archive.read("war3mapUnits.doo"))
    result = {
        "schema_version": 1,
        "map": map_name,
        "source": {"map_sha256": digest(source), "reference_sha256": digest(reference_path)},
        **describe(units, reference),
    }
    target = Path(output) / f"{Path(map_name).stem}.json"
    write_json(target, result)
    print(
        f"Map: {target} ({len(result['start_locations'])} starts, {len(result['gold_mines'])} mines, "
        f"{len(result['creep_camps'])} camps, {len(result['neutral_buildings'])} neutral buildings)"
    )
    return result
