"""Every unit in the game has a combat role, shown to Jev beside each unit type."""

import json
import unittest
from pathlib import Path

from wc3agent.game.catalog import Catalog
from wc3agent.game.roles import ROLES, TABLE, role

REFERENCE = Path(__file__).parents[1] / "src/wc3agent/game/data/reference.json"


class Roles(unittest.TestCase):
    def test_every_unit_in_the_game_has_a_known_role(self):
        units = json.loads(REFERENCE.read_text(encoding="utf-8"))["units"]
        table = json.loads(TABLE.read_text(encoding="utf-8"))
        missing = [raw for raw, u in units.items() if not u.get("structure") and raw not in table["roles"]]
        self.assertEqual(missing, [])
        self.assertEqual(set(table["roles"].values()) - set(ROLES), set())
        self.assertEqual({k: table["roles"][k] for k in table["assigned"]}, table["assigned"])  # hand-picked win

    def test_heroes_creeps_and_neutrals_have_roles(self):
        catalog = Catalog.load(REFERENCE)
        for raw, expected in {"Hamg": "caster hero", "Hmkg": "melee hero", "hsor": "caster", "hkni": "tank",
                              "nomg": "melee", "nwzg": "caster", "Nbrn": "caster hero"}.items():  # fmt: skip
            with self.subTest(raw=raw):
                self.assertEqual(role(catalog, raw), expected)


if __name__ == "__main__":
    unittest.main()
