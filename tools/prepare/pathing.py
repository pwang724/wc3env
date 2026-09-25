"""Prepare conservative footprint bounds from the installed pathing textures."""

import struct

from .gamedata import read_all, slk


def texture_size(data):
    """A pathing pixel covers 32 world units; retain the full texture bounds."""
    if len(data) < 18:
        raise ValueError("Truncated pathing texture")
    width, height = struct.unpack_from("<HH", data, 12)
    if not width or not height:
        raise ValueError("Empty pathing texture")
    return [32 * width, 32 * height]


def footprints(units, game_dir=None):
    destructables = slk(read_all([r"Units\DestructableData.slk"], game_dir)[r"Units\DestructableData.slk"])
    objects = {**units, **destructables}
    paths = {r["pathTex"] for r in objects.values() if r.get("pathTex", "_") not in ("", "_", "-")}
    textures = read_all(sorted(paths), game_dir)
    sizes = {path: texture_size(textures[path].encode("latin-1")) for path in paths}
    return {raw: sizes.get(row.get("pathTex"), [0, 0]) for raw, row in objects.items()}
