"""Bounded observation and submitted-intent history, with no tactical rules."""

from collections import defaultdict, deque
from math import hypot

from ..game.policies import MOVE_SETTLE, STUCK_DISTANCE, went_nowhere
from ..game.world import opponents, troops

RECENT_LOSS_SECONDS = 2.0  # window for a hero's current rate of health loss


class MicroMemory:
    def __init__(self, catalog=None):
        self.catalog = catalog
        self.previous = None
        self.events = deque(maxlen=100)
        self.unit_orders = defaultdict(lambda: deque(maxlen=3))
        self.submitted_orders = defaultdict(lambda: deque(maxlen=3))
        self.last_issued = {}
        self.last_seen = {}
        self.deltas = []
        self.health = {}  # unit id -> recent (time, hp) samples while continuously observed
        self.last_damage = {}
        self.summons = {}  # summon events outlive the bounded combat timeline
        self.buys = {}  # (unit id, item type) -> game time the unit last tried to buy it
        self.forms = defaultdict(dict)  # unit id -> {type id: its abilities when last seen as that type}
        self.buffs = {}  # unit id -> {buff id: game time it was first seen on the unit}
        self.moves = {}  # unit id -> the last move it was told: {"to": (x, y), "from": (x, y)}
        self.dead_ends = {}  # unit id -> move targets that left it idle where it stood, until it moves
        # unit id -> {spell's cast order: {"autocast": bool, "active": bool}}, from the toggles sent (the game does
        # not report them). Keyed by cast order ('heal', 'defend'): abilities sharing orders share the state.
        self.toggles = defaultdict(dict)
        self.toggle_orders = {}  # order name -> (the spell's cast order, kind) for autocast and on/off orders
        for a in ((catalog.abilities if catalog else {}) or {}).values():
            orders = a.get("levels", {}).get("1", {}).get("orders", [])
            cast = next((o["name"] for o in orders if o["kind"] == "cast"), None)
            switchable = any(o["kind"] == "deactivate" for o in orders)
            for o in orders:
                if cast and (o["kind"] != "cast" or switchable):
                    self.toggle_orders.setdefault(o["name"], (cast, o["kind"]))

    def ingest(self, obs):
        now = obs["game_time_seconds"]
        old = (
            {u["unit_id"]: u for u in self.previous["units"] + self.previous["visible_enemies"]}
            if self.previous
            else {}
        )
        self.deltas = [
            {
                "unit_id": u["unit_id"],
                "hp_change": round(u["hp"] - old[u["unit_id"]]["hp"], 2),
                "dx": round(u["x"] - old[u["unit_id"]]["x"], 2),
                "dy": round(u["y"] - old[u["unit_id"]]["y"], 2),
                "over_seconds": round(now - self.previous["game_time_seconds"], 3),
            }
            for u in obs["units"] + obs["visible_enemies"]
            if u["unit_id"] in old
        ]
        self.events.extend({"observed_at": now, **e} for e in obs["events"])
        current = {u["unit_id"]: u for u in obs["units"] + obs["visible_enemies"]}
        self.health = {uid: rows for uid, rows in self.health.items() if uid in current}
        self.last_damage = {uid: time for uid, time in self.last_damage.items() if uid in current}
        for uid, unit in current.items():
            rows = self.health.setdefault(uid, deque())
            if rows and unit["hp"] < rows[-1][1]:
                self.last_damage[uid] = now
            if not rows or rows[-1][0] != now:
                rows.append((now, unit["hp"]))
            while len(rows) > 1 and rows[1][0] <= now - 5:
                rows.popleft()
        self.buffs = {
            uid: {b: self.buffs.get(uid, {}).get(b, now) for b in unit.get("buffs", [])}
            for uid, unit in current.items()
            if unit.get("buffs")
        }
        for event in obs["events"]:
            if event["kind"] != "summon" or not event.get("summoned_id"):
                continue
            summoner = current.get(event.get("unit_id"), old.get(event.get("unit_id"), {}))
            durations = set()
            for live in summoner.get("abilities", []) if self.catalog else []:
                level = self.catalog.abilities.get(live["ability_id"], {}).get("levels", {}).get(str(live["level"]), {})
                if event["type_id"] in level.get("effects", {}).get("UnitID", "").split(",") and level.get(
                    "duration_seconds"
                ):
                    durations.add(level["duration_seconds"])
            self.summons[event["summoned_id"]] = {
                "summoner_id": event.get("unit_id"),
                "observed_at": now,
                "lifetime_seconds": next(iter(durations)) if len(durations) == 1 else None,
            }
        self.last_seen.update({u["unit_id"]: {"last_seen_at": now, **u} for u in opponents(obs)})
        deaths = {e["unit_id"] for e in obs["events"] if e["kind"] == "death"}
        self.last_seen = {
            uid: u for uid, u in self.last_seen.items() if uid not in deaths and now - u["last_seen_at"] < 12
        }
        # Workers disappear inside mines/buildings; absence does not erase their instruction history.
        for uid in deaths:
            self.summons.pop(uid, None)
            for history in (
                self.last_issued,
                self.unit_orders,
                self.submitted_orders,
                self.moves,
                self.dead_ends,
                self.forms,
            ):
                history.pop(uid, None)
        self._check_moves(current, now)
        self.previous = obs

    def _check_moves(self, current, now):
        for uid in list(self.dead_ends):
            unit, (x, y) = current.get(uid), self.dead_ends[uid]["at"]
            if unit is None or hypot(unit["x"] - x, unit["y"] - y) >= STUCK_DISTANCE:
                del self.dead_ends[uid]  # it has moved on; old dead ends no longer apply
        for uid, move in list(self.moves.items()):
            unit = current.get(uid)
            if unit is None or unit["order"] is not None or now - move["at"] < MOVE_SETTLE:
                continue  # still going, out of sight, or not yet executed: judge it later
            del self.moves[uid]
            if went_nowhere(move, unit):
                entry = self.dead_ends.setdefault(uid, {"at": move["from"], "targets": []})
                entry["targets"].append(move["to"])

    def observe_orders(self, capabilities):
        for cap in capabilities.values():
            history = self.unit_orders[cap["unit_id"]]
            if not history or history[-1]["order"] != cap["order"]:
                history.append(
                    {
                        "observed_at": cap["observed_at"],
                        "order": cap["order"],
                    }
                )

    @staticmethod
    def _recent_loss(rows, now, seconds=RECENT_LOSS_SECONDS):
        """(health lost, over seconds) between the first sample in the last `seconds` and now; healing counts."""
        recent = [row for row in rows if row[0] >= now - seconds]
        if len(recent) < 2:
            return (0, 0)
        return (max(0, recent[0][1] - recent[-1][1]), round(now - recent[0][0], 2))

    def remember_forms(self, capabilities, units):
        """Each unit's abilities in its current form, kept after it changes form (a bear keeps its Rejuvenation)."""
        for cap in capabilities.values():
            unit = units.get(cap["unit_id"])
            if unit:
                self.forms[cap["unit_id"]][unit["type_id"]] = [
                    {**ability, "observed_at": cap["observed_at"]} for ability in cap["abilities"]
                ]

    def record(self, game_time, submitted):
        """Remember what each unit was just told, so its timeline can say so next call."""
        now = game_time
        where = {u["unit_id"]: u for u in (self.previous or {}).get("units", [])}
        for action in submitted:
            uid, args = action["unit_id"], action["arguments"]
            if action["command"] == "move" and uid in where and not args.get("queued"):
                self.moves[uid] = {"to": (args["x"], args["y"]), "from": (where[uid]["x"], where[uid]["y"]), "at": now}
            elif not args.get("queued"):
                self.moves.pop(uid, None)
            toggle = self.toggle_orders.get(args.get("order")) if action["command"] == "cast" else None
            if toggle:
                spell, kind = toggle
                state = self.toggles[uid].setdefault(spell, {})
                if kind in ("enable_autocast", "disable_autocast"):
                    state["autocast"] = kind == "enable_autocast"
                else:
                    state["active"] = kind == "cast"
            if action["command"] == "buy":
                self.buys[(uid, args.get("item_type_id"))] = now
            self.last_issued[action["unit_id"]] = {"submitted_at": now, "action": action}
            history = self.submitted_orders[action["unit_id"]]
            if history and history[-1]["action"] == action:
                history[-1] = self.last_issued[action["unit_id"]]
            else:
                history.append(self.last_issued[action["unit_id"]])

    def context(self, obs):
        """Everything the request writer reads: recent events, movement and hit-point changes, enemies
        that walked out of sight, and each living unit's order timeline."""
        return {
            "recent_observed_events": list(self.events),
            "observed_changes": self.deltas,
            "health": {
                uid: {
                    "over_seconds": round(obs["game_time_seconds"] - rows[0][0], 2),
                    "hp_lost": round(sum(max(0, a[1] - b[1]) for a, b in zip(rows, list(rows)[1:])), 2),
                    "hp_gained": round(sum(max(0, b[1] - a[1]) for a, b in zip(rows, list(rows)[1:])), 2),
                    # Net loss over the last couple of seconds: a sudden focus shows at once, not averaged away.
                    "recent_net_loss": self._recent_loss(rows, obs["game_time_seconds"]),
                    **(
                        {"seconds_since_hp_loss": round(obs["game_time_seconds"] - self.last_damage[uid], 2)}
                        if uid in self.last_damage
                        else {}
                    ),
                }
                for uid, rows in self.health.items()
            },
            "summons": self.summons,
            "buffs": self.buffs,
            "toggles": self.toggles,
            "last_seen_enemies": list(self.last_seen.values()),
            "unit_history": {
                str(u["unit_id"]): {
                    "last_submitted": self.last_issued.get(u["unit_id"]),
                    "submitted_orders": list(self.submitted_orders[u["unit_id"]]),
                    "observed_order_changes": list(self.unit_orders[u["unit_id"]]),
                }
                for u in troops(obs)
            },
        }
