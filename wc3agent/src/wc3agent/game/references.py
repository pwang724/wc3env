"""Stable model-facing entity names for one game; native IDs stay at the action boundary."""

import re
from collections import defaultdict

TOKEN = r"(?:[a-z][a-z0-9]*\d+|#\d+)"
LEGACY = r"(?:[ubid]\d+|#\d+)"


def entity_id(entity):
    return entity.get("item_id", entity.get("unit_id", entity.get("id")))


def objects(obs, known=None):
    result = dict(known or {})
    for key in ("units", "visible_enemies", "items", "destructables"):
        result.update((entity_id(e), e) for e in obs.get(key, []))
    return result


class References:
    def __init__(self, catalog):
        self.catalog = catalog
        self.name_by_id, self.id_by_name, self.entities = {}, {}, {}
        self.dead = set()  # ids whose unit died: Warcraft gives a dead unit's id to a new one
        self.counts = defaultdict(int)

    def _type_name(self, entity):
        raw = entity.get("type_id", "")
        if "item_id" in entity:
            return self.catalog.items.get(raw, {}).get("name", "Item")
        if "unit_id" in entity:
            return self.catalog.units.get(raw, {}).get("name", "Unit")
        return "Tree" if entity.get("resource") == "lumber" else "Destructible"

    def name(self, entity):
        if isinstance(entity, int):
            entity = self.entities.get(entity, {"unit_id": entity})
        uid = entity_id(entity)
        if not uid:
            return "unobserved"  # the game uses zero for an unseen secondary reference
        if uid not in self.name_by_id:
            base = re.sub(r"[^a-z]", "", self._type_name(entity).lower()) or "entity"
            self.counts[base] += 1
            name = f"{base}{self.counts[base]}"
            self.name_by_id[uid] = name
            self.id_by_name[name] = uid
        return self.name_by_id[uid]

    def label(self, entity):
        name = self.name(entity)
        current = self._type_name(entity)
        base = re.sub(r"[^a-z]", "", current.lower())
        return name if name.rstrip("0123456789") == base else f"{name} ({current})"

    def remember(self, obs):
        entities = objects(obs)
        # A dead unit's id reused by a new unit of another type gets a new name (a Spirit Lodge was called
        # spiritwolf7 after a Feral Spirit wolf's id). Revived heroes keep theirs: same id, same type.
        for uid, entity in entities.items():
            old = self.entities.get(uid)
            if uid in self.dead and entity.get("hp", 0) > 0:
                self.dead.discard(uid)
                if old and old.get("type_id") != entity.get("type_id"):
                    self.name_by_id.pop(uid, None)
        self.dead.update(e["unit_id"] for e in obs.get("events", []) if e["kind"] == "death" and e.get("unit_id"))
        # Events may reveal an entity that disappeared before this observation.
        for event in obs.get("events", []):
            kind, raw = event["kind"], event.get("type_id")
            uid = (
                event.get("trained_id")
                if kind == "train_finish"
                else event.get("summoned_id")
                if kind == "summon"
                else None
            )
            if kind in ("death", "construct_start", "construct_finish", "upgrade_start", "upgrade_finish"):
                uid = event.get("unit_id")
            if uid and raw and uid not in entities:
                entities[uid] = {
                    "unit_id": uid,
                    "type_id": raw,
                    "structure": self.catalog.units.get(raw, {}).get("structure", False),
                }
            if event.get("item_id") and raw and event["item_id"] not in entities:
                entities[event["item_id"]] = {"item_id": event["item_id"], "type_id": raw}
        for uid, entity in sorted(entities.items()):
            self.entities[uid] = entity
            self.name(entity)

    def resolve(self, token, entities):
        token = token.lower()
        if token in self.id_by_name:
            return self.id_by_name[token]
        # Old recorded commands remain readable; prompts only teach names.
        if re.fullmatch(LEGACY, token):
            uid = int(token[1:])
            if token[0] != "#" and uid in entities:
                entity = entities[uid]
                prefix = (
                    "i"
                    if "item_id" in entity
                    else "b"
                    if entity.get("structure")
                    else "u"
                    if "unit_id" in entity
                    else "d"
                )
                if token[0] != prefix:
                    raise ValueError(f"{token} has the wrong prefix; use {self.name(entity)}")
            return uid
        raise ValueError(f"unknown entity name {token!r}; copy a name from the observation")
