"""Every fixed (hardcoded) agent policy in one place: what micro may do, rules that trim its menu,
and the defaults code applies when macro leaves something undone.

Prompts hold the text the models read; this module holds the rules code applies without asking.
"""

import json
import re
from functools import cache
from math import hypot
from pathlib import Path

from .facts import learnable
from .roles import role
from .strength import observed_strength
from .workers import is_worker

# Worker jobs and transformations remain macro decisions, including when a
# Peasant, Militia or Ghoul belongs to an army or scouting group.
ECONOMIC_ORDERS = {
    "harvest",
    "resumeharvesting",
    "returnresources",
    "repair",
    "repairon",
    "repairoff",
    "renew",
    "renewon",
    "renewoff",
    "restore",
    "restoreon",
    "restoreoff",
    "militia",
    "militiaoff",
    "townbellon",
    "townbelloff",
    "entangle",
    "entangleinstant",
    "root",
    "unroot",
}

# Tiny-building items and blight creation are base construction, not army
# recovery. Blink, teleportation and other army movement items remain available.
ECONOMIC_ITEM_ABILITIES = {"AIbh", "AIbs", "AIbb", "AIbl", "AIbf", "AIbg", "AIbr", "AIbt", "Ablp"}


def army_item(item):
    return not any(a["ability_id"] in ECONOMIC_ITEM_ABILITIES for a in item.get("abilities", []))


# ---- Macro defaults ------------------------------------------------------------------------------
# No-brainers code does when macro leaves them undone; macro sees each in its order feedback.


# Standard skill order where it differs from the hero's listed order; unlisted skills follow in
# listed order. Archmage: Water Elemental, Brilliance Aura; Mountain King: Storm Bolt;
# Far Seer: Feral Spirit; Dreadlord: Carrion Swarm; Warden: Fan of Knives.
PREFERRED_SKILLS = ("AHwe", "AHab", "AHtb", "AOsf", "AUcs", "AEfk")
GOLD_PER_MINE = 5  # more workers on one mine add no income


@cache
def hero_builds():
    """{hero type id: [ability id learned at level 1, 2, ...]}: standard builds from guides (data/hero_builds.json)."""
    return json.loads((Path(__file__).with_name("data") / "hero_builds.json").read_text(encoding="utf-8"))["builds"]


def skill_to_learn(catalog, hero, learned):
    """The skill code learns for a point macro left unspent: the next step of the hero's standard build
    (hero_builds) when that is learnable now; otherwise the deepest skill open now, on a tie the first in
    PREFERRED_SKILLS, then the hero's first listed. None when nothing can be learned."""
    if hero["level"] <= sum(learned.values()):
        return None
    options = learnable(catalog, hero, learned)
    taken = {}
    for raw in hero_builds().get(hero["type_id"], []):
        taken[raw] = taken.get(raw, 0) + 1
        if taken[raw] > learned.get(raw, 0):  # the first step of the build not yet learned
            if raw in options:
                return raw
            break

    def rank(raw):
        preferred = PREFERRED_SKILLS.index(raw) if raw in PREFERRED_SKILLS else len(PREFERRED_SKILLS)
        return (learned.get(raw, 0), -preferred, -options.index(raw))

    return max(options, key=rank, default=None)


def gold_overflow(miners, walking):
    """Workers to send to lumber so no mine has more than GOLD_PER_MINE. `miners` maps each mine to
    every worker gathering there (walking, inside, returning); only `walking` ones (on their way to the
    mine, carrying nothing) are moved, so no carried gold is lost."""
    moved = []
    for mine, workers in miners.items():
        extra = len(workers) - GOLD_PER_MINE
        moved += [uid for uid in reversed(workers) if uid in walking][: max(0, extra)]
    return moved


# ---- Micro movement menu ------------------------------------------------------------------------
# Fixed rules that shape which moves micro is offered. The model still chooses among what remains.

