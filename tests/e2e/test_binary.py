"""Binary observations carry the JSON observation's facts, and a binary session steps in one round trip."""

import unittest
from dataclasses import replace

from wc3env.binary import INSIDE, OWN, STATES, STRUCTURE, SharedObservations, fourcc
from wc3env.session import GameConfig, GameSession, MatchSetup, PlayerConfig

CONFIG = GameConfig(
    map="(2)EchoIsles.w3x",
    players=(PlayerConfig(0, "human"), PlayerConfig(1, "orc", control="computer")),
    ai_agents=(0,),
    setup=MatchSetup(seed=5, randomize_starts=True),
    render=False,
    sound=False,
    step_ms=250,
)


class BinaryObservationTest(unittest.TestCase):
    def test_binary_matches_json_of_the_same_state(self):
        with GameSession(CONFIG) as session:
            session.reset()
            session.debug("speed", factor=512)
            rpc = session.game.rpc
            for _ in range(479):  # two minutes: workers in mines, production, buffs
                rpc.step(250)
            located = rpc.step(250, observe=[0, 1])["observations"]
            shared = SharedObservations(session.game.pid)
            self.addCleanup(shared.close)
            binary = {o["player"]: shared.read(o["offset"], o["size"]) for o in located}
            for player in (0, 1):
                with self.subTest(player=player):
                    self.assert_same(binary[player], rpc.observe(player))

    def assert_same(self, b, j):
        self.assertEqual(b.game_time_seconds, j["game_time_seconds"])
        self.assertEqual((b.gold, b.lumber, b.food_used, b.food_cap), tuple(j["player"].values()))
        self.assertEqual(b.score, j["score"])
        flags = b.units["flags"]
        on_map = b.units[(flags & INSIDE) == 0]
        self.assertEqual(on_map["unit_id"][(on_map["flags"] & OWN) != 0].tolist(), [u["unit_id"] for u in j["units"]])
        self.assertEqual(
            on_map["unit_id"][(on_map["flags"] & OWN) == 0].tolist(), [u["unit_id"] for u in j["visible_enemies"]]
        )
        self.assertEqual(b.units["unit_id"][(flags & INSIDE) != 0].tolist(), [u["unit_id"] for u in j["inside"]])
        self.assertTrue(j["inside"], "two minutes in, some workers are inside a mine")
        units = {int(u["unit_id"]): u for u in b.units}
        for ju in j["units"] + j["visible_enemies"]:
            u = units[ju["unit_id"]]
            self.assertEqual(fourcc(u["type_id"]), ju["type_id"])
            self.assertEqual((int(u["owner"]), int(u["hp"]), int(u["max_hp"])), (ju["owner"], ju["hp"], ju["max_hp"]))
            self.assertAlmostEqual(float(u["x"]), ju["x"], places=2)
            self.assertEqual(bool(u["flags"] & STRUCTURE), ju["structure"])
            self.assertEqual([fourcc(r["buff_id"]) for r in b.buffs if r["unit_id"] == u["unit_id"]], ju["buffs"])
            if "abilities" in ju:
                mine = [r for r in b.abilities if r["unit_id"] == u["unit_id"]]
                self.assertEqual([fourcc(r["ability_id"]) for r in mine], [a["ability_id"] for a in ju["abilities"]])
            if "queue" in ju:
                queue = [fourcc(r["type_id"]) for r in b.queue if r["unit_id"] == u["unit_id"]]
                self.assertEqual((queue, STATES[u["state"]]), (ju["queue"], ju["state"] or ""))
        self.assertEqual([int(d["id"]) for d in b.destructables], [d["id"] for d in j["destructables"]])
        self.assertEqual([int(i["item_id"]) for i in b.items], [i["item_id"] for i in j["items"]])

    def test_binary_session_steps_and_validates_actions(self):
        with GameSession(replace(CONFIG, observation="binary")) as session:
            observations = session.reset()
            worker = next(
                int(u["unit_id"]) for u in observations[0].units if fourcc(u["type_id"]) == "hpea" and u["flags"] & OWN
            )
            self.assertEqual(set(observations), {0})  # the computer player is not observed
            enemy = next(int(u["unit_id"]) for u in observations[0].units if not u["flags"] & OWN)
            observations, done, info = session.step({0: [{"unit_id": worker, "command": "stop", "arguments": {}}]})
            self.assertFalse(done)
            self.assertEqual(info["elapsed_ms"], 250)
            self.assertEqual(set(observations), {0})
            self.assertEqual([r["method"] for r in info["rpc_timings_ms"]], ["act", "step"])
            with self.assertRaisesRegex(Exception, "uncontrolled unit"):
                session.step({0: [{"unit_id": enemy, "command": "stop", "arguments": {}}]})


if __name__ == "__main__":
    unittest.main()
