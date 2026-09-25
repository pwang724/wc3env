"""Game processes: independent launches from any DLL path, and a server that cannot be stalled."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from wc3env.game import hook_dll, launch
from wc3env.pipe import OverlappedPipeFile


class ProcessTest(unittest.TestCase):
    def test_unicode_dll_and_independent_default_launches(self):
        with tempfile.TemporaryDirectory(prefix="wc3-") as temp:
            dll = Path(temp) / "測試-hook.dll"
            shutil.copyfile(hook_dll(), dll)
            games = []
            try:
                with patch("wc3env.game.hook_dll", return_value=dll):
                    for _ in range(2):
                        games.append(launch(render=False))
                self.assertNotEqual(games[0].data_dir, games[1].data_dir)
                for g in games:
                    g.rpc.create_game(str(g.map), [{"slot": 0, "control": "agent"}], "stepping")
                    self.assertEqual(g.rpc.step(25)["elapsed_ms"], 25)
            finally:
                for g in games:
                    g.close()

    def test_server_disconnects_a_client_that_stops_reading(self):
        g = launch(render=False)
        self.addCleanup(g.close)
        g.pipe.close()
        deadline = time.monotonic() + 5
        while True:
            try:
                raw = OverlappedPipeFile(g.pipe.path)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        self.addCleanup(raw.close)
        start = time.monotonic()
        with self.assertRaises(OSError):
            for _ in range(64):
                raw.write(b"echo " + b"x" * 60000 + b"\n", timeout=12)
        self.assertLess(time.monotonic() - start, 10)
        self.assertIn("client stopped reading", g.log())