NEAR_ENEMY = 900.0  # enemies within this distance shape "away from the enemy"
NEARBY = 1100.0  # allies and enemies within this distance count for a unit's maneuvers
BACK_OFF_SECONDS = 1.5  # how far a back-off step goes, at the unit's speed
LINE_GAP = 350.0  # how far behind the front line "behind" is
ARRIVED = 150.0  # closer than this to a destination, a move there is pointless
SHOP_REACH = 250.0  # a hero farther than this from a shop is offered a visit instead of purchases
PICKUP_RANGE = 900.0  # ground items within this distance of a hero are offered for pickup
LOOT_RETRY_SECONDS = 2.0  # a pickup order is repeated after this long if the hero has not taken the item
INVENTORY_SLOTS = 6
STUCK_DISTANCE = 100.0  # a move that left its unit idle within this of where it started went nowhere
DEAD_END_RADIUS = 200.0  # move options this close to a target that went nowhere are withheld
MOVE_SETTLE = 0.5  # game seconds before an idle unit's move is judged; the order may not have run yet


# Attack options only for enemies within the unit's attack range plus a step: no chasing through the enemy army.
ATTACK_STEP = 450.0
MELEE_ATTACK_STEP = 200.0  # the front line's step: it pushed through the crowd toward farther named targets
BACKLINE = ("ranged", "caster", "siege", "caster hero")
FRONTLINE = ("tank", "melee", "melee hero", "summon")
SIEGE_DANGER_SECONDS = 6.0  # a siege unit that would die sooner than this at its current rate is offered no attacks
MELEE_ON_ME = 250.0  # a back-line unit is offered a step back only while an enemy melee unit is this close
HERO_DANGER_SECONDS = 5.0  # a hero that would die sooner than this at its current rate is offered no attacks
HERO_LOW_HEALTH = 0.2  # a hero below this share of health with the enemy player's units near is offered no attacks
DISABLED_ORDER = "851973"  # the engine's order while a unit is stunned, cycloned or asleep; no order table names it
ATTACK_HOLD = 3.5  # seconds a unit Jev sent at a target keeps attacking it before Jev is asked about it again


def holds(actions, heroes, now):
    """{unit id: (target id, until)}: units sent at a target (attack, spell or item) keep at it for ATTACK_HOLD
    before being asked again (Huntresses switched targets every second; a Healing Salve was re-sent each second).
    Heroes are asked every second anyway, so one about to die can get out."""
    return {
        a["unit_id"]: (a["arguments"]["target_id"], now + ATTACK_HOLD)
        for a in actions
        if a["command"] in ("attack", "cast", "use_item")
        and a["arguments"].get("target_id")
        and a["unit_id"] not in heroes
    }


STILL_HIT = 1.0  # a hero that lost health this recently is still being hit; otherwise it is out of danger
DELIBERATE_HOLD = 3.0  # seconds after Jev repositions a unit, before code restarts it if idle
THREAT_MARGIN = 150.0  # an enemy this much beyond its attack range can still reach a unit within a moment


def threatened(unit, enemies, catalog):
    """Whether any enemy can hit the unit now or within a moment: within that enemy's attack range plus THREAT_MARGIN."""
    return any(
        hypot(e["x"] - unit["x"], e["y"] - unit["y"])
        <= catalog.units.get(e["type_id"], {}).get("base_attack_range", 0) + THREAT_MARGIN
        for e in enemies
    )


def within_attack_reach(unit, target, catalog):
    """The unit can start hitting the target after a short step: its attack range plus MELEE_ATTACK_STEP for the
    front line, ATTACK_STEP for everyone else."""
    step = MELEE_ATTACK_STEP if role(catalog, unit["type_id"]) in FRONTLINE else ATTACK_STEP
    reach = catalog.units.get(unit["type_id"], {}).get("base_attack_range", 0) + step
    return hypot(target["x"] - unit["x"], target["y"] - unit["y"]) <= reach


