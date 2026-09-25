import contextlib
import io
import unittest

from wc3agent.console import SCREEN_BYTES, SCREEN_LINES, Console


class Board(unittest.TestCase):
    def test_the_board_fits_what_warcraft_shows_and_groups_units_by_choice(self):
        console = Console()
        reply = "\n".join(
            f"A long sentence of the macro reply number {i} about gold, lumber and the army." for i in range(20)
        )
        with contextlib.redirect_stdout(io.StringIO()):
            console.show(
                dict(kind="macro", turn=3, latency_ms=4000, landed_at_game_time=100.0, reply=reply, problems=[])
            )
            picked = {f"trollheadhunter{i}": "Keep current order" if i % 2 else f"Attack grunt{i}" for i in range(30)}
            console.show(
                dict(
                    kind="micro",
                    group="army",
                    control_group="army / Troll Headhunter",
                    at_game_time=101.0,
                    picked=picked,
                )
            )
        board = console.panel()
        self.assertLessEqual(len(board.encode("utf-8")), SCREEN_BYTES)  # past it Warcraft cut the text mid-color-code
        self.assertEqual(board.count("\n"), SCREEN_LINES - 1)
        self.assertIn("trollheadhunter1, trollheadhunter3", board)  # the same choice shares a line
        self.assertIn("more", board)
