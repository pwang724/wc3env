"""Read a prepared JSON reference; no game installation, DLL or archive access."""

import json
from copy import deepcopy
from pathlib import Path


class Catalog:
    def __init__(self, data):
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported reference schema; regenerate with tools.prepare")
        self.units = data["units"]
        self.footprints = data.get("footprints", {})
        self.abilities = data["abilities"]
        self.items = data["items"]
        self.upgrades = data["upgrades"]
        self.damage_multipliers = data["damage_multipliers"]
        self.buffs = data.get("buffs", {})

    def aura_buff(self, raw):
        """A buff a passive ability applies (Endurance Aura, the Healing Ward's aura): on every unit near its
        source, and not something a spell adds or removes. Item and creep copies of an aura need not be passive."""
        sources = self.buffs.get(raw, {}).get("abilities", [])
        return any(self.abilities.get(a, {}).get("levels", {}).get("1", {}).get("passive") for a in sources)

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Prepared reference missing: {path}. Run python -m tools.prepare reference"
            ) from None
        return cls(data)

    def unit(self, raw):
        return deepcopy(self.units[raw])

    def ability(self, raw, level=1):
        try:
            return deepcopy(self.abilities[raw]["levels"][str(level)])
        except KeyError:
            raise ValueError(f"Ability {raw} level {level} is absent from the prepared reference") from None

    def item(self, raw):
        return deepcopy(self.items[raw])

    def requirement_name(self, raw, level=1):
        if raw in self.units:
            return self.units[raw]["name"]
        names = self.upgrades.get(raw, {}).get("names", [])
        return names[min(level - 1, len(names) - 1)] if names else "Unidentified research"

    # ---- economy and tech tree -------------------------------------------------------------------

    def name(self, raw):
        if raw in self.TIERS:
            return ("Town Hall", "tier 2 hall (Keep)", "tier 3 hall (Castle)")[self.TIERS[raw] - 1]
        if raw in self.units:
            return self.units[raw]["name"]
        if raw in self.upgrades:
            return (self.upgrades[raw].get("names") or [raw])[0]
        return self.items.get(raw, {}).get("name", raw)

    def race_types(self, race):
        """A race's melee units and structures: whatever its worker, halls and producers reach.

        The melee worker is the one its own hall trains; campaign workers of the same race are not.
        """
        workers = [
            raw
            for raw, u in self.units.items()
            if u["race"] == race and any(raw in self.units.get(b, {}).get("trains", []) for b in u["builds"])
        ]
        found, names, frontier = [], set(), workers
        while frontier:
            raw = frontier.pop(0)
            unit = self.units.get(raw)
            if unit is None or raw in found:
                continue
            found.append(raw)
            frontier += unit["builds"] + unit["trains"] + unit["upgrades_to"]
        # Two type ids can share a name (Siege Engine with and without Barrage): list the first.
        return [raw for raw in found if not (self.units[raw]["name"] in names or names.add(self.units[raw]["name"]))]

    def upgrade_cost(self, source, target):
        """Upgrading a structure in place costs the difference between the two."""
        a, b = self.units[source], self.units[target]
        return max(0, b["gold"] - a["gold"]), max(0, b["lumber"] - a["lumber"])

    def satisfies(self, raw):
        """Types that count as `raw` for a requirement: itself and what it upgrades into (Keep for Town Hall)."""
        found, frontier = set(), [raw]
        while frontier:
            current = frontier.pop()
            if current not in found:
                found.add(current)
                frontier += self.units.get(current, {}).get("upgrades_to", [])
        return found

    TIERS = {"TWN1": 1, "TWN2": 2, "TWN3": 3}  # "any hall of this tier": the item tables use these

    def hall_tier(self, raw):
        """1 for a town hall, 2 for what it upgrades into, 3 for the next; 0 for anything else."""
        u = self.units.get(raw, {})
        if not (
            u.get("structure")
            and u.get("food_made", 0) >= 10
            and u.get("race") in ("human", "orc", "undead", "nightelf")
        ):
            return 0
        tier, current = 1, raw
        while True:
            below = next((r for r, v in self.units.items() if current in v["upgrades_to"]), None)
            if below is None:
                return tier
            tier, current = tier + 1, below

    def missing(self, requires, tech):
        """Requirement ids not met by `tech`, a map of type or research id to the count owned."""
        unmet = []
        for r in requires:
            if r in self.TIERS:
                if not any(count > 0 and self.hall_tier(t) >= self.TIERS[r] for t, count in tech.items()):
                    unmet.append(r)
            elif not any(tech.get(t, 0) > 0 for t in self.satisfies(r)):
                unmet.append(r)
        return unmet

    def upgrade_level(self, raw, researched):
        """The next level's definition, or None when the research is complete."""
        levels = self.upgrades[raw]["levels"]
        return levels[researched] if researched < len(levels) else None

    def attack_matchups(self, definitions):
        attacks = {u["base_attack_type"] for u in definitions.values()}
        armors = {u["base_armor_class"] for u in definitions.values()}
        return {
            "multipliers": {
                attack: {armor: value for armor, value in row.items() if armor in armors}
                for attack, row in self.damage_multipliers.items()
                if attack in attacks
            },
            "source": "Installed Units/MiscGame.txt; keys match base_attack_type and base_armor_class.",
            "limits": "Primary basic attack only, before numerical armor and other defenses. Base data does not reveal upgrades, buffs or immunity. Not spell damage or final DPS.",
        }
