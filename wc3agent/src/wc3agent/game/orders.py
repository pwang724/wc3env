"""Translate macro commands to game actions. Unit orders replace; explicit `queue` appends.

Each line is parsed once into one Command per actor, with stable names resolved to the game's IDs.
Commands are applied in order to the agent's Control. A Command whose actor is not observable waits
there and becomes actions against the observation in which the actor returns.
Command syntax lives in macro.prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import hypot

from .references import LEGACY, TOKEN, objects
from .workers import harvest_kinds
from .world import targets, town_hall, trading_shops

VERBS = (
    "take",
    "train",
    "upgrade",
    "research",
    "cancel",
    "build",
    "rally",
    "gold",
    "lumber",
    "repair",
    "attack",
    "move",
    "stop",
    "learn",
    "revive",
    "cast",
    "buy",
    "use",
    "give",
    "sell",
    "drop",
)
QUEUEABLE = ("move", "stop", "attack", "smart", "harvest", "build", "cast", "use_item")
QUEUE_VERBS = {"take", "build", "gold", "lumber", "repair", "attack", "move", "stop", "cast", "use"}
PRODUCTION = ("train", "research", "upgrade")  # production has its own queue and replaces no unit orders
GATHERING = ("harvest", "resumeharvesting", "returnresources")  # repeats until interrupted
# The reference lists only an ability's "on" order; these are the "off" sides a base defence needs.
OFF_ORDERS = {"uproot": "unroot", "backtowork": "townbelloff", "root": "root"}
_RACE_MINES = {"undead": "ugol", "nightelf": "egol"}  # what a hall's rally to "gold" aims at
POINT = re.compile(r"\b(?:at|near)\s+(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)")
NEAR = re.compile(rf"\bnear\s+({TOKEN})", re.I)  # build near an observed object
TARGET = re.compile(rf"\b(?:on|from|to)\s+({TOKEN})", re.I)


def _key(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _point(text):
    match = POINT.search(text)
    return {"x": float(match[1]), "y": float(match[2])} if match else None


@dataclass(frozen=True)
class Command:
    """One macro order for one actor, e.g. `buy archmage1 from vault1 Potion of Healing`."""

    verb: str
    actor: int
    text: str  # as written for this actor, for feedback
    name: str = ""  # what to make, learn, cast, buy, or rally to
    target: int | None = None
    point: dict | None = None
    near: int | None = None
    count: int = 1
    slot: int | None = None
    queued: bool = False


@dataclass
class _Batch:
    """One observation, indexed for resolving references. Gameplay legality is the engine's."""

    obs: dict
    own: dict
    entities: dict
    targets: set


