import unittest

from tests.fakes import FakeGame
from wc3env.protocol import ProtocolError
from wc3env.session import GameConfig, GameSession, PlayerConfig


class ControlGroupsTest(unittest.TestCase):
    def setUp(self):
        self.session = GameSession(
            GameConfig(map="m", players=(PlayerConfig(0), PlayerConfig(1))), game_factory=FakeGame
        )
        self.addCleanup(self.session.close)
        self.session.reset()
        self.groups = self.session.view(0).groups

    def test_groups_preserve_order_and_snapshot_arguments(self):
        self.groups.assign("workers", [1002, 1001, 1002])
        self.assertEqual(self.groups.members("workers"), (1002, 1001))
        args = {"x": -4900, "y": 2800}
        actions = self.groups.actions("workers", "move", args)
        args["x"] = 0
        self.assertEqual(actions[0].arguments["x"], -4900)
        self.assertIsNot(actions[0].arguments, actions[1].arguments)
        _, _, info = self.session.step({0: actions, 1: []})
        self.assertEqual(info["rejected"], {0: [], 1: []})

    def test_ownership_temporary_absence_and_reset(self):
        with self.assertRaises(ProtocolError):
            self.groups.assign("enemy", [2000])
        self.groups.assign("workers", [1001])
        world = self.session.game.server.world
        worker = world.units.pop(1001)
        self.session._observe_all()
        self.assertEqual(self.groups.actions("workers", "stop"), [])
        world.units[1001] = worker
        self.session._observe_all()
        self.assertEqual(self.groups.members("workers"), (1001,))
        self.session.reset()
        with self.assertRaises(KeyError):
            self.groups.members("workers")

    def test_groups_are_independent_between_players_and_closed_sessions(self):
        self.groups.assign("army", [1001])
        other = self.session.groups(1)
        other.assign("army", [2000])
        self.groups.clear()
        self.assertEqual(other.members("army"), (2000,))
        self.session.close()
        with self.assertRaises(RuntimeError):
            other.actions("army", "stop")
