"""Native site selection through step, without starting an AI controller."""

import tempfile
import unittest
from pathlib import Path

from tests.e2e.support import state
from wc3env import Action, GameConfig, GameSession, MatchSetup, PlayerConfig


class PlacementTest(unittest.TestCase):
    def start(self, race="human"):
        session = GameSession(
            GameConfig(
                players=(PlayerConfig(0, race), PlayerConfig(1, "human", control="computer")),
                setup=MatchSetup(seed=73),
                render=False,
                sound=False,
                step_ms=250,
            )
        )
        self.addCleanup(session.close)
        obs = session.reset()[0]
        session.debug("ai", player=1, paused=1)
        session.debug("speed", factor=64)
        session.debug("waitfloor", ms=1)
        session.debug("resources", player=0, gold=9999, lumber=9999)
        return session, obs

    def complete(self, session, actions, types, duration=120):
        seen = []
        for tick in range(duration * 4):
            views, done, info = session.step({0: actions if tick == 0 else []})
            self.assertEqual(info["rejected"][0], [])
            if tick == 0:
                placements = info["placements"][0]
            obs = views[0]
            seen.extend(e for e in obs["events"] if e["kind"] == "construct_finish")
            if len(seen) >= len(types) or done:
                break
        self.assertCountEqual([e["type_id"] for e in seen], types)
        return obs, placements

    def test_autoplaced_tree_of_life_can_entangle_observed_mine(self):
        session, obs = self.start("night_elf")
        worker = next(u for u in obs["units"] if u["type_id"] == "ewsp")
        mine = next(u for u in obs["units"] if u["type_id"] == "egol")
        session.debug("remove", unit_id=mine["unit_id"])
        existing_trees = {u["unit_id"] for u in obs["units"] if u["type_id"] == "etol"}
        obs = session.step({0: []})[0][0]
        target = next(u for u in obs["visible_enemies"] if u["type_id"] == "ngol")
        build = Action(worker["unit_id"], "build", dict(type_id="etol", x=target["x"], y=target["y"], auto_place=True))
        obs, sites = self.complete(session, [build], ["etol"], duration=240)
        tree = next(u for u in obs["units"] if u["type_id"] == "etol" and u["unit_id"] not in existing_trees)
        self.assertEqual(len(sites), 1)
        self.assertIn("Aent", {a["ability_id"] for a in tree["abilities"]})
        self.assertNotIn(worker["unit_id"], {u["unit_id"] for u in obs["units"]})
        cast = Action(tree["unit_id"], "cast", dict(order="entangle", target_id=target["unit_id"]))
        for tick in range(160):
            views, _, info = session.step({0: [cast] if tick == 0 else []})
            self.assertEqual(info["rejected"][0], [])
            obs = views[0]
            if any(u["type_id"] == "egol" for u in obs["units"]):
                break
        self.assertIn("egol", {u["type_id"] for u in obs["units"]})

    def test_haunted_mine_uses_native_target_order(self):
        session, obs = self.start("undead")
        worker = next(u for u in obs["units"] if u["type_id"] == "uaco")
        mine = next(u for u in obs["units"] if u["type_id"] == "ugol")
        session.debug("remove", unit_id=mine["unit_id"])
        obs = session.step({0: []})[0][0]
        target = next(u for u in obs["visible_enemies"] if u["type_id"] == "ngol")
        action = Action(worker["unit_id"], "build", dict(type_id="ugol", target_id=target["unit_id"]))
        obs, sites = self.complete(session, [action], ["ugol"])
        self.assertEqual(sites, [])
        built = next(u for u in obs["units"] if u["type_id"] == "ugol")
        self.assertEqual((built["x"], built["y"]), (target["x"], target["y"]))

    def test_selected_sites_replay_as_ordinary_build_orders(self):
        # No staging or setup overrides: replay must reproduce all simulation state.
        players = (PlayerConfig(0), PlayerConfig(1))
        builds = {
            "htow": ("hpea", "hhou"),
            "ogre": ("opeo", "otrb"),
            "unpl": ("uaco", "uzig"),
            "etol": ("ewsp", "emow"),
        }
        with tempfile.TemporaryDirectory(prefix="wc3-placement-") as folder:
            replay = Path(folder) / "placement.w3g"
            with GameSession(GameConfig(players=players, render=False, sound=False)) as session:
                views = session.reset()
                session.debug("speed", factor=64)
                session.debug("waitfloor", ms=1)
                trace = [state(views)]
                batches = {}
                for p, obs in views.items():
                    hall = next(u for u in obs["units"] if u["type_id"] in builds)
                    worker_type, building = builds[hall["type_id"]]
                    worker = next(u for u in obs["units"] if u["type_id"] == worker_type)
                    batches[p] = [
                        Action(
                            worker["unit_id"],
                            "build",
                            dict(type_id=building, x=hall["x"], y=hall["y"], auto_place=True),
                        )
                    ]
                completed = set()
                for tick in range(80):
                    views, _, info = session.step(batches if tick == 0 else {0: [], 1: []})
                    self.assertFalse(any(info["rejected"].values()))
                    completed.update(
                        p for p, obs in views.items() if any(e["kind"] == "construct_finish" for e in obs["events"])
                    )
                    trace.append(state(views))
                self.assertEqual(completed, {0, 1})
                session.save_replay(replay)
            with GameSession(GameConfig(map=str(replay), players=players, render=False, sound=False)) as session:
                self.assertEqual(state(session.reset()), trace[0])
                session.debug("speed", factor=64)
                session.debug("waitfloor", ms=1)
                for expected in trace[1:]:
                    views, _, _ = session.step({0: [], 1: []})
                    self.assertEqual(state(views), expected)

    def test_all_races_build_without_nonbuilder_orders(self):
        for race, worker_type, building in (
            ("human", "hpea", "hhou"),
            ("orc", "opeo", "otrb"),
            ("undead", "uaco", "uzig"),
            ("night_elf", "ewsp", "emow"),
        ):
            with self.subTest(race=race):
                session, obs = self.start(race)
                worker = next(u for u in obs["units"] if u["type_id"] == worker_type)
                hall = next(u for u in obs["units"] if u["structure"] and u["type_id"] not in ("ugol", "egol"))
                bystanders = {
                    u["unit_id"]: (u["x"], u["y"], u.get("order"))
                    for u in obs["units"]
                    if not u["structure"] and u["unit_id"] != worker["unit_id"]
                }
                action = Action(
                    worker["unit_id"], "build", dict(type_id=building, x=hall["x"], y=hall["y"], auto_place=True)
                )
                obs, sites = self.complete(session, [action], [building])
                self.assertEqual(len(sites), 1)
                for unit in obs["units"]:
                    if unit["unit_id"] in bystanders:
                        self.assertEqual((unit["x"], unit["y"], unit.get("order")), bystanders[unit["unit_id"]])
                session.close()

    def test_builds_in_one_step_and_pending_builds_choose_separate_sites(self):
        session, obs = self.start()
        workers = [u for u in obs["units"] if u["type_id"] == "hpea"]
        hall = next(u for u in obs["units"] if u["type_id"] == "htow")
        args = dict(type_id="hhou", x=hall["x"], y=hall["y"], auto_place=True)
        actions = [
            Action(workers[0]["unit_id"], "build", args),
            Action(workers[0]["unit_id"], "build", dict(args, queued=True)),
            Action(workers[1]["unit_id"], "build", args),
        ]
        # Earlier builds in the batch count as occupied, like a site a worker is walking to.
        _, _, info = session.step({0: actions})
        self.assertEqual(info["rejected"][0], [])
        sites = [(p["x"], p["y"]) for p in info["placements"][0]]
        self.assertEqual(len(set(sites)), 3)
        # Later steps avoid workers' current build orders and the Shift-queued sites handed out earlier.
        _, _, info = session.step({0: [Action(workers[2]["unit_id"], "build", args)]})
        self.assertNotIn((info["placements"][0][0]["x"], info["placements"][0][0]["y"]), sites)

    def test_new_search_accounts_for_building_in_next_observation(self):
        session, obs = self.start()
        worker = next(u for u in obs["units"] if u["type_id"] == "hpea")
        hall = next(u for u in obs["units"] if u["type_id"] == "htow")
        action = Action(worker["unit_id"], "build", dict(type_id="hhou", x=hall["x"], y=hall["y"], auto_place=True))
        obs, first = self.complete(session, [action], ["hhou"])
        obs, second = self.complete(session, [action], ["hhou"])
        self.assertNotEqual((first[0]["x"], first[0]["y"]), (second[0]["x"], second[0]["y"]))
        self.assertEqual(sum(u["type_id"] == "hhou" for u in obs["units"]), 2)

    def test_engine_rejects_no_site_and_invalid_builder_without_interrupting_other_orders(self):
        session, obs = self.start()
        hall = next(u for u in obs["units"] if u["type_id"] == "htow")
        worker = next(u for u in obs["units"] if u["type_id"] == "hpea")
        args = dict(type_id="hhou", x=hall["x"], y=hall["y"], auto_place=True)
        actions = [
            Action(hall["unit_id"], "build", args),
            Action(worker["unit_id"], "build", dict(args, x=999999, y=999999)),
            Action(worker["unit_id"], "move", {"x": hall["x"] - 400, "y": hall["y"] - 400}),
        ]
        views, _, info = session.step({0: actions})
        self.assertEqual(
            info["rejected"][0], [{"index": 0, "reason": "no_build_site"}, {"index": 1, "reason": "no_build_site"}]
        )
        self.assertEqual(info["placements"][0], [])
        unit = next(u for u in views[0]["units"] if u["unit_id"] == worker["unit_id"])
        self.assertEqual(unit["order"]["name"], "move")
