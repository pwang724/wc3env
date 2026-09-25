"""Footprint preparation reads dimensions from texture data, not filenames."""

import struct
import unittest

from tools.prepare.pathing import texture_size


class PreparedPathingTest(unittest.TestCase):
    def test_texture_header_dimensions_become_world_bounds(self):
        header = bytearray(18)
        struct.pack_into("<HH", header, 12, 6, 10)
        self.assertEqual(texture_size(header), [192, 320])
        for data in (b"", bytes(17), bytes(18)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                texture_size(data)


if __name__ == "__main__":
    unittest.main()