def step_back_offered(unit, enemies, catalog):
    """Back-line units step back only while an enemy melee unit is on them: a ranged enemy shooting from afar is
    no reason to walk out of range (traced orc duels: casters stepping back drifted 800 behind the line)."""
    if role(catalog, unit["type_id"]) not in BACKLINE:
        return True
    if role(catalog, unit["type_id"]) == "siege":
        return True  # siege steps out like a hero when about to die, whoever is hitting it
    return any(
        role(catalog, e["type_id"]) in FRONTLINE and hypot(e["x"] - unit["x"], e["y"] - unit["y"]) <= MELEE_ON_ME
        for e in enemies
    )


def keep_offered(unit, enemies, catalog):
    """In reach of enemies, "keep" is offered only for an order that chose something: a target, or
    a move. An attack-move there (often Warcraft's AI's) hits whatever it meets, so a target is picked."""
    order = unit["order"] or {}
    if order.get("name") != "attack" or order.get("target_id"):
        return True
    return not any(hypot(e["x"] - unit["x"], e["y"] - unit["y"]) < FIGHT_RANGE for e in enemies)


def portal_home_offered(obs):
    """Town Portal home is offered only while an enemy player's units are in view. Against neutral
    creeps a hero walks out of reach and comes back; a portal wastes the scroll and the camp."""
    players = {p["id"] for p in obs.get("players", []) if p["kind"] == "player" and p.get("relation") == "enemy"}
    return any(e["owner"] in players and e["hp"] > 0 for e in obs.get("visible_enemies", []))


def arrived(unit, point, obs, catalog):
    """Whether a move to `point` is pointless. A structure standing on the point stops units at its
    edge (a group anchored on its town hall), so its half-footprint counts as arrived too."""
    reach = ARRIVED
    for other in obs["units"] + obs.get("visible_enemies", []):
        width, height = catalog.footprints.get(other["type_id"], (0, 0)) if other["structure"] else (0, 0)
        if abs(point["x"] - other["x"]) <= width / 2 and abs(point["y"] - other["y"]) <= height / 2:
            reach = max(reach, max(width, height) / 2 + ARRIVED)
    return hypot(point["x"] - unit["x"], point["y"] - unit["y"]) <= reach


def went_nowhere(move, unit):
    """A move is a dead end when its unit is idle again, still where it was when told to go
    (an unreachable back-off point, or a spot it cannot get closer to)."""
    return unit["order"] is None and hypot(unit["x"] - move["from"][0], unit["y"] - move["from"][1]) < STUCK_DISTANCE


def dead_end(point, dead_ends):
    """Whether a candidate move aims where an earlier move from this position went nowhere."""
    return any(hypot(point["x"] - x, point["y"] - y) < DEAD_END_RADIUS for x, y in dead_ends)


def quiet(members, obs, pickups_by_unit, catalog, group_at=None):
    """Skip a micro call when it could only confirm the status quo. That needs a group destination
    and, for every member: no enemy within NEARBY, not wounded (recovery choices), no item to pick up
    or shop in view for a hero, and either idle at the destination or already walking there."""
    if not group_at:
        return False
    enemies = [e for e in obs.get("visible_enemies", []) if e["owner"] != 15 or not e["structure"]]
    for unit in members:
        if any(hypot(e["x"] - unit["x"], e["y"] - unit["y"]) < NEARBY for e in enemies):
            return False
        if unit["hp"] < unit["max_hp"] or pickups_by_unit.get(unit["unit_id"]):
            return False
        if unit["hero"] and obs.get("shops"):
            return False
        order = unit["order"]
        if order is None and not arrived(unit, group_at, obs, catalog):
            return False
        walking_there = (
            order
            and order.get("name") == "move"
            and hypot(order.get("x", 0) - group_at["x"], order.get("y", 0) - group_at["y"]) <= ARRIVED
        )
        if order is not None and not walking_there:
            return False
    return True


