"""Launch isolates each process, resolves maps, validates first and cleans up on failure."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wc3env import GameConfig, MatchSetup, PlayerConfig
from wc3env.compatibility import verify_executable
from wc3env.game import launch, resolve_map


class LaunchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.map = self.root / "Maps" / "FrozenThrone" / "chosen.w3x"
        self.map.parent.mkdir(parents=True)
        self.map.touch()
        self.cfg = SimpleNamespace(
            game_dir=self.root,
            map=self.map,
            user_dir=self.root / "Users",
            game_exe=self.root / "Warcraft.exe",
            output_dir=None,
            sound=True,
        )
        settings = patch("wc3env.game.settings", return_value=self.cfg)
        settings.start()
        self.addCleanup(settings.stop)

    @patch("wc3env.game.HookPipe")
    @patch("wc3env.game.launch_with_dll", return_value=(123, 456))
    def test_each_launch_gets_its_own_profile_directory(self, inject, pipe):
        output = self.root / "sessions" / "run-1" / "env"
        games = [launch(output_dir=output) for _ in range(2)]
        self.assertNotEqual(games[0].data_dir, games[1].data_dir)
        for game, call in zip(games, inject.call_args_list):
            self.assertTrue(game.data_dir.is_dir())
            self.assertEqual(game.data_dir.parent, output)
            self.assertEqual(call.kwargs["env"]["WC3HOOK_DOCS"], str(game.data_dir))
        self.assertFalse((self.root / "wc3env").exists())

    def test_map_names_resolve_to_one_file(self):
        for name in (None, "chosen.w3x", "Maps/FrozenThrone/chosen.w3x", self.map):
            self.assertEqual(resolve_map(name), self.map)
        (self.root / "Maps" / "chosen.w3x").touch()
        with self.assertRaisesRegex(ValueError, "explicit path"):
            resolve_map("chosen.w3x")
        with self.assertRaises(FileNotFoundError):
            resolve_map(self.root / "missing.w3x")

    @patch("wc3env.game.k32")
    @patch("wc3env.game.HookPipe")
    @patch("wc3env.game.launch_with_dll", return_value=(123, 456))
    def test_failed_connection_terminates_the_child(self, inject, pipe, kernel):
        pipe.return_value.connect.side_effect = TimeoutError("pipe unavailable")
        with self.assertRaises(TimeoutError):
            launch()
        kernel.TerminateProcess.assert_called_once_with(456, 0)
        kernel.CloseHandle.assert_called_once_with(456)
        pipe.return_value.close.assert_called_once()

    @patch("wc3env.game.launch_with_dll")
    def test_invalid_arguments_fail_before_launch(self, inject):
        replay = self.root / "recording.w3g"
        replay.touch()
        for kwargs in (
            {"map": "missing.w3x"},
            {"map": replay, "ai_difficulty": 1},
            {"window_mode": "headless"},
            {"ai_difficulty": 3},
            {"ai_agents": (1,)},  # an AI-backed slot must also be an agent slot
            {"background_visible": True, "window_mode": "interactive"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                launch(**kwargs)
        inject.assert_not_called()

    def test_unsupported_executable_is_rejected_before_injection(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "game.exe"
            exe.write_bytes(b"MZ" + bytes(100))
            with self.assertRaises(ValueError):
                verify_executable(exe)


class AiBackedAgents(unittest.TestCase):
    def test_ai_backed_agents_load_as_computer_slots_with_their_ai_running(self):
        config = GameConfig(
            players=(PlayerConfig(0, race="human"), PlayerConfig(1, control="computer")),
            ai_agents=(0,),
            setup=MatchSetup(seed=1),
        )
        players = config.launch_setup()["_setup"]["players"]
        self.assertEqual([p["control"] for p in players], ["agent_ai", "computer"])
        self.assertEqual(config.agent_slots, (0,))
        with self.assertRaises(ValueError):
            GameConfig(players=(PlayerConfig(0), PlayerConfig(1, control="computer")), ai_agents=(1,))
