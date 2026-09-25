"""When code hands army units to Jev in fusion mode, and when it resends Jev's orders."""

import unittest
from unittest.mock import MagicMock

from agent_fixtures import fighting_catalog, own, state
from wc3agent.game.policies import in_fight, losing, overridden

from wc3agent import fusion


def creep(uid, x, hp=200):
    return dict(unit_id=uid, type_id="ogru", owner=12, x=x, y=0.0, hp=hp, max_hp=200, mana=0, max_mana=0,
                structure=False, hero=False, level=0)  # fmt: skip


class Fights(unittest.TestCase):
    def setUp(self):
        self.catalog = fighting_catalog()

    def obs(self, *units, enemies=()):
        obs = state()
        obs["units"] = [own(1, "htow", x=-3000), *units]
        obs["visible_enemies"] = list(enemies)
        return obs

    def test_army_near_hostiles_and_its_neighbours_fight_but_workers_do_not(self):
        obs = self.obs(own(10, "hfoo", x=0), own(11, "hfoo", x=-500), own(12, "hfoo", x=-2000), own(3, "hpea", x=0),
                       enemies=[creep(50, 800)])  # fmt: skip
        ids, enemies = in_fight(obs, self.catalog)
        self.assertEqual(ids, {10, 11})
        self.assertEqual([e["unit_id"] for e in enemies], [50])
        self.assertEqual(in_fight(self.obs(own(10, "hfoo"), enemies=[creep(50, 2000)]), self.catalog), (set(), []))

    def test_a_much_stronger_enemy_counts_as_losing(self):
        obs = self.obs(own(10, "hfoo", hp=40), enemies=[creep(50, 300), creep(51, 300)])
        self.assertTrue(losing(obs, self.catalog, {10}, obs["visible_enemies"]))
        self.assertFalse(losing(obs, self.catalog, {10}, []))

    def test_an_order_is_resent_only_after_the_ai_replaced_it(self):
        attack = {"unit_id": 10, "command": "attack", "arguments": {"target_id": 50}}
        target = [creep(50, 300)]
        mine = own(10, "hfoo", order={"name": "attack", "target_id": 50})
        self.assertFalse(overridden(attack, mine, self.obs(mine, enemies=target)))
        mine["order"] = {"name": "attack", "x": 900, "y": 0}  # the AI's attack-move
        self.assertTrue(overridden(attack, mine, self.obs(mine, enemies=target)))
        self.assertFalse(overridden(attack, mine, self.obs(mine, enemies=[creep(50, 300, hp=0)])))  # target died
        move = {"unit_id": 10, "command": "move", "arguments": {"x": 1000, "y": 0}}
        self.assertTrue(overridden(move, mine, self.obs(mine)))
        mine["x"] = 1000
        self.assertFalse(overridden(move, mine, self.obs(mine)))  # arrived

    def test_a_run_stops_when_jev_keeps_failing(self):
        micro = MagicMock()
        micro.loot_sent = {}
        micro.act.return_value = ([], [{"error": "TypeSafe HTTP 402: no credits"}])
        fights = fusion.Fights(self.catalog, micro)
        obs = self.obs(own(10, "hfoo"))
        for _ in range(fusion.MAX_FAILURES - 1):
            fights.act(obs, wait=True)
        with self.assertRaisesRegex(RuntimeError, "402"):
            fights.act(obs, wait=True)
        micro.act.return_value = ([], [{"response": {}}])  # one answer resets the count
        fights.failures = fusion.MAX_FAILURES - 1
        fights.act(obs, wait=True)
        self.assertEqual(fights.failures, 0)

    def test_an_idle_fighter_is_sent_back_into_the_fight_on_attack_move(self):
        micro = MagicMock()
        micro.loot_sent = {}
        micro.act.return_value = ([], [])
        fights = fusion.Fights(self.catalog, micro)
        idle, busy = own(10, "hfoo", order=None), own(11, "hfoo", order={"name": "attack", "target_id": 50})
        actions, _ = fights.act(self.obs(idle, busy, enemies=[creep(50, 300)]), wait=True)
        self.assertEqual(actions, [{"unit_id": 10, "command": "attack", "arguments": {"x": 300.0, "y": 0.0}}])
        self.assertEqual(fights.idle_restarts, 1)

    def test_an_idle_unit_jev_chose_to_keep_or_reposition_is_left_alone_for_a_moment(self):
        micro = MagicMock()
        micro.loot_sent = {}
        micro.act.return_value = ([], [{"choices": {"10": "back_off"}}])
        fights = fusion.Fights(self.catalog, micro)
        obs = self.obs(own(10, "hfoo", order=None), enemies=[creep(50, 300)])
        actions, _ = fights.act(obs, wait=True)
        self.assertEqual(actions, [])  # Jev just placed it: no restart
        micro.act.return_value = ([], [])
        obs["game_time_seconds"] += 3.5
        actions, _ = fights.act(obs, wait=True)
        self.assertEqual([a["unit_id"] for a in actions], [10])  # the hold has run out


if __name__ == "__main__":
    unittest.main()
