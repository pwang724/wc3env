"""Jev under a macro model: groups named from outside, each with its own instruction.

Active groups have one outstanding Jev call per unit type, each choosing its units' next actions.
Only explicit macro groups are controlled. Idle army groups may navigate, scout,
recover or collect loot; worker jobs remain macro decisions. An answer is sent only for units
whose Control version has not changed since the question was asked.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from math import hypot
from threading import Event
from time import monotonic

from ..game.abilities import describe_abilities
from ..game.maneuvers import pickups
from ..game.policies import (
    CREEP_ESCAPE_RETRY_SECONDS,
    DISABLED_ORDER,
    LOOT_RETRY_SECONDS,
    REGROUP_RETRY_SECONDS,
    creep_escapes,
    holds,
    hostiles,
    loot_targets,
    quiet,
    repeated_cast,
    stragglers,
)
from ..game.workers import is_worker
from ..game.world import targets
from ..models.jev import timed_call
from .memory import MicroMemory
from .names import Names
from .request import Menu, build_request, map_response

FOLLOWUP_SECONDS = 4.0  # how long a unit changing form may take before its chosen spell is given up
ENGAGE_RADIUS = 1100.0  # nearby allies included as context around explicitly controlled members
MAX_CALLS = 8
CALL_INTERVAL = 1.0
BASE_EVENTS = ("construct", "upgrade", "train", "research")  # production is not the micro model's business


@dataclass
class Pending:
    future: Future
    menu: Menu
    record: dict
    versions: dict  # unit id -> Control version when asked


class MicroAgent:
    def __init__(self, catalog, *, model, key, ready=None, call_limit=None, references=None, loot_every_hero=False):
        """`loot_every_hero`: collect loot with every hero, not only grouped ones (another AI moves the rest)."""
        self.catalog, self.model, self.key = catalog, model, key
        self.loot_every_hero = loot_every_hero
        self.memory, self.names = MicroMemory(catalog), Names(catalog, references)
        self.executor = ThreadPoolExecutor(max_workers=MAX_CALLS, thread_name_prefix="micro")
        self.calls = 0
        self.call_limit = call_limit
        self.calls_by_group = Counter()
        self.skipped_quiet = 0  # calls not made because nothing could change (policies.quiet)
        self.last_request = {}  # (group, unit type) -> (game time, wall clock)
        self.pending = {}
        self.followups = {}  # unit id -> (cast to send, the form it waits for, game time to give up)
        self.loot_sent = {}  # hero id -> (item id, game time the pickup order was last sent)
        self.regroup_sent = {}  # unit id -> game time its regroup order was last sent
        self.escape_sent = {}  # unit id -> game time its step away from creeps was last sent
        self.ready = ready if ready is not None else Event()

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def collect(self, obs, versions, *, wait=False, finished=False, looting=()):
        """Take completed answers independently; all game state stays on the calling thread."""
        actions, records, landed = [], [], set()
        own, live = {u["unit_id"] for u in obs["units"] if u["hp"] > 0}, targets(obs)
        for key, pending in list(self.pending.items()):
            if not wait and not pending.future.done():
                continue
            del self.pending[key]
            landed.add(key)
            record = pending.record
            records.append(record)
            record["landed_at_game_time"] = obs["game_time_seconds"]
            try:
                pending.future.result()
                answers, names = record["response"]["answers"], pending.menu.question_names
                choices, selected = map_response(answers, pending.menu)
                record.update(choices=choices, proposed_actions=[option["action"] for option in selected])
                record["picked"] = {names[uid]: answers[names[uid]]["choice"] for uid in pending.menu.options}
            except Exception as problem:  # noqa: BLE001  a failed call costs this round, not the game
                record["error"] = str(problem).replace(self.key, "[REDACTED]") if self.key else str(problem)
                continue
            finally:
                response = record.get("response")
                record["usage"] = response.get("usage", {}) if isinstance(response, dict) else {}
            accepted, dropped, casting = [], [], set()
            for option in selected:
                action = option["action"]
                uid = action["unit_id"]
                target = action["arguments"].get("target_id", action["arguments"].get("shop_id"))
                effect = repeated_cast(option, self.catalog)
                reason = None
                if effect and effect in casting:
                    # Units asked together all see the effect missing: two Druids both Roaring at once.
                    reason = "another unit in this answer already casts it"
                elif finished:
                    reason = "run ended"
                elif uid not in own:
                    reason = "unit no longer available"
                elif versions[uid] != pending.versions[uid]:
                    reason = "superseded by new macro orders"
                elif uid in looting:
                    reason = "code is moving it (escape from creeps, loot pickup or regroup)"
                elif target and target not in live:
                    reason = "target no longer available"
                if not reason and action["command"] == "use_item" and option.get("item_type"):
                    current = next(
                        (
                            item
                            for item in obs["inventory"]
                            if item["unit_id"] == uid and item["slot"] == action["arguments"]["slot"]
                        ),
                        None,
                    )
                    if current is None or current["type_id"] != option["item_type"]:
                        reason = "inventory slot changed while deciding"
                if reason:
                    dropped.append({"action": action, "reason": reason, "unit": pending.menu.question_names[uid]})
                else:
                    accepted.append(action)
                    if effect:
                        casting.add(effect)
                    if option.get("then"):  # the spell of the form the unit is changing back to
                        self.followups[uid] = (
                            {"unit_id": uid, **option["then"]},
                            option["then_type"],
                            obs["game_time_seconds"] + FOLLOWUP_SECONDS,
                        )
            record.update(actions=accepted, dropped_actions=dropped)
            actions.extend(accepted)
        return actions, records, landed

    def hold(self, obs, actions, control):
        """Units Jev just sent at a target are not asked again for a moment (policies.holds)."""
        heroes = {u["unit_id"] for u in obs["units"] if u["hero"]}
        for uid, (target, until) in holds(actions, heroes, obs["game_time_seconds"]).items():
            control.commit(uid, target, until)

    def finish(self, obs):
        """Log outstanding calls at the end of a run without issuing their orders."""
        return self.collect(obs, {}, wait=True, finished=True)[1]

    def view(self, obs, groups, hall=None):
        """Keep explicit members and nearby allied fighters as context, without assigning them. Workers
        and buildings outside the groups take no part in the fight and are left out.

        `hall` is our town hall's unit id, the target of a Town Portal.
        """
        members = {uid for group in groups.values() for uid in group["ids"]}
        anchors = [u for u in obs["units"] if u["unit_id"] in members and u["hp"] > 0]
        base = next((u for u in obs["units"] if u["unit_id"] == hall), {})
        return {
            **obs,
            "units": [
                u
                for u in obs["units"]
                if u["hp"] > 0
                and (
                    u["unit_id"] in members
                    or (
                        not u["structure"]
                        and not is_worker(u, self.catalog)
                        and any(hypot(u["x"] - a["x"], u["y"] - a["y"]) < ENGAGE_RADIUS for a in anchors)
                    )
                )
            ],
            "events": [e for e in obs["events"] if not e["kind"].startswith(BASE_EVENTS)],
            "home": {"unit_id": hall, **{k: base[k] for k in ("x", "y") if k in base}},
        }

    def loot(self, obs, view, groups, busy):
        """Pickup orders for grouped heroes (every hero with `loot_every_hero`) with loot in reach
        (policies.loot_targets), and the heroes collecting. An order is repeated after LOOT_RETRY_SECONDS."""
        grouped = set().union(*(group["ids"] for group in groups.values()))
        heroes = [u for u in obs["units"] if u["hero"] and u["hp"] > 0 and u["unit_id"] not in busy
                  and (self.loot_every_hero or u["unit_id"] in grouped)]  # fmt: skip
        looting = loot_targets(heroes, view, {u["unit_id"]: pickups(u, view, self.catalog) for u in heroes})
        now, actions = obs["game_time_seconds"], []
        for uid, item in looting.items():
            sent_item, sent_at = self.loot_sent.get(uid, (None, -float("inf")))
            if sent_item != item or now - sent_at >= LOOT_RETRY_SECONDS:
                actions.append({"unit_id": uid, "command": "smart", "arguments": {"target_id": item}})
                self.loot_sent[uid] = (item, now)
        for uid in set(self.loot_sent) - set(looting):
            del self.loot_sent[uid]
        return actions, looting

    def escape(self, obs, groups, busy):
        """Move orders taking badly hurt units out of the creeps' reach (policies.creep_escapes), and the
        units escaping. An order is renewed after CREEP_ESCAPE_RETRY_SECONDS."""
        grouped = set().union(*(group["ids"] for group in groups.values())) - busy
        members = [u for u in obs["units"] if u["unit_id"] in grouped]
        neutral = {p["id"] for p in obs["players"] if p.get("kind") == "neutral"}
        creeps = [e for e in hostiles(obs) if e["owner"] in neutral]
        enemies = [e for e in hostiles(obs) if e["owner"] not in neutral]
        out = creep_escapes(members, creeps, enemies, self.memory.last_damage, obs["game_time_seconds"])
        now, actions = obs["game_time_seconds"], []
        for uid, (x, y) in out.items():
            if now - self.escape_sent.get(uid, -float("inf")) >= CREEP_ESCAPE_RETRY_SECONDS:
                actions.append({"unit_id": uid, "command": "move", "arguments": {"x": x, "y": y}})
                self.escape_sent[uid] = now
        for uid in set(self.escape_sent) - set(out):
            del self.escape_sent[uid]
        return actions, out

    def regroup(self, obs, groups, busy):
        """Attack-move orders sending stragglers back to their group (policies.stragglers), and the units
        regrouping. An order is repeated after REGROUP_RETRY_SECONDS."""
        members = [
            ([u for u in obs["units"] if u["unit_id"] in group["ids"] - busy], group.get("at"))
            for group in groups.values()
        ]
        back = stragglers(members, hostiles(obs))
        now, actions = obs["game_time_seconds"], []
        for uid, (x, y) in back.items():
            if now - self.regroup_sent.get(uid, -float("inf")) >= REGROUP_RETRY_SECONDS:
                actions.append({"unit_id": uid, "command": "attack", "arguments": {"x": x, "y": y}})
                self.regroup_sent[uid] = now
        for uid in set(self.regroup_sent) - set(back):
            del self.regroup_sent[uid]
        return actions, back

    def act(self, obs, control, hall, tech, *, wait=True, start=True):
        """This step's actions for Control's groups, and one log record per Jev call.

        Realtime callers poll with wait=False; stepped play waits while the game is paused.
        `start=False` collects answers without asking new questions from this observation.
        """
        # Only macro can assign units. Direct macro orders remove membership,
        # so retreating/scouting units cannot fall back into an unassigned group.
        army_groups = {}
        for name, group in control.groups.items():
            members = [
                u
                for u in obs["units"]
                if u["unit_id"] in group["ids"]
                and u["hp"] > 0
                and not u["structure"]
                and self.catalog.units.get(u["type_id"], {}).get("base_move_speed", 1)
                > 0  # wards cannot be commanded usefully
            ]
            if members:
                army_groups[name] = {
                    **group,
                    "ids": {u["unit_id"] for u in members},
                    "center": {axis: sum(u[axis] for u in members) / len(members) for axis in ("x", "y")},
                }
        groups = army_groups
        view = self.view(obs, groups, hall)
        view["army_groups"] = army_groups
        view["shops"] = [
            u
            for u in obs["units"] + obs["visible_enemies"]
            if u["hp"] > 0
            and self.catalog.units.get(u["type_id"], {}).get("sells_items")
            and u.get("state") != "constructing"
            and (
                u["owner"] == obs.get("observer", 0)
                or any(p["id"] == u["owner"] and p.get("relation") in ("ally", "neutral") for p in obs["players"])
            )
        ]
        view["tech"] = dict(tech)
        for u in obs["units"]:
            if u["hp"] > 0 and u.get("state") != "constructing":
                view["tech"][u["type_id"]] = view["tech"].get(u["type_id"], 0) + 1
        self.memory.ingest(obs)
        followups = []
        for uid, (then, form, until) in list(self.followups.items()):
            unit = next((u for u in obs["units"] if u["unit_id"] == uid and u["hp"] > 0), None)
            if unit is None or obs["game_time_seconds"] > until:
                del self.followups[uid]
            elif unit["type_id"] == form:  # changed back: cast what Jev chose
                followups.append(then)
                del self.followups[uid]
        busy = control.busy({u["unit_id"]: u for u in obs["units"]}, obs["game_time_seconds"])
        busy |= set(self.followups) | {a["unit_id"] for a in followups}
        # A stunned, cycloned or sleeping unit cannot act: asking about it only wastes the call.
        busy |= {u["unit_id"] for u in obs["units"] if str((u["order"] or {}).get("name")) == DISABLED_ORDER}
        escape_actions, escaping = self.escape(obs, groups, busy)
        busy |= set(escaping)
        loot_actions, looting = self.loot(obs, view, groups, busy)
        busy |= set(looting)
        regroup_actions, regrouping = self.regroup(obs, groups, busy)
        busy |= set(regrouping)
        active = {name: group["ids"] - busy for name, group in groups.items() if group["ids"] - busy}
        moved = set(looting) | set(escaping) | set(regrouping)
        actions, records, landed = self.collect(obs, control.versions, wait=wait, looting=moved)
        self.hold(obs, actions, control)
        actions = followups + escape_actions + loot_actions + regroup_actions + actions
        if not start or not active or not self.key:
            return actions, records
        caps = describe_abilities(self.catalog, view, tech)
        self.memory.observe_orders(caps)
        by_id = {u["unit_id"]: u for u in view["units"]}
        self.memory.remember_forms(caps, by_id)
        now = obs["game_time_seconds"]
        for name, ids in active.items():
            for type_id in sorted({by_id[uid]["type_id"] for uid in ids}):
                key = (name, type_id)
                if len(self.pending) >= MAX_CALLS:
                    break
                if key in self.pending or key in landed:
                    continue
                if self.call_limit is not None and self.calls_by_group[key] >= self.call_limit:
                    continue
                last_game, last_wall = self.last_request.get(key, (-float("inf"), -float("inf")))
                if now - last_game < CALL_INTERVAL or (not wait and monotonic() - last_wall < CALL_INTERVAL):
                    continue
                controlled = {uid for uid in ids if by_id[uid]["type_id"] == type_id}
                members = [by_id[uid] for uid in controlled]
                loot = {u["unit_id"]: pickups(u, view, self.catalog) for u in members}
                if quiet(members, view, loot, self.catalog, groups[name].get("at")):
                    self.skipped_quiet += 1
                    continue
                request, menu = build_request(
                    view, controlled, groups[name]["instruction"],
                    memory=self.memory, names=self.names, capabilities=caps, catalog=self.catalog, model=self.model,
                )  # fmt: skip
                record = {
                    "kind": "micro",
                    "model": self.model,
                    "call_index": self.calls,
                    "at_game_time": obs["game_time_seconds"],
                    "group": name,
                    "control_group": f"{name} / {self.catalog.name(type_id)}",
                    "controlled_units": list(menu.question_names.values()),
                    "type_id": type_id,
                }
                self.calls += 1
                future = self.executor.submit(timed_call, request, self.key, record)
                self.calls_by_group[key] += 1
                self.last_request[key] = (now, monotonic())
                self.pending[key] = Pending(future, menu, record, {uid: control.versions[uid] for uid in controlled})
                future.add_done_callback(lambda _: self.ready.set())
        if wait:
            completed, more_records, _ = self.collect(obs, control.versions, wait=True, looting=looting)
            self.hold(obs, completed, control)
            actions.extend(completed)
            records.extend(more_records)
        return actions, records
