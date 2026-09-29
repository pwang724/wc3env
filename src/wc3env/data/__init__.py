"""Game facts extracted from the installed game by `python -m tools.prepare` (tools/README.md).

reference.json: every unit, ability, item, upgrade and buff with costs, requirements and stats.
maps/<map>.json: a stock map's start locations, gold mines, creep camps and neutral buildings.
order_targets.json, item_targets.json: how each order and item is aimed (inputs to reference.json).
"""

from pathlib import Path

DIR = Path(__file__).resolve().parent
REFERENCE = DIR / "reference.json"
MAPS = DIR / "maps"