def loot_targets(members, obs, pickups_by_unit):
    """No-brainer loot: each hero walks to its closest item from `pickups` as soon as it drops, mid-fight
    too (tomes and runes always; other items while it has a free slot). {hero id: item id}.
    Micro is not asked about a hero while it is collecting."""
    items = {item["item_id"]: item for item in obs.get("items", [])}
    chosen = {}
    for unit in members:
        found = [items[p["item_id"]] for p in pickups_by_unit.get(unit["unit_id"], []) if p["item_id"] in items]
        if not found:
            continue
        taken = set(chosen.values())
        free = [item for item in found if item["item_id"] not in taken] or found
        closest = min(free, key=lambda item: hypot(item["x"] - unit["x"], item["y"] - unit["y"]))
        chosen[unit["unit_id"]] = closest["item_id"]
    return chosen


def summons_unit(catalog, raw):
    """A spell creating a unit that can move (Water Elemental, Spirit Wolves, Treants), not a ward. Micro is
    offered it only with an enemy near: cast on the walk, the summon runs out before the fight."""
    effects = catalog.abilities.get(raw, {}).get("levels", {}).get("1", {}).get("effects") or {}
    made = effects.get("UnitID")
    return bool(made) and (catalog.units.get(made, {}).get("base_move_speed") or 0) > 0


CREEP_ESCAPE_HEALTH = 0.3  # below this share of its health, a unit being hit by creeps steps out
CREEP_ESCAPE_HIT_SECONDS = 2.0  # "being hit": it lost health this recently
CREEP_REACH = 600.0  # creeps this close can reach the unit
CREEP_ESCAPE_STEP = 500.0  # how far the step out goes, straight away from those creeps
CREEP_ESCAPE_RETRY_SECONDS = 1.0  # the step is renewed this often while the creeps still reach it


def creep_escapes(members, creeps, enemies, last_damage, now):
    """No-brainer while creeping: {unit id: (x, y)} for units below CREEP_ESCAPE_HEALTH that are being hit
    with creeps in reach and no enemy player's unit near, each stepping straight away from those creeps.
    Creeps give up a chase and walk back to their camp, so the unit lives and fights again once they
    have turned to other targets; losing a unit to creeps never pays."""
    out = {}
    for unit in members:
        hit = now - last_damage.get(unit["unit_id"], -float("inf")) <= CREEP_ESCAPE_HIT_SECONDS
        if unit["hp"] >= CREEP_ESCAPE_HEALTH * unit["max_hp"] or not hit:
            continue
        near = [c for c in creeps if hypot(c["x"] - unit["x"], c["y"] - unit["y"]) <= CREEP_REACH]
        if not near or any(hypot(e["x"] - unit["x"], e["y"] - unit["y"]) <= NEARBY for e in enemies):
            continue
        x, y = sum(c["x"] for c in near) / len(near), sum(c["y"] for c in near) / len(near)
        away = hypot(unit["x"] - x, unit["y"] - y) or 1.0
        out[unit["unit_id"]] = (
            round(unit["x"] + CREEP_ESCAPE_STEP * (unit["x"] - x) / away, 1),
            round(unit["y"] + CREEP_ESCAPE_STEP * (unit["y"] - y) / away, 1),
        )
    return out


STRAGGLER_DISTANCE = 800.0  # a grouped unit this far from the rest of its group has fallen behind or wandered off
STRAGGLER_CLEAR = 600.0  # ...unless an enemy is this close to it: then it is fighting where it is
STRAGGLER_HURT = 0.4  # a unit below this share of its health may be escaping on purpose; leave it
REGROUP_RETRY_SECONDS = 2.0  # a regroup order is repeated after this long if the unit is still behind