class Orders:
    def __init__(self, world):
        """Translate against observation facts; parsing never queries the game."""
        self.world, self.catalog = world, world.catalog
        self.references, self.control = world.references, world.control
        self._last = {}  # per reply; see parse

    def _batch(self, obs):
        own = {u["unit_id"]: u for u in obs["units"] if u["hp"] > 0}
        return _Batch(obs, own, objects(obs, self.world.known_units), targets(obs))

    # ---- names -----------------------------------------------------------------------------------

    def _named(self, name, candidates, label):
        """The id among `candidates` (raw ids) whose name matches, exactly and then by prefix."""
        wanted = _key(name)
        if name.strip() in candidates:
            return name.strip()
        names = {raw: _key(label(raw)) for raw in candidates}
        exact = [raw for raw, n in names.items() if n == wanted]
        close = exact or [raw for raw, n in names.items() if wanted and (n.startswith(wanted) or wanted.startswith(n))]
        return close[0] if close else None

    def _research(self, name, building):
        for raw in self.catalog.units[building["type_id"]]["researches"]:
            levels = self.catalog.upgrades[raw]["levels"]
            if raw == name.strip() or any(_key(level["name"]) == _key(name) for level in levels):
                return raw
        return None

    def _ability(self, name, unit, *, learn=False):
        """A skill to learn, or one to cast: an observed ability or any skill of the hero's type.
        Whether a skill is learned yet is the engine's to decide, so a reply can learn then cast it."""
        skills = self.catalog.units[unit["type_id"]]["potential_hero_abilities"]
        abilities = (
            skills
            if learn
            else [a["ability_id"] for a in unit.get("abilities", []) if a["ability_id"] in self.catalog.abilities]
            + skills
        )
        return self._named(name, abilities, lambda raw: self.catalog.abilities[raw]["levels"]["1"]["name"])

    # ---- parsing ---------------------------------------------------------------------------------

    def parse(self, text, obs, turn=None):
        """(actions, notes) for a reply, applying its lines in order to Control."""
        batch = self._batch(obs)
        actions, notes = [], []
        self._last = {}  # actor -> verb of its latest unit order in this reply
        for line in text.splitlines():
            line = line.strip().lstrip("-*• ").strip("`")
            queued = line.lower().startswith("queue ")
            words = (line.split(maxsplit=1)[1] if queued else line).split()
            verb = words[0].lower() if words else ""
            if queued and verb not in QUEUE_VERBS:
                notes.append(f"{line!r}: queue is for unit orders; production has its own queue")
                continue
            if verb in ("group", "disband"):
                try:
                    actions.extend(self._group(verb, line, batch, turn, notes))
                except ValueError as problem:
                    notes.append(f"{line!r}: {problem}")
                continue
            if len(words) < 2 or verb not in VERBS or not re.fullmatch(TOKEN, words[1], re.I):
                continue  # prose, such as a plan sentence that happens to start with "Build"
            for command in self._commands(verb, words, queued, batch, notes, line):
                actions.extend(self._apply(command, batch, turn, notes))
        self.control.prune()
        return actions, notes

    def _commands(self, verb, words, queued, batch, notes, line):
        """One Command per actor. Leading names are actors; take's second name, and a name after
        on/from, is the target."""
        end = 2
        if verb not in ("take", "revive"):
            while end < len(words) and re.fullmatch(TOKEN, words[end], re.I):
                end += 1
        tail = " ".join(words[end:])
        target = (
            words[2]
            if verb in ("take", "revive") and len(words) > 2
            else (m[1] if (m := TARGET.search(tail)) else None)
        )
        near = NEAR.search(tail)
        name = re.sub(rf"\b(?:on|from|to|near)\s+{TOKEN}|\b(?:at|near)\s+-?\d.*$|\bx\d+\b", " ", tail, flags=re.I)
        count, slot = re.search(r"\bx(\d+)\b", tail), re.search(r"\bslot\s+(\d)", tail)
        commands = []
        for actor in dict.fromkeys(words[1:end]):
            text = f"{'queue ' if queued else ''}{verb} {actor} {tail}".strip()
            try:
                uid = self.references.resolve(actor, batch.entities)
            except ValueError as problem:
                notes.append(f"{line!r}: {problem}")
                continue
            try:
                aim = self.references.resolve(target, batch.entities) if target else None
                beside = self.references.resolve(near[1], batch.entities) if near else None
            except ValueError as problem:
                notes.append(f"{text!r}: {problem}")
                continue
            commands.append(
                Command(
                    verb, uid, text, name=" ".join(name.split()), target=aim, point=_point(tail), near=beside,
                    count=int(count[1]) if count else 1, slot=int(slot[1]) if slot else None, queued=queued,
                )
            )  # fmt: skip
        return commands

    def _apply(self, command, batch, turn, notes):
        uid = command.actor
        if uid not in batch.own and uid not in self.world.known_units:
            notes.append(f"{command.text!r}: skipped; {self.world.unavailable.get(uid, 'not a known owned unit')}")
            return []
        if command.queued and self._gathering(uid, batch):
            notes.append(
                f"{command.text!r}: skipped; gathering never ends, so an order queued after it never runs. "
                "Give the order without queue, then queue gathering after it."
            )
            return []
        if command.verb in QUEUE_VERBS:
            self._last[uid] = command.verb
        if not command.queued and command.verb not in PRODUCTION:
            self.control.deferred.pop(uid, None)  # an ordinary unit order replaces waiting ones
        inside = next((u for u in batch.obs.get("inside", []) if u["unit_id"] == uid), None)
        if (
            inside
            and command.verb == "gold"
            and not command.queued
            and (inside["order"] or {}).get("name") in GATHERING
        ):
            notes.append(f"{command.text!r}: skipped; it is already inside the mine gathering gold")
            return []
        if uid not in batch.own or uid in self.control.deferred:
            self.control.defer(uid, command, turn)
            reason = (
                "appended to deferred orders"
                if uid in batch.own
                else "actor is inside a mine or building until it comes out"
                if inside
                else "actor is not observable"
            )
            notes.append(f"{command.text!r}: deferred; {reason}")
            return []
        made = self._emit(command, batch, notes)
        grouped = any(uid in g["ids"] for g in self.control.groups.values())
        if grouped and made and all(a["command"] in ("cast", "use_item") for a in made):
            # A spell or item use keeps the unit in its group; micro resumes once it has run.
            self.control.cast(uid, made[-1]["arguments"].get("order"), batch.obs["game_time_seconds"])
        elif any(a["command"] in QUEUEABLE for a in made):
            self.control.take(uid)
        elif made:
            self.control.touch([uid])
        return made

    def _gathering(self, uid, batch):
        """Whether the unit's orders so far end in gathering: its latest order in this reply, else
        its current (or last seen) job."""
        if uid in self._last:
            return self._last[uid] in ("gold", "lumber")
        unit = batch.own.get(uid) or self.world.known_units.get(uid) or {}
        pending = self.control.deferred.get(uid)
        if pending and pending.commands:
            return pending.commands[-1][0].verb in ("gold", "lumber")
        return (unit.get("order") or {}).get("name") in GATHERING

    def release(self, obs):
        """(actions, notes, turns): deferred commands whose actors are observable again."""
        batch = self._batch(obs)
        actions, notes, turns = [], [], []
        for uid in [uid for uid in self.control.deferred if uid in batch.own]:
            for command, turn in self.control.release(uid).commands:
                made = self._emit(command, batch, notes)
                actions.extend(made)
                turns.extend([turn] * len(made))
            notes.append(f"Deferred orders for {self.references.label(batch.own[uid])} resolved.")
        return actions, notes, turns

    def _emit(self, command, batch, notes):
        try:
            made = self._actions(command, batch)
        except ValueError as problem:
            notes.append(f"{command.text!r}: {problem}")
            return []
        accepted = []
        for action in made:
            aim = action["arguments"].get("target_id", action["arguments"].get("shop_id"))
            if aim is not None and aim not in batch.targets and action["command"] != "revive":
                notes.append(
                    f"{command.text!r}: target {self.references.name(aim)} is no longer observable; order skipped"
                )
                continue
            if command.queued:
                action["arguments"]["queued"] = True
            accepted.append(action)
        return accepted

    def _group(self, verb, line, batch, turn, notes):
        """`group name footman1 footman2 [attack] [at X Y]: instruction` or `disband name`; return
        initial moves (attack-moves after `attack`)."""
        head, _, instruction = line.partition(":")
        words = head.split()
        if len(words) < 2 or words[1].lower() in self.references.id_by_name or re.fullmatch(LEGACY, words[1], re.I):
            raise ValueError("a group needs a name: group footmen footman1 footman2: what to do")
        name = words[1].lower()
        if verb == "disband":
            if name not in self.control.groups:
                raise ValueError(f"there is no group {name!r}")
            self.control.disband(name)
            return []
        ids = []
        for token in re.findall(rf"(?<!\w){TOKEN}\b", " ".join(words[2:]), re.I):
            try:
                ids.append(self.references.resolve(token, batch.entities))
            except ValueError as problem:
                notes.append(f"group {name}: skipped {token}; {problem}")
        known = self.world.known_units | batch.own
        valid = [n for n in dict.fromkeys(ids) if n in known and not known[n]["structure"]]
        notes += [
            f"group {name}: skipped {self.references.name(n)}; {self.world.unavailable.get(n, 'not a known owned non-structure unit')}"
            for n in ids
            if n not in valid
        ]
        if not valid:
            raise ValueError("a group needs your own non-structure units")
        if not instruction.strip():
            raise ValueError("a group needs an instruction after a colon")
        where = _point(head)
        attack = where is not None and re.search(r"\battack\s+at\b", head, re.I) is not None
        already = self.control.delegate(name, set(valid), instruction.strip(), where, attack)
        # Only newcomers, or everyone after a change of destination: repeating the line must not
        # pull units that are already there (and perhaps fighting under Jev) off their targets.
        moves = []
        for uid in valid if where else ():
            if uid in already:
                continue
            verb = "attack" if attack else "move"
            move = Command(
                verb, uid, f"{verb} {self.references.name(known[uid])} at {where['x']} {where['y']}", point=where
            )
            if uid in batch.own:
                moves.extend(self._emit(move, batch, notes))
            else:
                self.control.defer_move(uid, move, turn, name)
                notes.append(
                    f"group {name}: movement for {self.references.label(known[uid])} deferred until observable"
                )
        return moves

    def _actions(self, command, batch):
        verb, uid, name, target, where = command.verb, command.actor, command.name, command.target, command.point
        obs, first = batch.obs, batch.own[uid]

        def one(order, arguments):
            return [{"unit_id": uid, "command": order, "arguments": dict(arguments)}]

        aim = {"target_id": target} if target else where or {}
        if verb in ("train", "upgrade"):
            definition = self.catalog.units[first["type_id"]]
            raw = self._named(name, definition["trains"] + definition["upgrades_to"], self.catalog.name)
            if raw is None:
                raise ValueError(f"{definition['name']} cannot make {name!r}")
            dead = next((h for h in self.world.fallen.values() if h["type_id"] == raw), None)
            if dead:  # the engine silently refuses a second hero of a type you own, dead or alive
                hero, altar = self.references.name(dead), self.references.name(first)
                raise ValueError(f"{hero} is dead and cannot be trained again; revive it: revive {altar} {hero}")
            return one("train", {"type_id": raw}) * command.count
        if verb == "research":
            raw = self._research(name, first)
            if raw is None:
                raise ValueError(f"{self.catalog.name(first['type_id'])} cannot research {name!r}")
            return one("research", {"type_id": raw})
        if verb == "cancel":
            if not first["structure"]:
                raise ValueError("cancel needs a building; use stop or a new order to interrupt a unit")
            return one("cast", {"order": "cancel"})
        if verb == "build":
            raw = self._named(name, self.catalog.units[first["type_id"]]["builds"], self.catalog.name)
            if raw is None:
                raise ValueError(f"{self.catalog.name(first['type_id'])} cannot build {name!r}")
            if target:
                site = objects(obs).get(target)
                if site is None:
                    raise ValueError("build target is no longer observable")
                return one("build", {"type_id": raw, "target_id": target, "x": site["x"], "y": site["y"]})
            beside = objects(obs).get(command.near) if command.near is not None else None
            if command.near is not None and beside is None:
                raise ValueError(f"{self.references.name(command.near)} is not observable to build near")
            anchor = where or beside or town_hall(obs, self.catalog, self.world.hall) or first
            return one("build", {"type_id": raw, "x": anchor["x"], "y": anchor["y"], "auto_place": True})
        if verb == "rally":
            if not first["structure"] or not self.catalog.units[first["type_id"]]["trains"]:
                raise ValueError("rally needs a unit-producing structure")
            if where is not None:
                aim = where
            elif _key(name) == "gold":
                race = self.catalog.units[first["type_id"]].get("race")
                aim = {
                    "target_id": self._nearest(
                        first, self._mines(first, obs, {_RACE_MINES.get(race, "ngol")}), "gold mine"
                    )["unit_id"]
                }
            elif _key(name) == "lumber":
                aim = {"target_id": self._nearest(first, self._trees(obs), "trees")["id"]}
            else:
                raise ValueError("rally needs gold, lumber or 'at x y'")
            return one("cast", {"order": "setrally", **aim})
        if verb == "gold":
            mine_types, _ = harvest_kinds(first)
            if not mine_types:
                raise ValueError("unit cannot harvest gold in its current form")
            return one(
                "harvest",
                {"target_id": self._nearest(first, self._mines(first, obs, mine_types), "gold mine")["unit_id"]},
            )
        if verb == "lumber":
            if not harvest_kinds(first)[1]:
                raise ValueError("unit cannot harvest lumber in its current form")
            return one("harvest", {"target_id": self._nearest(first, self._trees(obs), "trees")["id"]})
        if verb == "revive":
            if target not in self.world.fallen:
                raise ValueError("revive needs an altar and one of your dead heroes: revive altarofkings1 archmage1")
            return one("revive", {"target_id": target})
        if verb == "take":
            if target not in {i["item_id"] for i in obs["items"]} or not first["hero"]:
                raise ValueError("take needs a hero and a ground item: take archmage1 potionofhealing1")
            return one("smart", {"target_id": target})
        if verb == "stop":
            return one("stop", {})
        if verb == "move":
            if where is None:
                raise ValueError("move needs 'at x y'")
            return one("move", where)
        if verb == "attack":
            if not aim:
                raise ValueError("attack needs 'on grunt1' or 'at x y'")
            return one("attack", aim)
        if verb == "repair":
            if not target:
                raise ValueError("repair needs 'on farm1'")
            return one("smart", {"target_id": target})
        if verb == "learn":
            raw = self._ability(name, first, learn=True)
            if raw is None:
                raise ValueError(f"{self.catalog.name(first['type_id'])} has no skill {name!r}")
            return one("learn", {"ability_id": raw})
        if verb == "cast":
            worker_order = {"calltoarms": "militia", "backtowork": "militiaoff"}.get(_key(name))
            if worker_order and not first["structure"]:  # a Peasant's or Militia's side of Call to Arms
                return one("cast", {"order": worker_order})
            if _key(name) in OFF_ORDERS:  # uproot an Ancient, send Militia back to work
                return one("cast", {"order": OFF_ORDERS[_key(name)], **aim})
            raw = self._ability(name, first)
            orders = (
                [o for o in self.catalog.abilities[raw]["levels"]["1"]["orders"] if o["kind"] == "cast"] if raw else []
            )
            if not orders:
                raise ValueError(f"no castable ability {name!r}")
            if orders[0].get("target_form") == "none":
                aim = {}  # the game ignores a targeted order for a spell that takes no target
            return one("cast", {"order": orders[0]["name"], **aim})
        if verb == "buy":
            wanted = re.sub(r"^.*?\bitem\b", "", name, flags=re.I).strip() or name  # "Goblin Merchant Item Boots"
            selling = [
                (shop, raw)
                for shop in trading_shops(obs, self.catalog)
                if not target or shop["unit_id"] == target
                if (raw := self._named(wanted, self.catalog.units[shop["type_id"]]["sells_items"], self.catalog.name))
            ]
            if not selling:
                raise ValueError(f"no shop in view sells {wanted!r} (see SHOPS IN VIEW and the ITEMS list)")
            shop, raw = min(selling, key=lambda pair: hypot(pair[0]["x"] - first["x"], pair[0]["y"] - first["y"]))
            return one("buy", {"shop_id": shop["unit_id"], "item_type_id": raw})
        if verb == "use":
            if command.slot is None or not 1 <= command.slot <= 6:
                raise ValueError("use needs 'slot 1' to 'slot 6'")
            return one("use_item", {"slot": command.slot - 1, **aim})
        if verb in ("give", "sell", "drop"):
            if command.slot is None or not 1 <= command.slot <= 6:
                raise ValueError(f"{verb} needs 'slot 1' to 'slot 6'")
            if verb != "drop" and not target:
                raise ValueError(f"{verb} needs 'to' a unit or shop")
            return one("drop_item", {"slot": command.slot - 1, **aim})
        raise ValueError("unknown order")

    @staticmethod
    def _mines(unit, obs, kinds):
        return [
            u
            for u in obs["units"] + obs["visible_enemies"]
            if u["type_id"] in kinds
            and u["hp"] > 0
            and u.get("state") != "constructing"
            and (u["type_id"] == "ngol" or u["owner"] == unit["owner"])
        ]

    @staticmethod
    def _trees(obs):
        return [d for d in obs["destructables"] if d["resource"] == "lumber" and d["hp"] > 0]

    @staticmethod
    def _nearest(unit, places, kind):
        if not places:
            raise ValueError(f"no {kind} in view")
        return min(places, key=lambda p: hypot(p["x"] - unit["x"], p["y"] - unit["y"]))
