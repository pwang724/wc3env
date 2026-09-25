"""Hidden targets and events stay isolated between observers."""

import unittest

from wc3env.game import launch
from wc3env.rpc import RpcError


class VisibilityTest(unittest.TestCase):
    def test_event_overflow_is_reported_and_consumed_per_observer(self):
        g = launch(agents=(0, 1), render=False)
        self.addCleanup(g.close)
        c = g.rpc
        players = [{"slot": p, "control": "agent"} for p in (0, 1)]
        c.create_game(str(g.map), players, "stepping")
        halls = [next(u for u in c.observe(p)["units"] if u["structure"]) for p in (0, 1)]

        def spawn(player, n):
            hall = halls[player]
            ids = c.debug("spawn", type_id="hfoo", player=player, x=hall["x"] + 400, y=hall["y"] - 500, n=n)["unit_ids"]
            self.assertEqual(len(ids), n)
            return ids

        for batch, n in enumerate((500, 500, 100)):
            ids = spawn(0, n)
            if batch == 0:
                enemy = c.observe(1)
                self.assertFalse(set(ids) & {u["unit_id"] for u in enemy["visible_enemies"]})
                # This unread visible event must survive the other player's hidden flood.
                retained = spawn(1, 1)[0]
                c.debug("kill", unit_id=retained)
            for uid in ids:
                c.debug("kill", unit_id=uid)
        obs = c.observe(0)
        self.assertGreater(obs["events_lost"], 0)
        self.assertEqual(len(obs["events"]) + obs["events_lost"], 1100)
        other = c.observe(1)
        self.assertEqual(other["events_lost"], 0)
        self.assertEqual([(e["kind"], e["unit_id"]) for e in other["events"]], [("death", retained)])
        for p in (0, 1):
            following = c.observe(p)
            self.assertEqual((following["events_lost"], following["events"]), (0, []))
        # Consumed ring slots must not count as lost when reused.
        for uid in spawn(0, 100):
            c.debug("kill", unit_id=uid)
        following = c.observe(0)
        self.assertEqual((following["events_lost"], len(following["events"])), (0, 100))
        c.debug("kill", unit_id=spawn(0, 1)[0])
        c.reset()
        c.create_game(str(g.map), players, "stepping")
        for p in (0, 1):
            fresh = c.observe(p)
            self.assertEqual((fresh["events_lost"], fresh["events"]), (0, []))

    def test_hidden_targets_wrong_object_types_and_destructable_fog(self):
        g = launch(agents=(0, 1), render=False)
        self.addCleanup(g.close)
        c = g.rpc
        c.create_game(str(g.map), [{"slot": p, "control": "agent"} for p in (0, 1)], "stepping")
        obs = {p: c.observe(p) for p in (0, 1)}
        own = next(u for u in obs[0]["units"] if not u["structure"])
        hidden = next(u for u in obs[1]["units"] if u["structure"])
        for command in ("attack", "smart"):
            r = c.act(
                0, [{"unit_id": own["unit_id"], "command": command, "arguments": {"target_id": hidden["unit_id"]}}]
            )
            self.assertEqual(r["rejected"], [{"index": 0, "reason": "bad_arguments"}])
        item = c.debug("item", type_id="phea", x=own["x"], y=own["y"])["item_id"]
        r = c.act(0, [{"unit_id": item, "command": "stop"}])
        self.assertEqual(r["rejected"], [{"index": 0, "reason": "unknown_unit"}])
        with self.assertRaises(RpcError):
            c.debug("hp", unit_id=item, value=100)
        for p in (0, 1):
            self.assertEqual(obs[p]["observer"], p)
            self.assertEqual(obs[p]["events_lost"], 0)
            self.assertTrue(obs[p]["destructables"])
            self.assertNotIn("tree", obs[p])
        self.assertNotEqual({d["id"] for d in obs[0]["destructables"]}, {d["id"] for d in obs[1]["destructables"]})