def stragglers(groups, hostile):
    """No-brainer regroup: {unit id: (x, y)} for grouped non-hero units far from the rest of their group and
    not busy with it, each sent (attack-move) to the middle of the rest. `groups`: [(members, destination or
    None)]. A unit stays when it attacks something, has an enemy near itself, is badly hurt, or is at or
    heading for the rest, or for the group's destination while the rest is not fighting. Heroes' trips
    (shop, heal, loot) are Jev's to decide."""
    back = {}
    for members, at in groups:
        for unit in members:
            rest = [u for u in members if u is not unit]
            if not rest or unit["hero"] or unit["hp"] < STRAGGLER_HURT * unit["max_hp"]:
                continue
            x, y = sum(u["x"] for u in rest) / len(rest), sum(u["y"] for u in rest) / len(rest)
            order = unit.get("order") or {}
            if order.get("target_id") is not None:
                continue
            fighting = any(hypot(e["x"] - u["x"], e["y"] - u["y"]) <= NEAR_ENEMY for e in hostile for u in rest)
            spots = [(x, y)] + ([(at["x"], at["y"])] if at and not fighting else [])
            where = [(unit["x"], unit["y"])] + ([(order["x"], order["y"])] if "x" in order else [])
            if any(hypot(a - c, b - d) <= STRAGGLER_DISTANCE for a, b in where for c, d in spots):
                continue
            if any(hypot(e["x"] - unit["x"], e["y"] - unit["y"]) <= STRAGGLER_CLEAR for e in hostile):
                continue
            back[unit["unit_id"]] = (round(x, 1), round(y, 1))
    return back


# ---- Fusion: Warcraft's AI plays our side, Jev fights -------------------------------------------
# Code decides when a fight starts and ends; Jev decides every unit's action inside it.

FIGHT_RANGE = 900.0  # army units this close to a hostile unit are in a fight
FIGHT_JOIN = 600.0  # army units this close to a fighting unit join it
CALM_SECONDS = 3.0  # the fight ends after this long with no hostile unit within FIGHT_RANGE
RETREAT_RATIO = 0.6  # in a game, retreat when our strength falls below this share of the enemy's here
RETREAT_AFTER = 3.0  # seconds into a fight before the strength comparison counts (the army arrives)
RETREAT_SECONDS = 8.0  # how long a retreat lasts before Warcraft's AI takes the units back
NEUTRAL_PASSIVE = 15  # critters and shops, never a fight


def hostiles(obs):
    """Living hostile units in view (enemy players and creeps), not structures or critters."""
    return [e for e in obs["visible_enemies"] if e["hp"] > 0 and not e["structure"] and e["owner"] != NEUTRAL_PASSIVE]


def army(obs, catalog):
    """Our living fighters: no buildings, workers or units that cannot move (wards)."""
    return [
        u
        for u in obs["units"]
        if u["hp"] > 0
        and not u["structure"]
        and not is_worker(u, catalog)
        and catalog.units.get(u["type_id"], {}).get("base_move_speed", 1) > 0
    ]


def in_fight(obs, catalog, members=()):
    """Army units in a fight: near a hostile unit, or near a unit that is. `members` already in the
    fight stay while any hostile is within FIGHT_RANGE of them. Returns (unit ids, nearby hostiles)."""
    enemies, own = hostiles(obs), army(obs, catalog)

    def near(u, others, reach):
        return any(hypot(o["x"] - u["x"], o["y"] - u["y"]) < reach for o in others)

    engaged = [u for u in own if near(u, enemies, FIGHT_RANGE)]
    if not engaged:
        return set(), []
    joined = [u for u in own if u in engaged or near(u, engaged, FIGHT_JOIN) or u["unit_id"] in members]
    return {u["unit_id"] for u in joined}, [e for e in enemies if near(e, joined, FIGHT_RANGE)]


def losing(obs, catalog, members, enemies, ratio=RETREAT_RATIO):
    """Our fighting units' strength is below `ratio` of the hostiles near them."""
    ours = observed_strength([u for u in obs["units"] if u["unit_id"] in members], catalog)
    theirs = observed_strength(enemies, catalog)
    return theirs > 0 and ours < ratio * theirs


def overridden(action, unit, obs):
    """Whether Warcraft's AI replaced Jev's attack or move for this unit and it should be resent:
    the unit is no longer on that order, and the target is alive or the destination not reached."""
    order, args = unit.get("order") or {}, action["arguments"]
    if action["command"] == "attack" and "target_id" in args:
        alive = any(e["unit_id"] == args["target_id"] and e["hp"] > 0 for e in obs["visible_enemies"])
        return alive and (order.get("name") != "attack" or order.get("target_id") != args["target_id"])
    if action["command"] == "move":
        there = hypot(unit["x"] - args["x"], unit["y"] - args["y"]) <= ARRIVED
        heading = (
            order.get("name") == "move" and hypot(order.get("x", 0) - args["x"], order.get("y", 0) - args["y"]) < 50
        )
        return not there and not heading
    return False


