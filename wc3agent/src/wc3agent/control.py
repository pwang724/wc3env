"""Who commands each unit: macro directly, or micro through a named group.

Every change macro makes to how a unit is commanded bumps that unit's version: a direct order,
a deferred order, joining or leaving a group, or a new objective for its group. A micro answer
is sent only for units whose versions are still the ones it was asked under.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

CAST_SECONDS = 2.0  # micro leaves a unit alone at least this long after macro's cast or item use


@dataclass
class Deferred:
    commands: list = field(default_factory=list)  # (command, macro turn), sent once the unit is observable
    group: str | None = None  # set when this is the unit's move to join that group's destination


class Control:
    def __init__(self):
        self.groups = {}  # name -> {ids, instruction, at, attack}: units handed to the micro model
        self.deferred = {}  # unit id -> Deferred
        self.versions = Counter()  # unit id -> number of macro changes to how it is commanded
        self.casting = {}  # unit id -> (order, game time): macro's cast or item use that micro must not cut short
        self.committed = {}  # unit id -> (target id, game time until): Jev's attack, not asked about again yet

    def touch(self, ids):
        self.versions.update(ids)

    def cast(self, uid, order, now):
        """Macro gave a grouped unit a spell or item use; it stays in its group, and micro leaves it
        alone until the order has run (CAST_SECONDS, or longer while the unit is still on that order)."""
        self.casting[uid] = (order, now)
        self.touch([uid])

    def busy(self, units, now):
        """Units micro must not command yet: macro's cast or item use is still running, or Jev just sent the
        unit at a target and it is still attacking it."""
        for uid, (order, at) in list(self.casting.items()):
            unit = units.get(uid)
            running = order is not None and (unit or {}).get("order") and unit["order"]["name"] == order
            if unit is None or (now - at >= CAST_SECONDS and not running):
                del self.casting[uid]
        for uid, (target, until) in list(self.committed.items()):
            order = (units.get(uid) or {}).get("order") or {}
            if now >= until or order.get("target_id") != target:  # the target died or the unit was turned away
                del self.committed[uid]
        return set(self.casting) | set(self.committed)

    def commit(self, uid, target, until):
        """Jev sent the unit at `target`: let it hit its target until `until` rather than asking again."""
        self.committed[uid] = (target, until)

    def adopt(self, summoner, summoned):
        """A summoned unit joins its summoner's group, with the same objective."""
        for group in self.groups.values():
            if summoner in group["ids"]:
                group["ids"].add(summoned)
                self.touch([summoned])
                return

    def take(self, uid):
        """Macro commands the unit directly; it leaves any group."""
        for group in self.groups.values():
            group["ids"].discard(uid)
        self.casting.pop(uid, None)
        self.touch([uid])

    def defer(self, uid, command, turn):
        """Append a direct order to the unit's waiting orders."""
        pending = self.deferred.setdefault(uid, Deferred())
        pending.commands.append((command, turn))
        pending.group = None
        self.take(uid)

    def defer_move(self, uid, command, turn, group):
        """The unit's move to its new group's destination, kept until the unit is observable."""
        self.deferred[uid] = Deferred([(command, turn)], group)

    def release(self, uid):
        self.touch([uid])
        return self.deferred.pop(uid)

    def delegate(self, name, ids, instruction, at, attack=False):
        """Hand `ids` to micro under `name`; `attack`: they attack-move to `at` instead of walking.
        Return the members already heading to this destination the same way."""
        previous = self.groups.get(name)
        before = set(previous["ids"]) if previous else set()
        same_way = previous and (previous["at"], previous.get("attack", False)) == (at, attack)
        already = before if same_way else set()
        changed = not same_way or previous["instruction"] != instruction
        self.deferred = {
            uid: p
            for uid, p in self.deferred.items()
            if not (p.group == name or uid in ids) or (p.group == name and uid in already and uid in ids)
        }
        for group in self.groups.values():
            group["ids"] -= ids
        self.groups[name] = {"ids": set(ids), "instruction": instruction, "at": at, "attack": attack}
        self.touch(before | ids if changed else before ^ ids)
        return already

    def disband(self, name):
        group = self.groups.pop(name)
        self.deferred = {uid: p for uid, p in self.deferred.items() if p.group != name}
        self.touch(group["ids"])

    def lose(self, uid):
        """The unit died or changed owner. Return whether it had orders waiting."""
        for group in self.groups.values():
            group["ids"].discard(uid)
        self.prune()
        return self.deferred.pop(uid, None) is not None

    def prune(self):
        self.groups = {name: g for name, g in self.groups.items() if g["ids"]}
