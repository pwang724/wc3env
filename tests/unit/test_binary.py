"""Binary observations parse as the DLL lays them out (wc3hook/obsbin.h)."""

import unittest

import numpy as np

from wc3env.binary import DTYPES, HEADER, INSIDE, MAGIC, OWN, TABLES, VERSION, fourcc, parse
from wc3env.protocol import own_unit_ids


def encode(**tables):
    """An observation as obs_write_binary writes it: the header, then each table's records."""
    offset, body, entries = HEADER.size, b"", []
    for name in TABLES:
        records = np.array(tables.get(name, []), DTYPES[name])
        entries += [offset, len(records), DTYPES[name].itemsize]
        offset += records.nbytes
        body += records.tobytes()
    score = [0] * 24 + [7]
    # magic, version, size, player, sequence, time; gold, lumber, food used, food cap; result, events lost
    head = [MAGIC, VERSION, offset, 1, 0, 61250, 500, 0, 0, 0, 2, 0, *score, len(TABLES), *entries]
    return HEADER.pack(*head) + body


class BinaryObservationTest(unittest.TestCase):
    def test_record_sizes_match_the_dll(self):
        sizes = {name: dtype.itemsize for name, dtype in DTYPES.items()}
        self.assertEqual(HEADER.size, 248)
        self.assertEqual(
            sizes,
            {
                "units": 72,
                "abilities": 24,
                "buffs": 8,
                "queue": 12,
                "inventory": 16,
                "items": 16,
                "destructables": 24,
                "events": 20,
            },
        )

    def test_tables_and_scalars_parse(self):
        peasant = int.from_bytes(b"hpea", "big")
        units = [
            (15419, peasant, 1, 4404.2, 3010.1, 220, 220, 0, 0, OWN, 0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0),
            (15420, peasant, 1, 0, 0, 220, 220, 0, 0, OWN | INSIDE, 0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0),
            (15500, peasant, 0, 10, 20, 100, 220, 0, 0, 0, 0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0),
        ]
        obs = parse(encode(units=units, events=[(20, 15500, 0, peasant, 0)]))
        self.assertEqual((obs.player, obs.game_time_seconds, obs.gold, obs.result), (1, 61.25, 500, "defeat"))
        self.assertEqual(obs.score["total"], 7)
        self.assertEqual(fourcc(obs.units["type_id"][0]), "hpea")
        self.assertAlmostEqual(float(obs.units["x"][0]), 4404.2, places=3)
        self.assertEqual(own_unit_ids(obs), frozenset({15419, 15420}))
        self.assertEqual(obs.events["kind"].tolist(), [20])
        self.assertEqual(len(obs.destructables), 0)

    def test_mismatched_layout_is_rejected(self):
        data = bytearray(encode())
        data[4] = 99  # version
        with self.assertRaisesRegex(ValueError, "does not match"):
            parse(bytes(data))
        with self.assertRaisesRegex(ValueError, "shorter"):
            parse(b"\0" * 8)


if __name__ == "__main__":
    unittest.main()
