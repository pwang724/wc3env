"""A scenario: a staged start, a goal, an optional scripted opponent, and metrics of what happened.

Definitions are JSON files in `definitions/`. Places are named, not written as coordinates, so a
scenario works from either start location: "home", "enemy_home", "nearest_camp", "camp:3",
"toward:nearest_camp:700" (700 short of it, coming from home), "building:Goblin Merchant", plus dx/dy.

Optional fields: "race" (human | orc | undead | nightelf; the player's race, default human), "opponent"
(computer | idle | attack: keeps attack-moving at your army | raid: at your workers, else your hall),
"opponent_after_seconds" (the scripted opponent waits that long before its first order),
"finish_after_seconds" (play on that long once the finish condition holds: loot drops, skill points).
"""

from __future__ import annotations

import json
from math import hypot
from pathlib import Path

from ..game.facts import cleared_camps, learned_skills
from ..game.strength import observed_strength

DEFINITIONS = Path(__file__).with_name("definitions")
OPPONENT_ORDER_SECONDS = 5.0
COMPARE = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b}


def names():
    return sorted(p.stem for p in DEFINITIONS.glob("*.json"))


class Scenario:
    def __init__(self, name):
        d = json.loads((DEFINITIONS / f"{name}.json").read_text(encoding="utf-8"))
        self.name, self.title, self.goal, self.minutes = name, d["title"], d["goal"], d["minutes"]
        self.setup, self.checks = d["setup"], d["checks"]
        self.race = d.get("race", "human")
        self.opponent = d.get("opponent", "computer")  # computer | idle | attack | raid
        self.scripted_opponent = self.opponent in ("idle", "attack", "raid")
        self.opponent_after = d.get("opponent_after_seconds", 0)
        # Ends the scenario early: a metric name that is true once done, or {metric, op, value} like a check.
        self.finish = d.get("finish")
        self.finish_after = d.get("finish_after_seconds", 0)
        self.done_at = None  # game time the finish condition first held
        self.skip_seconds = d.get("skip_seconds", 0)  # game time run idle before the scenario starts (shops stock up)
        self.tags, self.samples, self.events, self.first_seen = {}, [], [], {}
        self.catalog = self.map = self.home = None  # supplied by the runner before staging
        self.cleared = set()
        self.last_opponent_order = -1e9
        self.enemy_base_seen = self.start_strength = self.started_at = self.live = None

    # ---- staging ---------------------------------------------------------------------------------

    def place(self, spec):
        m, home = self.map, self.home
        anchor = spec.get("at", "home")

        def named(word):
            if word == "home":
                return home
            if word == "enemy_home":
                return m.enemy_starts(home)[0]
            if word == "nearest_camp":
                return min(m.camps, key=lambda c: m.distance(home, c))
            if word.startswith("camp:"):
                return m.camp(int(word[5:]))
            if word.startswith("building:"):
                return m.nearest(home, [b for b in m.buildings if b["name"] == word[9:]])
            raise ValueError(f"unknown place {word!r}")

        if anchor.startswith("toward:"):
            word, short = anchor[len("toward:") :].rsplit(":", 1)
            target = named(word)
            gap = m.distance(home, target)
            k = max(0.0, (gap - float(short)) / gap)
            point = {"x": home["x"] + (target["x"] - home["x"]) * k, "y": home["y"] + (target["y"] - home["y"]) * k}
        else:
            point = named(anchor)
        return {"x": float(point["x"] + spec.get("dx", 0)), "y": float(point["y"] + spec.get("dy", 0))}

    def stage(self, session, catalog, mapinfo, obs):
        """Stage from map data and the initial observation, independently of the agent."""
        self.catalog, self.map = catalog, mapinfo
        hall = next(u for u in obs["units"] if u["structure"])
        self.home = mapinfo.home(hall)
        watched = None  # where the staged party stands: the camera goes there, the stock map starts on the hall
        for op in self.setup:
            kind, owner = op["op"], 1 if op.get("owner") == "enemy" else 0
            if kind == "resources":
                session.debug("resources", player=0, gold=op.get("gold", 0), lumber=op.get("lumber", 0))
            elif kind == "ai":
                session.debug("ai", player=1, paused=1 if op.get("paused", True) else 0)
            elif kind == "item":
                session.debug("item", type_id=op["type"], **self.place(op))
            elif kind in ("spawn", "hero"):
                where = self.place(op)
                ids = session.debug("spawn", type_id=op["type"], player=owner, n=op.get("n", 1), **where)["unit_ids"]
                tag = op.get("tag", "enemy" if owner else "army")
                self.tags.setdefault(tag, []).extend(ids)
                if tag == "hero" or (tag == "army" and watched is None):
                    watched = where
                for uid in ids:
                    if op.get("level"):
                        session.debug("level", unit_id=uid, level=op["level"])
                    for item in op.get("items", []):
                        session.debug("give", unit_id=uid, type_id=item)
                    if op.get("hp"):
                        session.debug("hp", unit_id=uid, value=op["hp"])
                    if op.get("mana") is not None:
                        session.debug("mana", unit_id=uid, value=op["mana"])
            else:
                raise ValueError(f"unknown setup op {kind!r}")
        if watched:
            session.debug("camera", **watched)

    # ---- watching --------------------------------------------------------------------------------

    def saw(self, obs):
        """Every observation the agent acts on passes through here once, from staging on."""
        now, catalog, units = obs["game_time_seconds"], self.catalog, obs["units"]
        if not obs.get("result"):
            self.live = obs
        self.cleared.update(cleared_camps(self.map, obs))
        workers = [u for u in units if not u["structure"] and catalog.units.get(u["type_id"], {}).get("builds")]
        army = [u for u in units if not u["structure"] and u not in workers]
        if self.started_at is None:
            self.started_at = now
        types = {}
        for u in units:
            if u["hp"] > 0:
                types[u["type_id"]] = types.get(u["type_id"], 0) + 1
        self.samples.append(
            {
                "t": now,
                "types": types,  # living own units by type: Militia present, an Ancient uprooted
                # An uprooted Ancient loses the game's structure flag: the reference says structure, the game says not.
                "uprooted": sum(
                    1 for u in units if not u["structure"] and catalog.units.get(u["type_id"], {}).get("structure")
                ),
                "gold": obs["player"]["gold"],
                "food_left": obs["player"]["food_cap"] - obs["player"]["food_used"],
                "food_cap": obs["player"]["food_cap"],
                "idle_workers": sum(w["order"] is None for w in workers),
                "workers_outside": len(workers),
                "workers": len(workers)  # plus workers inside mines and buildings
                + sum(1 for u in obs.get("inside", []) if catalog.units.get(u["type_id"], {}).get("builds")),
                "strength": observed_strength(army, catalog),
            }
        )
        for u in units:
            if not (u["structure"] and u.get("state") == "constructing"):
                self.first_seen.setdefault(u["type_id"], now)
        self.events += [{**e, "t": now} for e in obs["events"]]
        enemy_players = {p["id"] for p in obs["players"] if p["kind"] == "player" and p["relation"] == "enemy"}
        if self.enemy_base_seen is None and any(
            u["structure"] and u["owner"] in enemy_players for u in obs["visible_enemies"]
        ):
            self.enemy_base_seen = now
        if self.start_strength is None:
            mine = [u for u in units if u["unit_id"] in self.tags.get("army", []) + self.tags.get("hero", [])]
            self.start_strength = observed_strength(mine, catalog) or None

    def opponent_actions(self, obs):
        """The scripted opponent's actions this step (player 1): `attack` keeps attack-moving at our army,
        `raid` at our workers (at our hall once none are in the open: Militia, Burrows)."""
        now, catalog = obs["game_time_seconds"], self.catalog
        if self.opponent not in ("attack", "raid") or now - self.last_opponent_order < OPPONENT_ORDER_SECONDS:
            return []
        if self.started_at is not None and now - self.started_at < self.opponent_after:
            return []
        workers = [u for u in obs["units"] if not u["structure"] and catalog.units.get(u["type_id"], {}).get("builds")]
        army = [u for u in obs["units"] if not u["structure"] and u not in workers]
        target = army if self.opponent == "attack" else workers
        if target:
            x, y = sum(u["x"] for u in target) / len(target), sum(u["y"] for u in target) / len(target)
        elif self.opponent == "raid":
            x, y = self.home["x"], self.home["y"]
        else:
            return []
        self.last_opponent_order = now
        return [
            {"unit_id": uid, "command": "attack", "arguments": {"x": x, "y": y}}
            for uid in self.tags.get("enemy", [])[:64]
        ]

    def finished(self, obs):
        if not self.finish:
            return False
        if self.done_at is None:
            if isinstance(self.finish, str):
                done = bool(self.metric(self.finish, obs))
            else:
                value = self.metric(self.finish["metric"], obs)
                done = value is not None and COMPARE[self.finish["op"]](value, self.finish["value"])
            if not done:
                return False
            self.done_at = obs["game_time_seconds"]
        return obs["game_time_seconds"] - self.done_at >= self.finish_after

    # ---- scoring ---------------------------------------------------------------------------------

    def _dt(self, test):
        return round(sum(b["t"] - a["t"] for a, b in zip(self.samples, self.samples[1:]) if test(a)), 1)

    def _count(self, kind, test=lambda e: True):
        return sum(1 for e in self.events if e["kind"] == kind and test(e))

    def _time(self, kind, test=lambda e: True):
        return next((round(e["t"], 1) for e in self.events if e["kind"] == kind and test(e)), None)

    def metric(self, name, obs):
        catalog, units = self.catalog, obs["units"]
        mine = set(self.tags.get("army", []) + self.tags.get("hero", []))
        heroes = [u for u in units if u["hero"]]
        key, _, arg = name.partition(":")
        if key == "workers":
            return self.samples[-1]["workers"]
        if key in ("gold_mined", "lumber_total", "units_killed", "units_trained", "total"):
            return obs["score"].get(key, 0)
        if key == "idle_worker_seconds":
            return self._dt(lambda s: s["idle_workers"] > 0)
        if key == "supply_blocked_seconds":
            return self._dt(lambda s: s["food_left"] <= 0 and s["food_cap"] < 100)
        if key == "average_unspent_gold":
            return round(sum(s["gold"] for s in self.samples) / max(1, len(self.samples)))
        if key == "first_time":  # first_time:halt = when the first finished Altar existed
            return self.first_seen.get(arg)
        if key == "hero_time":
            return next(
                (
                    t
                    for raw, t in sorted(self.first_seen.items(), key=lambda kv: kv[1])
                    if catalog.units.get(raw, {}).get("hero")
                ),
                None,
            )
        if key == "camp_cleared":
            target = self.place({"at": arg or "nearest_camp"})
            camp = min(self.map.camps, key=lambda c: hypot(c["x"] - target["x"], c["y"] - target["y"]))
            return camp["number"] in self.cleared
        if key == "units_lost":
            return self._count(
                "death",
                lambda e: (
                    e["owner"] == 0
                    and not catalog.units.get(e["type_id"], {}).get("structure")
                    and not catalog.units.get(e["type_id"], {}).get("builds")
                ),
            )
        if key == "workers_lost":
            return self._count(
                "death", lambda e: e["owner"] == 0 and bool(catalog.units.get(e["type_id"], {}).get("builds"))
            )
        if key == "structures_lost":
            return self._count(
                "death", lambda e: e["owner"] == 0 and catalog.units.get(e["type_id"], {}).get("structure")
            )
        if key == "hero_alive":
            return bool(heroes)
        if key == "hero_level":
            return max((h["level"] for h in heroes), default=0)
        if key == "hero_health_percent":
            return round(100 * min((h["hp"] / h["max_hp"] for h in heroes), default=0))
        if key == "unspent_skill_points":
            skills = learned_skills(catalog, obs)
            return None if not heroes else sum(h["level"] - sum(skills.get(h["unit_id"], {}).values()) for h in heroes)
        if key == "items_picked_up":
            return self._count("item_pickup")
        if key == "items_carried":
            return len(obs["inventory"])
        if key == "items_used":
            return self._count("item_use")
        if key == "items_bought":
            return self._count("item_sold", lambda e: e["buyer_id"] in mine)
        if key == "army_kept_percent":
            alive = [u for u in units if u["unit_id"] in mine]
            return round(100 * observed_strength(alive, catalog) / self.start_strength) if self.start_strength else None
        if key == "enemy_army_destroyed_percent":
            staged = self.tags.get("enemy", [])
            dead = {e["unit_id"] for e in self.events if e["kind"] == "death"}
            return round(100 * sum(uid in dead for uid in staged) / len(staged)) if staged else None
        if key == "enemy_base_seen_time":
            return self.enemy_base_seen
        if key == "upgrade_started_time":
            return self._time("upgrade_start")
        if key == "researches_done":
            return self._count("research_finish")
        if key == "expansion_started_time":
            halls = {raw for raw, u in catalog.units.items() if u["structure"] and u["food_made"] >= 10}
            return self._time("construct_start", lambda e: e["type_id"] in halls)
        if key == "seconds":  # until the finish condition held, not the extra time played after it
            return round(obs["game_time_seconds"] if self.done_at is None else self.done_at, 1)
        if key == "fewest_workers":  # on the map: this drops while Peons are inside Burrows
            return min(s["workers_outside"] for s in self.samples)
        if key == "present_seconds":  # present_seconds:hmil = how long any Militia existed
            return self._dt(lambda s: s["types"].get(arg, 0) > 0)
        if key == "uprooted_seconds":
            return self._dt(lambda s: s["uprooted"] > 0)
        if key == "count":  # count:hhou = finished Farms standing now
            return sum(1 for u in units if u["type_id"] == arg and u["hp"] > 0 and u.get("state") != "constructing")
        if key == "food_cap":
            return obs["player"]["food_cap"]
        if key == "structure_health_percent":  # the worst-off finished structure
            standing = [u for u in units if u["structure"] and u["hp"] > 0 and u.get("state") != "constructing"]
            return round(100 * min((u["hp"] / u["max_hp"] for u in standing), default=0))
        if key == "structures_started":
            return self._count("construct_start")
        raise ValueError(f"unknown metric {name!r}")

    def score(self, obs):
        """Checks against the final observation. Once the game has ended it lists none of our units,
        so unit-state checks read the last observation before that; event counts are unaffected."""
        if obs.get("result") and self.live is not None:
            obs = self.live
        results = []
        for check in self.checks:
            value = self.metric(check["metric"], obs)
            ok = None
            if "op" in check:
                ok = value is not None and COMPARE[check["op"]](value, check["value"])
            results.append({**check, "measured": value, "ok": ok})
        return results