# ---- micro menu trims: which targets and spells Jev is offered ------------------------------------------------

FAST = 320.0  # move speed from which a unit is offered enemy back-line targets anywhere (Raiders, wolves, Knights)
BACK_LINE = ("siege", "caster", "caster hero")
BACK_LINE_REACH = 600.0  # beyond its range, how far a ranged unit or hero is offered the enemy back line
RANGED = 300.0
PEEL_REACH = 800.0  # how close a fighter must be to an enemy hitting our back line to be offered it
# Every targeted heal in the game data (with creep and neutral copies), and Death Coil and Holy Light.
HEALS = (
    "AUdc",
    "ACdc",
    "AHhb",
    "AIhl",
    "Ahea",
    "Anhe",
    "Anh1",
    "Anh2",
    "Arej",
    "ACrj",
    "ACr2",
    "AOhw",
    "ANhw",
    "AChv",
    "AIrl",
)
BUY_RETRY_SECONDS = (
    20.0  # a purchase a unit just tried is not offered again for this long (a failed one was re-tried every second)
)
HERO_HEAL_BELOW = 0.5  # a hero of ours below this share of health is healed before any unit
UNIT_HEAL_BELOW = 0.4  # a unit of ours is worth a heal below this share of health
# Buffs given in order of who gains most: our heroes, then tanks and high-damage units, then everyone else
# (Footmen, casters); never summons, wards or workers (a Priest's Inner Fire skipped the Mountain King).
BUFF_QUEUE = ("Ainf", "ACif")
HIGH_DPS = 14.0  # base damage per second from which a unit is a high-damage dealer (Rifleman 14, Knight 24)
SACRIFICE = ("AUdr",)  # Dark Ritual: kills its own target for mana
SACRIFICE_HEALTH = 0.25  # below this share of health a unit of ours may be sacrificed
HP_PRICED = ("Acmg",)  # Control Magic: mana cost is DataB times the target summon's current health
DISPELS = ("Aprg", "Apg2", "ACpu", "Aadm", "ACdm", "ACd2", "Andm")  # Purge, Abolish Magic: they destroy summons
COUPLE = "coupleinstant"  # an Archer mounting a Hippogryph, or a Hippogryph picking up an Archer
DAMAGE = re.compile(r"\bdeal(?:s|ing)?\b[^.]*damage|\bdamages\b", re.I)


def far_target(unit, target, catalog, threats):
    """Why an enemy beyond a short step is still offered as an attack, or None: "peel" when it just hit our
    back line and this fighter is near it, "raid" when it is the enemy back line and this unit is fast, or
    ranged (or a hero) with it within range plus BACK_LINE_REACH."""
    mine = catalog.unit(unit["type_id"])
    distance = hypot(target["x"] - unit["x"], target["y"] - unit["y"])
    if target["unit_id"] in threats and role(catalog, unit["type_id"]) not in BACK_LINE and distance <= PEEL_REACH:
        return "peel"
    if role(catalog, target["type_id"]) in BACK_LINE:
        if mine.get("base_move_speed", 0) >= FAST:
            return "raid"
        shooter = unit["hero"] or mine.get("base_attack_range", 0) >= RANGED
        if shooter and distance <= mine.get("base_attack_range", 0) + BACK_LINE_REACH:
            return "raid"
    return None


def repeats_effect(target, ability, casting):
    """The spell's effect is on the target already, or another of ours is casting it there (`casting`: set of
    (order, target id)): a second cast adds nothing. Spells that deal damage still hurt (Frost Nova on a slowed unit)."""
    if DAMAGE.search(ability.get("description", "")):
        return False
    buffs = {b.strip() for b in (ability.get("effects", {}).get("BuffID") or "").split(",") if b.strip()}
    if buffs & set(target.get("buffs", [])):
        return True
    return any((o["name"], target["unit_id"]) in casting for o in ability["orders"] if o["kind"] == "cast")


