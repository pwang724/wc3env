"""Actions must start on the next turn at any speed, including after a fast reset."""

import unittest

from wc3env.game import launch


class TurnTimingTest(unittest.TestCase):
    def check_timing(self, agents):
        game = launch(agents=agents)
        self.addCleanup(game.close)
        c = game.rpc
        players = [{"slot": p, "control": "agent" if p in agents else "computer"} for p in (0, 1)]
        for episode, speed in enumerate((1, 64, 256, 2048, 64)):
            with self.subTest(agents=agents, episode=episode, speed=speed):
                if episode:
                    c.debug("speed", factor=speed)
                    c.reset()
                c.create_game(str(game.map), players, "stepping")
                c.debug("speed", factor=speed)
                c.debug("waitfloor", ms=1)
                c.debug("render", on=0)
                heroes = {}
                for p in agents:
                    o = c.observe(p)
                    self.assertEqual(o["game_time_seconds"], 1.0)
                    hall = next(u for u in o["units"] if u["structure"])
                    hero = c.debug("spawn", type_id="Hamg", player=p, x=hall["x"] + 300, y=hall["y"] - 500)["unit_ids"][
                        0
                    ]
                    heroes[p] = hero
                    c.debug("level", unit_id=hero, level=4)
                    c.debug("give", unit_id=hero, type_id="phea")
                    c.observe(p)

                def act(command, arguments):
                    for p, hero in heroes.items():
                        result = c.act(p, [{"unit_id": hero, "command": command, "arguments": arguments}])
                        self.assertEqual(result["rejected"], [])

                act("learn", {"ability_id": "AHwe"})
                c.step(25)
                for p, hero in heroes.items():
                    o = c.observe(p)
                    self.assertEqual(o["game_time_seconds"], 1.025)
                    self.assertIn({"kind": "hero_learn", "unit_id": hero, "ability_id": "AHwe"}, o["events"])
                act("cast", {"order": "waterelemental"})
                c.step(500)
                for p, hero in heroes.items():
                    self.assertIn(
                        {"kind": "spell_effect", "unit_id": hero, "ability_id": "AHwe"}, c.observe(p)["events"]
                    )
                    c.debug("hp", unit_id=hero, value=100)
                act("use_item", {"slot": 0})
                c.step(250)
                for p, hero in heroes.items():
                    self.assertIn({"kind": "item_use", "unit_id": hero, "type_id": "phea"}, c.observe(p)["events"])
                for _ in range(8):
                    act("move", {"x": 0, "y": 0})
                    c.step(25)
                    for hero in heroes.values():
                        self.assertEqual(c.debug("order", unit_id=hero)["order_id"], 851986)
                    act("stop", {})
                    c.step(25)
                    for hero in heroes.values():
                        self.assertNotEqual(c.debug("order", unit_id=hero)["order_id"], 851986)

    def test_local_actions(self):
        self.check_timing((0,))

    def test_shared_actions(self):
        self.check_timing((0, 1))

    def test_progressing_step_can_take_more_than_five_wall_seconds(self):
        game = launch(render=False)
        self.addCleanup(game.close)
        c = game.rpc
        c.create_game(str(game.map), [{"slot": 0, "control": "agent"}])
        c.debug("speed", factor=1)
        result = c.step(6000)
        self.assertEqual((result["elapsed_ms"], result["reason"]), (6000, "target"))
