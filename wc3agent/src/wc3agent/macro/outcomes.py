"""Observe whether submitted orders took effect; never issue or retry orders.

Production (build, train, research) is watched for every order. Macro's own buy, cast, item use
and gather orders are watched too; micro's are not, since micro sees its units every second.
"""

from collections import Counter
from copy import deepcopy
from math import hypot

from ..game.orders import GATHERING, QUEUEABLE
from ..game.policies import SHOP_REACH
from ..game.workers import MINE_CAPACITY

OBSERVATION_GRACE = 3.0
WAIT = "wait"  # keep watching: the order is visibly still on its way
PRODUCTION = ("build", "train", "research")
MACRO_ONLY = ("buy", "cast", "use_item", "harvest")
# Casts with no effect on the caster that the observation shows: rally points and cancels; Call to
# Arms and Back to Work change the Peasants, not the hall; rooting is not observed.
UNWATCHED_CASTS = ("setrally", "cancel", "townbellon", "townbelloff", "root", "unroot")
AT_RESOURCE = 250.0  # a gatherer this close to its order's target is working it
QUIET = ("gathering",)  # results kept out of macro's feedback: a working gather order is the usual case


class Outcomes:
    def __init__(self, catalog, references):
        """`references` are the game's shared entity names; MacroMemory keeps them current."""
        self.catalog = catalog
        self.references = references
        self.observation = {}
        self.pending = []
        self.events = []
        self.serial = 0

    def emit(self, order, status, now, evidence):
        row = {k: order[k] for k in ("id", "turn", "action_index", "action", "ordered_at", "label")}
        row.update(status=status, at_game_time=now, evidence=evidence)
        self.events.append(row)
        return row

    def drain(self):
        rows, self.events = self.events, []
        return rows

    def submitted(self, actions, now, turns=None, rejected=()):
        obs = self.observation
        units = {u["unit_id"]: u for u in obs.get("units", [])}
        refused = {r["index"]: r["reason"] for r in rejected}
        expected = self._already_expected(units)
        rows = []
        for index, action in enumerate(actions):
            command, args, uid = action["command"], action["arguments"], action["unit_id"]
            turn = turns[index] if turns else None
            if index not in refused and command in QUEUEABLE and not args.get("queued"):
                rows += self._supersede_builds(uid, command, now)
            watched = command in PRODUCTION or (
                command in MACRO_ONLY and turn is not None and args.get("order") not in UNWATCHED_CASTS
            )
            if not watched and index not in refused:
                continue
            producer = units.get(uid, {})
            raw = args.get("type_id", "")
            upgrade = command == "train" and raw in self.catalog.units.get(producer.get("type_id"), {}).get(
                "upgrades_to", []
            )
            self.serial += 1
            order = dict(
                id=self.serial,
                turn=turn,
                action_index=index,
                action=deepcopy(action),
                ordered_at=now,
                label=self._label(command, args, raw, upgrade, producer or {"unit_id": uid}),
                before=deepcopy(producer),
                player=dict(obs.get("player", {})),
                item=next(
                    (i for i in obs.get("inventory", []) if i["unit_id"] == uid and i["slot"] == args.get("slot")),
                    None,
                ),
                existing=set(units),
                upgrade=upgrade,
                finished=0,
                events_lost=False,
            )
            if index in refused:
                rows.append(self.emit(order, "rejected", now, f"Environment rejected the command: {refused[index]}"))
                continue
            if command in PRODUCTION:
                expected[uid, raw] += 1
                order["expected"] = expected[uid, raw]
            self.pending.append(order)
            self.emit(
                order, "submitted", now, "Command accepted by the interface; game execution is not yet confirmed."
            )
        return rows

    def _label(self, command, args, raw, upgrade, actor):
        who = self.references.label(actor)
        if command == "buy":
            return f"buy {self.catalog.name(args['item_type_id'])} with {who}"
        if command == "cast":
            return f"cast {args['order']} with {who}"
        if command == "use_item":
            return f"use slot {args['slot'] + 1} with {who}"
        if command == "harvest":
            return f"gather with {who}"
        verb = "upgrade" if upgrade else command
        return f"{verb} {self.catalog.name(raw) if raw else args.get('order', '')} at {who}"

    def _already_expected(self, units):
        """(producer, type) -> queue entries plus completions already accounted for: what is queued
        now, or what earlier pending orders still expect, whichever is more."""
        expected = Counter(
            {(uid, raw): u.get("queue", []).count(raw) for uid, u in units.items() for raw in u.get("queue", [])}
        )
        for pending in self.pending:
            a = pending["action"]
            if a["command"] not in PRODUCTION:
                continue
            key = (a["unit_id"], a["arguments"]["type_id"])
            expected[key] = max(expected[key], pending["expected"] - pending["finished"])
        return expected

    def _supersede_builds(self, uid, command, now):
        """An unqueued unit order replaces the unit's pending builds, casts, item uses and gathering."""
        replaced = [
            p
            for p in self.pending
            if p["action"]["unit_id"] == uid and p["action"]["command"] in ("build", "cast", "use_item", "harvest")
        ]
        self.pending = [p for p in self.pending if p not in replaced]
        reason = f"A later {command} command replaced this unit's orders before the result was confirmed."
        return [self.emit(p, "superseded", now, reason) for p in replaced]

    def update(self, obs):
        self.observation = obs
        now, units = obs["game_time_seconds"], {u["unit_id"]: u for u in obs["units"]}
        rows, waiting, claimed = [], [], set()
        for order in self.pending:
            order["events_lost"] |= bool(obs.get("events_lost"))
            producer = units.get(order["action"]["unit_id"])
            found = self._evidence(order, producer, units, obs["events"], claimed)
            age = now - order["ordered_at"]
            if found is WAIT or (found is None and age < OBSERVATION_GRACE):
                waiting.append(order)
                continue
            status, evidence = found or ("not_observed", self._missing(order, producer, age))
            rows.append(self.emit(order, status, now, evidence))
        for order in waiting:
            order["existing"].update(claimed)
        self.pending = waiting
        return rows

    def _placed(self, structure, args):
        where = f"New {self.references.label(structure)} started at ({structure['x']:.0f}, {structure['y']:.0f})"
        if "near" not in args:
            return where + "."
        away = hypot(structure["x"] - args["near"][0], structure["y"] - args["near"][1])
        return f"{where}, {away:.0f} from the requested anchor; Warcraft chose the site."

    def _evidence(self, order, producer, units, events, claimed):
        """(status, evidence) once the game shows the order took effect; WAIT while a worker is
        visibly still on its way; None when nothing is observed yet."""
        action = order["action"]
        uid, args, command = action["unit_id"], action["arguments"], action["command"]
        job = (producer or {}).get("order")
        if command in MACRO_ONLY:
            found = self._unit_order_evidence(order, producer, events)
            # A Shift-queued order waits behind the unit's current job, like a queued build.
            return WAIT if found is None and args.get("queued") and job else found
        raw = args["type_id"]
        if command == "build":
            # A structure another worker is visibly building is that worker's; Undead, Orc and
            # Night Elf builders are not seen at the site, so the nearest new structure counts.
            builders = {
                (w.get("order") or {}).get("target_id"): n
                for n, w in units.items()
                if (w.get("order") or {}).get("name") in ("repair", "smart")
            }
            structure = min(
                (
                    u
                    for n, u in units.items()
                    if n not in order["existing"]
                    and n not in claimed
                    and u["structure"]
                    and u["type_id"] == raw
                    and builders.get(n, uid) == uid
                    and hypot(u["x"] - args["x"], u["y"] - args["y"]) < 200
                ),
                key=lambda u: hypot(u["x"] - args["x"], u["y"] - args["y"]),
                default=None,
            )
            if structure:
                claimed.add(structure["unit_id"])
                return "started", self._placed(structure, args)
            # Travelling to the site, or Shift-queued behind another job. Gathering never ends, so a
            # queued build that is not done once the worker gathers again was dropped.
            if job and (job["name"] == raw or (args.get("queued") and job["name"] not in GATHERING)):
                return WAIT
            return None
        if order["upgrade"]:
            upgrading = producer and (producer.get("state") == "upgrading" or producer["type_id"] == raw)
            # An already-running upgrade is not evidence for a duplicate order.
            if upgrading and order["before"].get("state") != "upgrading" and order["expected"] == 1:
                return "started", "Producer changed to upgrading state or the requested building type."
            return None
        finish = "research_finish" if command == "research" else "train_finish"
        order["finished"] += sum(
            e["kind"] == finish and e.get("unit_id") == uid and e.get("type_id") == raw for e in events
        )
        queue = producer.get("queue", []) if producer else []
        if queue.count(raw) + order["finished"] >= order["expected"]:
            return "queued", f"Production queue/completion events confirm an additional {self.catalog.name(raw)}."
        return None

    def _unit_order_evidence(self, order, unit, events):
        """Buy: the sale event. Cast: the spell's effect event, or the unit changing form (Uproot,
        Call to Arms). Item use: the use event, or the slot's item gone or down a charge. Gather:
        the worker inside the mine, carrying resources back, or working at its target."""
        uid, args, command = order["action"]["unit_id"], order["action"]["arguments"], order["action"]["command"]
        mine = [e for e in events if e.get("unit_id") == uid or e.get("buyer_id") == uid]
        job = (unit or {}).get("order") or {}
        if command == "buy":
            if any(e["kind"] == "item_sold" and e["type_id"] == args["item_type_id"] for e in mine):
                return "bought", "The shop sold it."
            return None
        if command == "cast":
            if any(e["kind"] == "spell_effect" for e in mine):
                return "cast", "The spell took effect."
            if unit and unit["type_id"] != order["before"].get("type_id"):
                return "cast", f"The unit changed form to {self.catalog.name(unit['type_id'])}."
            return WAIT if job.get("name") == args["order"] else None  # walking into range or channeling
        if command == "use_item":
            item = order["item"]
            if item is None:
                return None
            if any(e["kind"] == "item_use" and e["type_id"] == item["type_id"] for e in mine):
                return "used", f"{self.catalog.name(item['type_id'])} was used."
            now = next(
                (i for i in self.observation.get("inventory", []) if i["unit_id"] == uid and i["slot"] == item["slot"]),
                None,
            )
            if unit and (now is None or now["type_id"] != item["type_id"] or now["charges"] < item["charges"]):
                return "used", f"{self.catalog.name(item['type_id'])} was used up or lost a charge."
            return None
        if unit is None:  # gathering
            if any(e["kind"] == "death" for e in mine):
                return None
            return "gathering", "The worker went into the mine."
        if job.get("name") == "returnresources":
            return "gathering", "The worker is carrying resources back."
        if job.get("name") in GATHERING:
            if "x" in job and hypot(unit["x"] - job["x"], unit["y"] - job["y"]) <= AT_RESOURCE:
                return "gathering", "The worker is working its resource."
            return WAIT  # still walking there
        return None

    def _missing(self, order, producer, age):
        """Evidence text for an order the game never showed taking effect."""
        args, command = order["action"]["arguments"], order["action"]["command"]
        if command in MACRO_ONLY:
            return self._missing_unit_order(order, producer, age)
        if command == "build":
            evidence = f"No new structure at ({args['x']:g}, {args['y']:g}) after {age:.1f}s."
            if args.get("queued"):
                evidence += (
                    " This was Shift-queued; the worker's waiting orders are not visible, so it may still be pending."
                )
            else:
                job = (producer.get("order") or {}).get("name", "idle") if producer else "not visible"
                evidence += f" Worker now: {job}."
        else:
            evidence = (
                f"No new upgrade state or type change observed after {age:.1f}s."
                if order["upgrade"]
                else f"No additional queue entry or completion observed after {age:.1f}s."
            )
            if order["before"].get("state"):
                evidence += f" Producer was {order['before']['state']} when ordered."
            if producer:
                evidence += f" Current state: {producer.get('state') or 'ready'}; queue: {producer.get('queue', [])}."
            else:
                evidence += " Producer is no longer visible."
        evidence += self._shortfall(order)
        if order["events_lost"]:
            evidence += " Some game events were lost."
        return evidence + " The engine gives no failure reason; check current state before retrying."

    def _shortfall(self, order):
        """What the order needed against what the player held when it was sent."""
        player, args, command = order["player"], order["action"]["arguments"], order["action"]["command"]
        raw = args.get("type_id")
        if not player or not raw:
            return ""
        if command == "research":
            level = (self.catalog.upgrades.get(raw, {}).get("levels") or [{}])[0]
            cost = dict(gold=level.get("gold", 0), lumber=level.get("lumber", 0), food=0)
        else:
            unit = self.catalog.units.get(raw, {})
            cost = dict(gold=unit.get("gold", 0), lumber=unit.get("lumber", 0), food=unit.get("food", 0))
        short = []
        if cost["gold"] > player.get("gold", 0):
            short.append(f"{cost['gold']} gold needed, {player['gold']} held")
        if cost["lumber"] > player.get("lumber", 0):
            short.append(f"{cost['lumber']} lumber needed, {player['lumber']} held")
        if cost["food"] and player.get("food_used", 0) + cost["food"] > player.get("food_cap", 0):
            short.append(f"{cost['food']} food needed, {player['food_used']}/{player['food_cap']} used")
        return f" When ordered: {'; '.join(short)}." if short else ""

    def _seen(self, uid):
        obs = self.observation
        return next((u for u in obs.get("units", []) + obs.get("visible_enemies", []) if u["unit_id"] == uid), None)

    def _missing_unit_order(self, order, unit, age):
        args, command = order["action"]["arguments"], order["action"]["command"]
        job = ((unit or {}).get("order") or {}).get("name", "idle") if unit else "not visible"
        if command == "buy":
            hero = order["before"]
            shop = self._seen(args["shop_id"])
            away = hypot(shop["x"] - hero["x"], shop["y"] - hero["y"]) if shop and hero else 0
            if away > SHOP_REACH:
                return (
                    f"No sale after {age:.1f}s. The hero was {away:.0f} from the shop when ordered; "
                    f"it must stand next to it (within about {SHOP_REACH:.0f})."
                )
            return f"No sale after {age:.1f}s. Gold when ordered: {order['player'].get('gold', '?')}; stock is not observed."
        if command == "cast":
            mana = f" Mana {unit['mana']:.0f}/{unit['max_mana']:.0f}." if unit and unit.get("max_mana") else ""
            return f"No spell effect after {age:.1f}s. Unit now: {job}.{mana} The engine gives no failure reason."
        if command == "use_item":
            if order["item"] is None:
                return f"Slot {args['slot'] + 1} was empty when ordered."
            return f"No use of {self.catalog.name(order['item']['type_id'])} after {age:.1f}s. Unit now: {job}."
        mine = self._seen(args.get("target_id"))
        full = (
            f" A {self.catalog.name(mine['type_id'])} takes at most {MINE_CAPACITY[mine['type_id']]} workers."
            if job == "idle" and mine and mine["type_id"] in MINE_CAPACITY
            else ""
        )
        return f"The worker did not start gathering. Worker now: {job}.{full}"

    def finish(self, now):
        for order in self.pending:
            self.emit(
                order,
                "unconfirmed",
                now,
                "Run ended before execution could be confirmed; this is not a confirmed failure.",
            )
        self.pending = []

    @staticmethod
    def feedback(row):
        origin = f"turn {row['turn']}, " if row["turn"] is not None else ""
        return (
            f"Order {row['id']} ({origin}{row['ordered_at']:.1f}s): {row['label']} — {row['status']}: {row['evidence']}"
        )