def spell_targets(caster, ability, targets, catalog):
    """Trim a targeted spell's candidates by its policy: a heal's queue (our heroes below half health first,
    otherwise real units below UNIT_HEAL_BELOW, never a summon, ward or worker; damage on enemies stays);
    Dark Ritual only on a summon or a unit about to die; Control Magic only where its real cost is affordable; a dispel
    never on our own summons, which it destroys."""
    aid = ability["ability_id"]
    if aid in HEALS:  # Death Coil went on skeletons
        friends = [v for v in targets if v["owner"] == caster["owner"]]
        heroes = [v for v in friends if v["hero"] and v["hp"] < HERO_HEAL_BELOW * v["max_hp"]]
        units = [
            v
            for v in friends
            if role(catalog, v["type_id"]) not in ("summon", "ward", "worker")
            and v["hp"] < UNIT_HEAL_BELOW * v["max_hp"]
        ]
        targets = [v for v in targets if v["owner"] != caster["owner"]] + (heroes or units)
    if aid in BUFF_QUEUE:  # the first tier with anyone still lacking the buff

        def tier(v):
            if v["hero"]:
                return 0
            unit = catalog.units.get(v["type_id"], {})
            damage, period = unit.get("damage") or [0, 0], unit.get("base_attack_period") or 0
            dps = sum(damage) / 2 / period if period else 0
            return 1 if role(catalog, v["type_id"]) == "tank" or dps >= HIGH_DPS else 2

        friends = [
            v
            for v in targets
            if v["owner"] == caster["owner"] and role(catalog, v["type_id"]) not in ("summon", "ward", "worker")
        ]
        best = min((tier(v) for v in friends), default=None)
        targets = [v for v in targets if v["owner"] != caster["owner"]] + [v for v in friends if tier(v) == best]
    if aid in SACRIFICE:  # a Lich sacrificed healthy Gargoyles
        targets = [
            v for v in targets if role(catalog, v["type_id"]) == "summon" or v["hp"] < SACRIFICE_HEALTH * v["max_hp"]
        ]
    if aid in DISPELS:  # a Shaman purged our own Feral Spirit wolves
        targets = [v for v in targets if v["owner"] != caster["owner"] or role(catalog, v["type_id"]) != "summon"]
    if aid in HP_PRICED:  # Control Magic costs a share of the summon's current health, not its listed mana cost
        share = float(ability.get("effects", {}).get("DataB") or 0)
        targets = [v for v in targets if caster["mana"] >= share * v["hp"]]
    return targets


def pairing_partner(unit, own, catalog, order_name=COUPLE):
    """Another type of ours nearby that can take part in the same order (an Archer for a Hippogryph)."""
    return any(
        other["type_id"] != unit["type_id"]
        and hypot(other["x"] - unit["x"], other["y"] - unit["y"]) < NEARBY
        and any(
            o["name"] == order_name
            for live in other.get("abilities", [])
            for o in catalog.abilities.get(live["ability_id"], {}).get("levels", {}).get("1", {}).get("orders", [])
        )
        for other in own
    )


def danger_seconds(unit, catalog):
    """How soon a unit may be about to die before it is made to get out, or None for units that fight on:
    heroes (HERO_DANGER_SECONDS) and siege (SIEGE_DANGER_SECONDS)."""
    if unit["hero"]:
        return HERO_DANGER_SECONDS
    return SIEGE_DANGER_SECONDS if role(catalog, unit["type_id"]) == "siege" else None


def low_hero(unit, enemy_units):
    """A hero below HERO_LOW_HEALTH with an enemy player's unit (not a creep) within NEAR_ENEMY: it gets out
    whether or not it is hit this second (a Tauren Chieftain at 82/800 walked back into the enemy army)."""
    return (
        unit["hero"]
        and unit["hp"] < HERO_LOW_HEALTH * unit["max_hp"]
        and any(hypot(e["x"] - unit["x"], e["y"] - unit["y"]) <= NEAR_ENEMY for e in enemy_units)
    )


