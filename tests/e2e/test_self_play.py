"""Two agents, one real simulation: startup, command execution, fog, events and results.

python -m unittest tests.e2e.test_self_play
"""

import math
import unittest

from wc3env.protocol import Action, Observation
from wc3env.session import GameConfig, GameSession, PlayerConfig


class SelfPlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = GameSession(GameConfig(players=(PlayerConfig(1), PlayerConfig(0))))
        cls.addClassCleanup(cls.session.close)

    def setUp(self):
        self.obs = self.session.reset()
        self.rpc = self.session.game.rpc
        self.rpc.debug("speed", factor=64)
        self.rpc.debug("waitfloor", ms=1)
        self.rpc.debug("render", on=0)
        self.halls = {
            p: next(u for u in o["units"] if u["type_id"] in ("htow", "ogre", "unpl", "etol"))
            for p, o in self.obs.items()
        }

    def step(self, actions=None):
        previous_time = self.obs[0]["game_time_seconds"]
        self.obs, done, info = self.session.step(actions or {0: [], 1: []})
        self.assertEqual(info["rejected"], {0: [], 1: []})
        self.assertEqual(self.obs[0]["game_time_seconds"], self.obs[1]["game_time_seconds"])
        self.assertEqual(self.obs[0]["sequence"], self.obs[1]["sequence"])
        if not done:
            self.assertAlmostEqual(self.obs[0]["game_time_seconds"], previous_time + 1)
        return done

    def spawn(self, p, type_id="hfoo", n=1):
        h = self.halls[p]
        return self.rpc.debug("spawn", type_id=type_id, player=p, x=h["x"] + 400, y=h["y"] - 500, n=n)["unit_ids"]

    def test_no_builtin_ai_and_each_view_has_its_own_fog(self):
        self.assertEqual([p["control"] for p in self.session.setup["players"]], ["agent", "agent"])
        before = self.obs
        items = {
            p: self.rpc.debug("item", type_id="phea", x=h["x"] + 200, y=h["y"])["item_id"]
            for p, h in self.halls.items()
        }
        for _ in range(30):
            self.assertFalse(self.step())
        for p in (0, 1):
            self.assertEqual({u["unit_id"] for u in self.obs[p]["units"]}, {u["unit_id"] for u in before[p]["units"]})
            self.assertEqual(self.obs[p]["player"]["gold"], before[p]["player"]["gold"])
            self.assertIn(items[p], {i["item_id"] for i in self.obs[p]["items"]})
            self.assertNotIn(items[1 - p], {i["item_id"] for i in self.obs[p]["items"]})
            self.assertNotIn(self.halls[1 - p]["unit_id"], {u["unit_id"] for u in self.obs[p]["visible_enemies"]})

    def test_both_players_move_and_removed_selections_are_safe(self):
        self.check_moves_and_removed_selections()

    def check_moves_and_removed_selections(self):
        ids = {p: self.spawn(p)[0] for p in (0, 1)}
        self.step()
        before = {p: next(u for u in self.obs[p]["units"] if u["unit_id"] == ids[p]) for p in (0, 1)}
        targets = {p: (u["x"] + 500, u["y"]) for p, u in before.items()}
        self.step({p: [Action(ids[p], "move", {"x": x, "y": y})] for p, (x, y) in targets.items()})
        self.step()
        for p in (0, 1):
            u = next(u for u in self.obs[p]["units"] if u["unit_id"] == ids[p])
            self.assertLess(math.dist((u["x"], u["y"]), targets[p]), 400, (p, before[p], u))
            self.rpc.debug("remove", unit_id=ids[p])
        ids = {p: self.spawn(p)[0] for p in (0, 1)}
        self.step()
        self.step({p: [Action(ids[p], "move", {"x": self.halls[p]["x"], "y": self.halls[p]["y"]})] for p in (0, 1)})
        self.assertNotIn("native exception", self.session.game.log())

    def test_large_batches_keep_every_players_selection(self):
        ids = {p: self.spawn(p, n=64) for p in (0, 1)}
        self.step()
        # Full batches for both players must fit, including every selection record.
        self.step(
            {
                p: [Action(uid, "move", {"x": self.halls[p]["x"] - 1500, "y": self.halls[p]["y"]}) for uid in ids[p]]
                for p in (0, 1)
            }
        )
        for p in (0, 1):
            orders = [self.rpc.debug("order", unit_id=uid)["order_id"] for uid in ids[p]]
            self.assertEqual(orders, [851986] * 64, (p, orders))

    def test_both_players_train_with_separate_events(self):
        trainers = {p: self.spawn(p, "hbar")[0] for p in (0, 1)}
        for p in (0, 1):
            self.rpc.debug("resources", player=p, gold=5000, lumber=5000)
        self.step()
        self.step({p: [Action(uid, "train", {"type_id": "hfoo"})] for p, uid in trainers.items()})
        finished = {0: [], 1: []}
        for _ in range(25):
            for p in (0, 1):
                finished[p] += [e for e in self.obs[p]["events"] if e["kind"] == "train_finish"]
            self.step()
        for p in (0, 1):
            self.assertEqual(len(finished[p]), 1, finished)
            self.assertEqual(finished[p][0]["unit_id"], trainers[p])
            self.assertIn(finished[p][0]["trained_id"], {u["unit_id"] for u in self.obs[p]["units"]})

    def test_repeated_reset_clears_units_orders_events_and_staging(self):
        pid = self.session.game.pid

        def state(obs):
            return {
                p: (o["player"], sorted((u["type_id"], u["x"], u["y"], u["hp"]) for u in o["units"]))
                for p, o in obs.items()
            }

        initial = state(self.obs)
        for _ in range(3):
            for p in (0, 1):
                hero = self.spawn(p, "Hamg")[0]
                self.rpc.debug("level", unit_id=hero, level=3)
                self.rpc.debug("give", unit_id=hero, type_id="phea")
                self.rpc.debug("resources", player=p, gold=9999, lumber=9999)
                # Leave actions and events pending when reset starts.
                self.rpc.act(p, [Action(hero, "move", {"x": 0, "y": 0}).to_dict()])
            self.rpc.debug("invulnerable", player=1, on=1)
            self.obs = self.session.reset()
            self.assertEqual(self.session.game.pid, pid)
            self.assertEqual(state(self.obs), initial)
            self.assertEqual(self.session.steps, 0)
            for o in self.obs.values():
                self.assertEqual((o["sequence"], o["game_time_seconds"], o["result"], o["events"]), (0, 1.0, "", []))
            self.assertFalse(self.step())
            self.assertEqual(state(self.obs), initial)  # no old orders or built-in AI

        # The previous episode's recurring invulnerability must not affect fresh units.
        h = self.halls[0]
        attacker = self.rpc.debug("spawn", type_id="hfoo", player=0, x=h["x"] + 400, y=h["y"] - 500)["unit_ids"][0]
        target = self.rpc.debug("spawn", type_id="hfoo", player=1, x=h["x"] + 450, y=h["y"] - 500)["unit_ids"][0]
        self.step()
        self.step({0: [Action(attacker, "attack", {"target_id": target})], 1: [Action(target, "stop", {})]})
        for _ in range(4):
            self.step()
        target_obs = next(u for u in self.obs[1]["units"] if u["unit_id"] == target)
        self.assertLess(target_obs["hp"], target_obs["max_hp"])

    def test_two_agents_fight_to_a_result(self):
        """Player 1 wins: the host must preserve both results even though the local player loses."""
        h = self.halls[0]
        self.rpc.debug("spawn", type_id="hkni", player=1, x=h["x"] + 600, y=h["y"] - 500, n=24)
        self.rpc.debug("invulnerable", player=1, on=1)
        self.step()

        class AttackVisible:
            def act(self, observation: Observation):
                o = observation.payload
                enemies = [u for u in o["visible_enemies"] if u["owner"] in (0, 1) and u["hp"] > 0]
                targets = [u for u in enemies if u["structure"]] or enemies
                if not targets:
                    return []
                target = targets[0]
                return [
                    Action(u["unit_id"], "attack", {"target_id": target["unit_id"]})
                    for u in o["units"]
                    if not u["structure"] and u["hp"] > 0
                ][:64]

        agents = {p: AttackVisible() for p in (0, 1)}
        for _ in range(180):
            if self.step({p: a.act(Observation.from_dict(self.obs[p])) for p, a in agents.items()}):
                break
        self.assertTrue(self.session.done)
        self.assertEqual(self.obs[0]["result"], "defeat")
        self.assertEqual(self.obs[1]["result"], "victory")
        for p in (1, 0):
            final = self.rpc.observe(p)
            self.assertEqual(final["game_time_seconds"], self.obs[p]["game_time_seconds"])
            self.assertEqual(final["result"], self.obs[p]["result"])
        pid = self.session.game.pid
        self.obs = self.session.reset()
        self.assertEqual(self.session.game.pid, pid)
        self.assertFalse(self.session.done)
        for o in self.obs.values():
            self.assertEqual((o["sequence"], o["game_time_seconds"], o["result"], o["events"]), (0, 1.0, "", []))
        self.check_moves_and_removed_selections()
