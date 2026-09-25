"""Macro memory: observed history, who commands each unit, and feedback on submitted orders."""

import math

from ..control import Control
from ..game.facts import cleared_camps, learned_skills
from ..game.policies import FIGHT_RANGE
from ..game.references import References
from ..game.workers import MINES, is_worker
from ..game.world import town_hall
from .outcomes import QUIET, Outcomes

STALL_SECONDS = 2.0  # game time without hit points rising before construction counts as stopped
STARTS = {"construct_start": "state", "upgrade_start": "state", "train_start": "queue", "research_start": "queue"}


class MacroMemory:
    """Macro history and standing orders, updated once per environment observation."""

    def __init__(self, catalog, mapinfo, player=0):
        self.catalog, self.map, self.player = catalog, mapinfo, player
        # Written by `update`, from observations.
        self.race = self.home = self.hall = None  # race name; the map's start location; our first hall's unit id
        self.started = {}  # (unit_id, "state" | "queue") -> game time the current item began
        self.last_hp = {}  # structure under construction -> (highest hit points seen, game time they last rose)
        self.stalled = set()  # structures whose hit points have not risen for STALL_SECONDS: nobody is building
        self.gathering = {}  # worker unit_id -> "gold" | "lumber", as last seen
        self.gold_mine = {}  # worker unit_id -> the mine it last headed for
        self.cleared = set()  # camp numbers
        self.enemy_structures = {}  # unit_id -> last sighting
        self.enemy_seen_at = None  # game time any enemy player's unit or structure was last in view
        self.enemy_fought_at = None  # game time our army was last within fighting range of the enemy player
        self.tech = {}  # research id -> level owned, counted from research_finish events
        self.skills = {}  # hero unit id -> {ability id: level}, from the heroes' abilities
        self.idle_since = {}  # hero unit id -> game time it was first seen with no order, while it stays idle
        self.events = []  # every event since the last macro turn (one observation holds one step's)
        self.events_lost = 0
        self.control = Control()  # groups and deferred orders, changed by the order parser
        self.known_units = {}  # last observed own units; absence alone does not mean death
        self.unavailable = {}  # confirmed death or loss of ownership
        self.fallen = {}  # dead own heroes, as last seen, until they are revived
        self.references = References(catalog)
        self.outcomes = Outcomes(catalog, self.references)
        # Feedback and combat activity since the previous macro request.
        # Consumed when included in a new request; later arrivals remain for the next one.
        self.notes = []
        self.fighting = set()

    def consume(self):
        """Clear events and feedback included in the request that is starting now.

        Events arriving while the model thinks stay available for its next request.
        Return the feedback snapshot for this request's log.
        """
        told, self.notes = self.notes, []
        self.events, self.events_lost, self.fighting = [], 0, set()
        return told

    def submitted(self, actions, game_time, turns=None, rejected=()):
        """Submission is an acknowledgement, not proof the game executed an order.

        `turns` is the macro turn behind each action (None for micro's)."""
        rows = self.outcomes.submitted(actions, game_time, turns, rejected)
        self.notes.extend(self.outcomes.feedback(r) for r in rows)

    def update(self, obs):
        """Fold one observation in; called on every step's observation, in order."""
        now, units = obs["game_time_seconds"], obs["units"]
        self.references.remember(obs)
        self._track_units(obs)
        if self.race is None:
            worker = next((u for u in units if self.catalog.units.get(u["type_id"], {}).get("builds")), None)
            hall = town_hall(obs, self.catalog)
            if worker:
                self.race = self.catalog.units[worker["type_id"]]["race"]
            if hall:
                self.home, self.hall = self.map.home(hall), hall["unit_id"]
        self.stamp(obs)
        self.events += obs["events"]
        self.events_lost += obs["events_lost"]
        for event in obs["events"]:
            if event["kind"] == "summon" and event.get("summoned_id"):
                self.control.adopt(event["unit_id"], event["summoned_id"])
        for event in obs["events"]:
            if event["kind"] == "research_finish":
                self.tech[event["type_id"]] = self.tech.get(event["type_id"], 0) + 1
        self.skills = learned_skills(self.catalog, obs)
        heroes = [u for u in units if u["hero"] and u["hp"] > 0]
        self.idle_since = {h["unit_id"]: self.idle_since.get(h["unit_id"], now) for h in heroes if not h["order"]}
        for row in self.outcomes.update(obs):
            if row["status"] not in QUIET:
                self.notes.append(self.outcomes.feedback(row))
        self._track_construction(units, now)
        self._track_gathering(obs)
        self.cleared.update(cleared_camps(self.map, obs))
        enemies = {p["id"] for p in obs["players"] if p["kind"] == "player" and p["relation"] == "enemy"}
        if any(u["owner"] in enemies for u in obs["visible_enemies"]):
            self.enemy_seen_at = now
        fighters = [u for u in units if u["hp"] > 0 and not u["structure"] and not is_worker(u, self.catalog)]
        if any(
            math.hypot(e["x"] - u["x"], e["y"] - u["y"]) <= FIGHT_RANGE
            for e in obs["visible_enemies"]
            if e["owner"] in enemies and e["hp"] > 0
            for u in fighters
        ):
            self.enemy_fought_at = now
        for u in obs["visible_enemies"]:
            if u["owner"] in enemies and u["structure"]:
                self.enemy_structures[u["unit_id"]] = {**u, "seen": now}
        for event in obs["events"]:
            if event["kind"] == "death":
                self.enemy_structures.pop(event["unit_id"], None)
                self.gathering.pop(event["unit_id"], None)
                self.gold_mine.pop(event["unit_id"], None)

    def _track_units(self, obs):
        lost = {e["unit_id"]: "confirmed dead" for e in obs["events"] if e["kind"] == "death"}
        for unit in obs["visible_enemies"]:
            if unit["unit_id"] in self.known_units and unit["owner"] != self.player:
                lost[unit["unit_id"]] = "no longer owned"
        for unit in obs["units"]:
            uid = unit["unit_id"]
            if unit["hp"] <= 0:
                lost[uid] = "confirmed dead"
            else:
                self.known_units[uid] = unit
                self.unavailable.pop(uid, None)
                self.fallen.pop(uid, None)
        for unit in obs.get("inside", []):  # in a mine or building: last full record, current order
            uid = unit["unit_id"]
            base = self.known_units.get(uid) or dict(structure=False, hero=False, owner=self.player, abilities=[])
            self.known_units[uid] = {**base, **unit}
            self.unavailable.pop(uid, None)
        for e in obs["events"]:  # a hero seen only in its death event (killed before we observed it)
            if e["kind"] == "death" and e.get("owner") == self.player and e["unit_id"] not in self.known_units:
                if self.catalog.units.get(e.get("type_id"), {}).get("hero"):
                    self.fallen[e["unit_id"]] = {
                        "unit_id": e["unit_id"],
                        "type_id": e["type_id"],
                        "hero": True,
                        "level": "?",
                    }
        for uid, reason in lost.items():
            if uid not in self.known_units:
                continue
            unit = self.known_units.pop(uid)
            self.unavailable[uid] = reason
            if unit.get("hero") and reason == "confirmed dead":
                self.fallen[uid] = unit
            if self.control.lose(uid):
                self.notes.append(f"Deferred orders for {self.references.label(unit)} cancelled: {reason}.")

    def stamp(self, obs):
        """Remember when production started; safe to call on every observation."""
        for event in obs["events"]:
            if event["kind"] in STARTS:
                self.started[(event["unit_id"], STARTS[event["kind"]])] = obs["game_time_seconds"]

    def _track_construction(self, units, now):
        """A structure under construction whose hit points stopped rising has nobody building it.

        Realtime observations arrive every tenth of a second, too often for hit points to rise between
        two of them, so construction counts as stopped only after STALL_SECONDS without a rise."""
        seen = {}
        for u in units:
            if u["structure"] and u.get("state") == "constructing":
                hp, rose = self.last_hp.get(u["unit_id"], (-1, now))
                seen[u["unit_id"]] = (u["hp"], now) if u["hp"] > hp else (hp, rose)
        self.stalled = {uid for uid, (_, rose) in seen.items() if now - rose >= STALL_SECONDS}
        self.last_hp = seen

    def _track_gathering(self, obs):
        """Carrying resources home targets the hall, so remember what each worker last gathered."""
        targets = {u["unit_id"]: u for u in obs["visible_enemies"] + obs["units"]}
        trees = {d["id"] for d in obs["destructables"] if d["resource"] == "lumber"}
        for w in obs["units"] + obs.get("inside", []):
            order = w.get("order")
            if not order or order["name"] not in ("harvest", "resumeharvesting", "returnresources"):
                continue
            if targets.get(order["target_id"], {}).get("type_id") in MINES:
                self.gathering[w["unit_id"]] = "gold"
                self.gold_mine[w["unit_id"]] = order["target_id"]
            elif order["target_id"] in trees:
                self.gathering[w["unit_id"]] = "lumber"

    def seconds_left(self, unit, slot, total, now):
        began = self.started.get((unit["unit_id"], slot))
        return None if began is None else max(0, round(total - (now - began)))