def escape_only(unit, options, dies_in, catalog):
    """A hero or siege unit that would die within its danger_seconds at its current rate is offered no attacks:
    what remains is how to get out (step back, spells, items). Kept only when a step back is on the menu."""
    limit = danger_seconds(unit, catalog)
    if limit is None or dies_in is None or dies_in >= limit:
        return options
    attacking = (unit["order"] or {}).get("name") == "attack"
    safe = {
        key: option
        for key, option in options.items()
        if (option["action"] or {}).get("command") != "attack"
        and key != "close_in"
        and not key.startswith("clump_")
        and not (key == "keep" and attacking)
    }
    return safe if any(key in safe for key in ("back_off", "behind_line")) else options


def repeated_cast(option, catalog):
    """What a chosen spell does, if a second copy in the same answer would only repeat it: the same spell on
    the same unit, or the same area spell cast around the caster (Roar). None for anything else."""
    then = option.get("then")
    action = then or option["action"]
    if action["command"] != "cast" or (not then and option.get("order_kind") != "cast"):
        return None
    args = action["arguments"]
    if args.get("target_id"):
        return (args["order"], args["target_id"])
    if "x" in args:
        return None  # placed at a point: two wards or traps in different places both count
    ability = catalog.abilities.get(option.get("ability_id"), {}).get("levels", {}).get("1", {})
    return (args["order"], None) if ability.get("area") else None  # Bear Form changes only its caster


def unassigned_fighters(units, groups, catalog):
    """Idle fighting units of ours in no group, and the group they should join: the one holding the most of our
    heroes (then the largest). New Grunts waiting at a rally point sat out whole fights until macro named them.
    Returns ({unit id: group name}, ...) empty when no group has a hero."""
    heroes = {name: sum(1 for u in units if u["hero"] and u["unit_id"] in g["ids"]) for name, g in groups.items()}
    if not heroes or not max(heroes.values()):
        return {}
    army = max(groups, key=lambda name: (heroes[name], len(groups[name]["ids"])))
    grouped = set().union(*(g["ids"] for g in groups.values()))
    return {
        u["unit_id"]: army
        for u in units
        if u["hp"] > 0
        and not u["structure"]
        and u["unit_id"] not in grouped
        and u["order"] is None
        and not is_worker(u, catalog)
        and role(catalog, u["type_id"]) not in ("ward", "worker")
        and catalog.units.get(u["type_id"], {}).get("base_move_speed", 0) > 0
    }


# ---- Filming a game ---------------------------------------------------------------------------------------

CAMERA_FIGHT = 900.0  # a unit of ours this close to a hostile one is in a fight, and the camera goes there
CAMERA_ARMY = 1200.0  # without a fight, the camera shows our units this close to our strongest hero
CAMERA_STILL = 300.0  # the camera stays put while its spot moves less than this


def camera_spot(obs, catalog):
    """Where a filmed game's camera looks: the fight of ours with the most hostile units in it, else the army
    around our highest-level hero, else our fighting units; None with no fighting unit of ours."""
    ours = [u for u in obs["units"] if u["hp"] > 0 and not u["structure"] and not is_worker(u, catalog)]
    enemies = hostiles(obs)
    if not ours:
        return None
    near = {u["unit_id"]: [e for e in enemies if hypot(e["x"] - u["x"], e["y"] - u["y"]) <= CAMERA_FIGHT] for u in ours}
    fighter = max(ours, key=lambda u: len(near[u["unit_id"]]))
    if near[fighter["unit_id"]]:
        crowd = [fighter, *near[fighter["unit_id"]]]
    else:
        heroes = [u for u in ours if u["hero"]]
        lead = max(heroes, key=lambda u: (u["level"], u["unit_id"])) if heroes else None
        crowd = [u for u in ours if hypot(u["x"] - lead["x"], u["y"] - lead["y"]) <= CAMERA_ARMY] if lead else ours
    return (sum(u["x"] for u in crowd) / len(crowd), sum(u["y"] for u in crowd) / len(crowd))
