"""Write docs/examples/observation.json: a real observation with a little of everything in it.

    python tools/example_observation.py

A hero with two items is spawned, one footman is killed, a scroll lies on the ground, so the
sample shows a hero, an inventory, a ground item, several unit types and a death event.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wc3env.game import launch  # noqa: E402
from wc3env.session import GameConfig  # noqa: E402


def main() -> None:
    g = launch(sound=False)
    try:
        c = g.rpc
        c.create_game(str(g.map), [p.to_dict() for p in GameConfig().players], "stepping")
        c.debug("render", on=0)
        hall = next(u for u in c.observe(0)["units"] if u["structure"])
        x, y = hall["x"], hall["y"]
        foot = c.debug("spawn", type_id="hfoo", player=0, x=x + 400, y=y - 400, n=3)["unit_ids"]
        hero = c.debug("spawn", type_id="Hamg", player=0, x=x + 500, y=y - 200)["unit_ids"][0]
        c.debug("give", unit_id=hero, type_id="phea")
        c.debug("give", unit_id=hero, type_id="stwp")
        c.debug("item", type_id="stwp", x=x + 300, y=y + 300)
        c.step(1000)
        c.debug("kill", unit_id=foot[0])
        c.step(1000)
        obs = c.observe(0)
        out = ROOT / "docs" / "examples" / "observation.json"
        out.write_text(json.dumps(obs, indent=1))
        print(f"{out}: {len(obs['units'])} units, {len(obs['events'])} events")
    finally:
        g.close()


if __name__ == "__main__":
    main()
