"""Micro type and skill labels; entity names come from the References macro also uses."""

from ..game.references import References

COPY_MARKS = {"AC": "creep", "AI": "item"}  # ability id prefixes of the creep and item copies of spells


class Names:
    """Stable labels for a run, including departed units still mentioned in history."""

    def __init__(self, catalog, references=None):
        self.catalog = catalog
        self.references = references if references is not None else References(catalog)
        self.groups, self.ability_data, self.skill_names = {}, {}, {}
        self.unit_identity = {}

    def remember(self, obs, capabilities):
        self.references.remember(obs)
        for cap in capabilities.values():
            for ability in cap["abilities"]:
                self.ability_data[ability["ability_id"]] = ability
        for unit in sorted(obs["units"] + obs["visible_enemies"], key=lambda u: u["unit_id"]):
            raw = unit["type_id"]
            self.unit_identity[unit["unit_id"]] = (raw, unit["owner"] == obs["observer"], unit["structure"])
            if raw not in self.groups:
                definition = self.catalog.unit(raw)
                base = definition["name"] if definition["name"] != raw else "Unidentified unit type"
                if definition.get("form"):
                    base = f"{base} {definition['form']}"  # Druid of the Claw (Bear Form)
                label, variant = base, 1
                while label in self.groups.values():
                    variant += 1
                    label = f"{base} variant {variant}"
                self.groups[raw] = label

    def name(self, entity_id):
        """A unit's, item's or tree's name, also after it has left view."""
        return self.references.name(int(entity_id))

    def is_item(self, entity_id):
        return "item_id" in self.references.entities.get(entity_id, {})

    def ability(self, aid):
        if aid not in self.ability_data:
            self.ability_data[aid] = self.catalog.ability(aid)
        if aid not in self.skill_names:
            value = self.ability_data[aid]["name"]
            base = value if value != aid else "Unidentified skill"
            # Creeps and items carry copies of race spells (a creep's Purge, the Shaman's Purge). The copy is
            # the one marked, whichever is seen first, so our Shaman's spell is never "Purge variant 2".
            if aid[:2] in COPY_MARKS and self._shared(value):
                base = f"{base} ({COPY_MARKS[aid[:2]]})"
            label, variant = base, 1
            while label in self.skill_names.values():
                variant += 1
                label = f"{base} variant {variant}"
            self.skill_names[aid] = label
        return self.skill_names[aid]

    def _shared(self, name):
        """Whether another ability in the catalog has this name."""
        if not hasattr(self, "_name_counts"):
            self._name_counts = {}
            for raw in getattr(self.catalog, "abilities", {}):
                other = self.catalog.ability(raw)["name"]
                self._name_counts[other] = self._name_counts.get(other, 0) + 1
        return self._name_counts.get(name, 0) > 1
