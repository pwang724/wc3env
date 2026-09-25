"""Startup overrides and seeded execution in the supported offline engine."""

import math
import unittest

from wc3env.protocol import Action
from wc3env.session import GameConfig, GameSession, MatchSetup, PlayerConfig


class MatchSetupTest(unittest.TestCase):
    def session(self, seed=42, players=None, **kwargs):
        config = GameConfig(
            render=False,
            players=players or (PlayerConfig(0, "human"), PlayerConfig(1, "human")),
            setup=MatchSetup(seed=seed, randomize_starts=True),
            **kwargs,
        )
        session = GameSession(config)
        self.addCleanup(session.close)
        return session

    def test_each_race_creates_its_own_starting_units(self):
        for race, hall, worker in (
            ("human", "htow", "hpea"),
            ("orc", "ogre", "opeo"),
            ("undead", "unpl", "uaco"),
            ("night_elf", "etol", "ewsp"),
        ):
            with self.subTest(race=race), self.session(players=(PlayerConfig(0, race), PlayerConfig(1))) as s:
                obs = s.reset()[0]
                self.assertEqual(s.setup["players"][0]["race"], race)
                self.assertIn(hall, {u["type_id"] for u in obs["units"]})
                self.assertIn(worker, {u["type_id"] for u in obs["units"]})

    def test_computer_can_replace_the_default_local_slot_and_runs_ai(self):
        with self.session(players=(PlayerConfig(0, "human", "computer"), PlayerConfig(1, "orc"))) as s:
            before = s.reset()
            self.assertEqual(s.setup["players"][0]["control"], "computer")
            s.game.rpc.debug("speed", factor=64)
            for _ in range(30):
                obs, done, info = s.step({1: []})
                self.assertFalse(done)
                self.assertEqual(info["elapsed_ms"], 1000)
            self.assertGreater(obs[0]["score"]["gold_mined"], before[0]["score"]["gold_mined"])
            # Melee startup may issue initial gather orders even with AI suppressed.
            for key in ("units_trained", "structures_built"):
                self.assertEqual(obs[1]["score"][key], before[1]["score"][key])

    def test_seed_repeats_races_positions_and_combat_after_reset(self):
        with self.session(
            seed=21,
            players=(PlayerConfig(0, "random"), PlayerConfig(1, "random")),
            step_ms=250,
            max_episodes_per_process=2,
        ) as s:
            runs = []
            pids = []
            for _ in range(3):
                obs = s.reset()
                pids.append(s.game.pid)
                if len(pids) == 3:
                    self.assertEqual(s.game.rpc.tuning.get("speed"), {"factor": 64})
                initial = {p: sorted((u["type_id"], u["x"], u["y"]) for u in o["units"]) for p, o in obs.items()}
                s.game.rpc.debug("speed", factor=64)
                home = obs[0]["units"][0]
                ids = [
                    s.game.rpc.debug("spawn", type_id="hfoo", player=p, x=home["x"] + 400 + p * 60, y=home["y"] - 400)[
                        "unit_ids"
                    ][0]
                    for p in (0, 1)
                ]
                s._observe_all()  # refresh the normal observation used for action validation after staging
                trace = []
                for tick in range(32):
                    actions = {
                        p: [Action(ids[p], "attack", {"target_id": ids[1 - p]})] if tick == 0 else [] for p in (0, 1)
                    }
                    obs, done, _ = s.step(actions)
                    self.assertFalse(done)
                    trace.append(
                        tuple(next(u["hp"] for u in obs[p]["units"] if u["unit_id"] == ids[p]) for p in (0, 1))
                    )
                self.assertNotEqual(trace[0], trace[-1], "combat must actually deal damage")
                runs.append((s.setup, initial, trace))
            self.assertEqual(runs[0], runs[1])
            self.assertEqual(runs[0], runs[2])
            self.assertEqual(pids[0], pids[1])
            self.assertNotEqual(pids[0], pids[2])

    def test_distinct_seeds_can_change_start_locations(self):
        locations = []
        for seed in (1, 2):
            with self.session(seed=seed) as s:
                obs = s.reset()[0]
                hall = next(u for u in obs["units"] if u["type_id"] == "htow")
                locations.append((hall["x"], hall["y"]))
        self.assertNotEqual(*locations)

    def test_four_map_slots_have_independent_commands_views_and_resets(self):
        players = tuple(PlayerConfig(p, race) for p, race in enumerate(("human", "orc", "undead", "night_elf")))
        with self.session(players=players, map="(4)TwistedMeadows.w3x") as s:
            for episode in range(2):
                with self.subTest(episode=episode):
                    obs = s.reset()
                    s.game.rpc.debug("speed", factor=64)
                    ids = {}
                    for p, value in obs.items():
                        home = value["units"][0]
                        ids[p] = s.game.rpc.debug("spawn", type_id="hgry", player=p, x=home["x"], y=home["y"])[
                            "unit_ids"
                        ][0]
                    obs = s._observe_all()
                    initial = {p: next(u for u in obs[p]["units"] if u["unit_id"] == ids[p]) for p in ids}
                    actions = {
                        p: [Action(ids[p], "move", {"x": u["x"] + 500, "y": u["y"]})] for p, u in initial.items()
                    }
                    obs, done, info = s.step(actions)
                    self.assertFalse(done)
                    self.assertEqual(info["rejected"], {p: [] for p in ids})
                    self.assertEqual(len({o["game_time_seconds"] for o in obs.values()}), 1)
                    for p, value in obs.items():
                        self.assertTrue(all(u["owner"] == p for u in value["units"]))
                        unit = next(u for u in value["units"] if u["unit_id"] == ids[p])
                        self.assertGreater(unit["x"], initial[p]["x"] + 20)

    def test_every_slot_of_a_large_map_gets_its_own_start_location(self):
        # The lobby moves the slots it knows off their scripted locations; activated slots
        # must not land on an occupied one (a shared start displaces a hall and blocks mining).
        players = tuple(PlayerConfig(p, "human") for p in range(8))
        with self.session(players=players, map="(8)TwilightRuins.w3x") as s:
            for episode in range(2):
                with self.subTest(episode=episode):
                    obs = s.reset()
                    halls = {p: next((u["x"], u["y"]) for u in o["units"] if u["structure"]) for p, o in obs.items()}
                    for p, a in halls.items():
                        for q, b in halls.items():
                            if p < q:
                                self.assertGreater(math.dist(a, b), 2000, f"players {p} and {q} share a start")

    def test_nonzero_agent_result_and_alternate_map_resets(self):
        session = GameSession(GameConfig(map="(4)TurtleRock.w3x", players=(PlayerConfig(1),), render=False))
        self.addCleanup(session.close)
        for episode in range(3):
            session.reset()
            c = session.game.rpc
            c.debug("speed", factor=64)
            self.assertEqual(session.step({1: []})[2]["elapsed_ms"], 1000)
        # Result tracking must work when slot zero is not an agent.
        for u in c.observe(0)["units"]:
            c.debug("kill", unit_id=u["unit_id"])
        for _ in range(30):
            if session.step({1: []})[1]:
                break
        self.assertTrue(session.done)
        self.assertEqual(c.observe(1)["result"], "victory")
        self.assertEqual(c.observe(0)["result"], "defeat")


if __name__ == "__main__":
    unittest.main()
