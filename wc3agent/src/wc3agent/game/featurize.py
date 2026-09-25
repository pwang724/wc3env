"""Observation in, text out: everything the macro model reads about the game.

MacroMemory is maintained by the agent; these functions only read it.
`system_prompt` is the static half (rules, costs and the tech tree, once); `describe` is the
per-turn half, one function per section. Neither writes to the world.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..macro.prompts import (
    GENERAL_GUIDE,
    HERO_IDLE,
    MACRO_SYSTEM,
    NEVER_FOUGHT,
    NO_PRESSURE,
    RACE_GUIDES,
    SCOUT_NEVER,
    SCOUT_STALE,
    UNGROUPED,
)
from ..prompts import HERO_RULE
from .facts import fight_summary, learnable
from .references import objects
from .shops import available_items
from .strength import camp_strength, observed_strength, unit_strength
from .workers import MINE_CAPACITY, is_worker
from .world import trading_shops

NEUTRAL_HOSTILE, HERO_LIMIT = 12, 3
HERO_IDLE_SECONDS = 5.0  # a hero idle this long gets a warning in HEROES
SCOUT_SECONDS = 90.0  # no enemy seen for this long gets a warning in ENEMY
PRESSURE_FROM_SECONDS = 330.0  # from 5:30 the army is expected to fight the enemy player regularly
PRESSURE_SECONDS = 150.0  # this long without fighting the enemy player gets a PRESSURE line in ENEMY
FIRST_SCOUT_SECONDS = 150.0  # the first scout goes out at about 1:30-2:30; no warning before then
UPKEEP = ((50, "no upkeep"), (80, "low upkeep: 70% gold income"), (10**9, "high upkeep: 40% gold income"))


def clock(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def cost(gold, lumber, food=0):
    parts = [f"{gold}g"] + ([f"{lumber}w"] if lumber else []) + ([f"{food} food"] if food else [])
    return " ".join(parts)


def ids(world, units):
    return " ".join(world.references.name(u) for u in units)


def place(u):
    return f"({round(u['x'])},{round(u['y'])})"


def race_sheet(catalog, race):
    """One race in three sections: structures (with what each trains and researches), units, heroes."""

    def needs(requires):
        return f"  needs {', '.join(catalog.name(r) for r in requires)}" if requires else ""

    roster = [(raw, catalog.units[raw]) for raw in catalog.race_types(race)]
    made_by, upgraded_from = defaultdict(list), {}
    for raw, unit in roster:
        for kind in ("builds", "trains", "upgrades_to"):
            for made in unit[kind]:
                made_by[made].append(unit["name"])
        for made in unit["upgrades_to"]:
            upgraded_from.setdefault(made, raw)

    def entry(raw, u):
        gold, lumber = (
            catalog.upgrade_cost(upgraded_from[raw], raw) if raw in upgraded_from else (u["gold"], u["lumber"])
        )
        source = "/".join(dict.fromkeys(made_by.get(raw, []))) or "start"
        stats = f"{u['hp']}hp" + (f" {round(sum(u['damage']) / 2)}dmg {u['base_attack_type']}" if u["damage"] else "")
        stats += f" {u['base_armor_class']} armor {round(u['armor'])}" + (
            f" +{u['food_made']} food" if u["food_made"] else ""
        )
        worth = round(unit_strength(u, catalog.damage_multipliers))
        verb = "upgraded from" if raw in upgraded_from else "built by" if u["structure"] else "from"
        return (
            f"  {u['name']}  {cost(gold, lumber, u['food'])} {round(u['build_seconds'])}s {verb} {source} | {stats}"
            + (f" strength {worth}" if worth else "")
            + (f" (level 5: {round(unit_strength(u, catalog.damage_multipliers, level=5))})" if u["hero"] else "")
            + ("" if u["requires_by_count"] else needs(u["requires"])),
            f"      {u['description']}",
        )

    lines = [f"{race.upper()} STRUCTURES"]
    for raw, u in roster:
        if not u["structure"]:
            continue
        lines += entry(raw, u)
        if u["trains"]:
            lines.append("      trains: " + ", ".join(catalog.name(made) for made in u["trains"]))
        if u["upgrades_to"]:
            lines.append("      upgrades to: " + ", ".join(catalog.name(made) for made in u["upgrades_to"]))
        for raw_upgrade in u["researches"]:
            for level in catalog.upgrades[raw_upgrade]["levels"]:
                lines.append(
                    f"      research {level['name']}  {cost(level['gold'], level['lumber'])} {round(level['seconds'])}s"
                    f"{needs(level['requires'])}: {level.get('description', '')}"
                )
    lines.append(f"{race.upper()} UNITS")
    for raw, u in roster:
        if not u["structure"] and not u["hero"]:
            lines += entry(raw, u)
    lines.append(
        f"{race.upper()} HEROES (the first is free; the second needs the tier 2 hall, the third tier 3; at most 3)"
    )
    for raw, u in roster:
        if u["hero"]:
            lines += entry(raw, u)
            lines.append(f"      rule: {HERO_RULE}")
            for raw_skill in u["potential_hero_abilities"]:
                skill = catalog.abilities[raw_skill]["levels"]["1"]
                lines.append(f"      skill {skill['name']}: {skill['description']}")
    return "\n".join(lines)


def item_kind(item):
    """How an item works once a hero has it."""
    if item.get("class") == "PowerUp":
        return "works at once when picked up"
    return "use it from its slot" if item.get("usable") else "passive while carried"


def stocked(catalog, shop, now, tech):
    """Items meeting initial stock timing and current tech requirements: 'Name 100g, ...'.

    Counts and restock timers are not observed, so an item bought out seconds ago still shows."""
    names = []
    for item in available_items(catalog, shop, now, tech):
        names.append(f"{item['name']} {cost(item['gold'], item['lumber'])}")
    return ", ".join(names)


def item_sheet(world):
    """Every item a shop can sell you: the map's neutral shops and your own race's."""
    catalog = world.catalog
    shops = [(b["name"], b["sells_items"]) for b in world.map.buildings]
    shops += [(catalog.units[raw]["name"], catalog.units[raw]["sells_items"]) for raw in catalog.race_types(world.race)]
    lines, seen = ["ITEMS"], set()
    for shop, stock in shops:
        if not stock or shop in seen:
            continue
        seen.add(shop)
        lines.append(f"  {shop}")
        for raw in stock:
            item = catalog.items.get(raw)
            if item:
                stock = f"first in stock at {clock(item['stock_start_seconds'])}, then {item['stock_max']} every {round(item['stock_seconds'])}s"
                if item.get("requires"):
                    stock += "; needs " + ", ".join(catalog.name(r) for r in item["requires"])
                lines.append(
                    f"    {item['name']} {cost(item['gold'], item['lumber'])} [{item_kind(item)}; {stock}]: {item['description']}"
                )
    return "\n".join(lines)


def map_sheet(world):
    """The static map: where things are, relative to home."""
    m, home = world.map, world.home
    lines = [f"MAP {m.name}; your base is at {place(home)}"]
    for start in m.enemy_starts(home):
        lines.append(f"  possible enemy base {place(start)}")
    for mine in m.mines:
        guard = f"guarded (creep level {mine['guard_level']})" if mine["guard_level"] else "unguarded"
        lines.append(
            f"  gold mine {place(mine)} {mine['gold']} gold, {guard}, {round(m.distance(home, mine))} from home"
        )
    for b in m.buildings:
        sells = ", ".join(world.catalog.name(raw) for raw in b["sells_items"] + b["sells_units"])
        lines.append(
            f"  {b['name']} {place(b)}, {round(m.distance(home, b))} from home" + (f"; sells {sells}" if sells else "")
        )
    lines.append("CREEP CAMPS (strength uses the same scale as army strength)")
    for camp in sorted(m.camps, key=lambda c: m.distance(home, c)):
        creeps = ", ".join(f"{c['name']} {c['level']}" for c in camp["creeps"])
        drops = f"; drops {', '.join(camp['drops'])}" if camp["drops"] else ""
        lines.append(
            f"  camp {camp['number']} {place(camp)} {round(m.distance(home, camp))} from home, "
            f"strength {round(camp_strength(camp, world.catalog))}: {creeps}{drops}"
        )
    return "\n".join(lines)


def system_prompt(world):
    """The macro model's system prompt: the rules, the hand-written guides and the sheets above."""
    return MACRO_SYSTEM.format(
        race=world.race,
        guide=GENERAL_GUIDE,
        race_guide=RACE_GUIDES.get(world.race, ""),
        race_sheet=race_sheet(world.catalog, world.race),
        item_sheet=item_sheet(world),
        map_sheet=map_sheet(world),
    )


def option(world, label, gold, lumber, food, requires, tech, player):
    """(label, why not): an empty reason means it can be ordered now."""
    problems = [f"needs {world.catalog.name(r)}" for r in world.catalog.missing(requires, tech)]
    if gold > player["gold"]:
        problems.append(f"{gold - player['gold']} more gold")
    if lumber > player["lumber"]:
        problems.append(f"{lumber - player['lumber']} more lumber")
    if food and player["food_used"] + food > player["food_cap"]:
        problems.append("more food")
    return label, ", ".join(problems)


def option_line(title, options):
    """'barracks1: Footman, Rifleman | NOT YET: Knight (needs Castle)'."""
    ready = [label for label, why in options if not why]
    blocked = [f"{label} ({why})" for label, why in options if why]
    parts = ([", ".join(ready)] if ready else []) + (["NOT YET: " + "; ".join(blocked)] if blocked else [])
    return f"  {title}: " + " | ".join(parts)


def can_do(world, obs, tech):
    catalog, player, units = world.catalog, obs["player"], obs["units"]
    heroes = [u for u in units if u["hero"]] + list(world.fallen.values())  # a dead hero still counts
    queued = {raw for u in units for raw in u.get("queue", [])}
    lines, producers = [], defaultdict(list)
    for u in units:
        if u["structure"] and u.get("state") != "constructing":
            queue = u.get("queue", [])
            producers[u["type_id"], u.get("state"), bool(queue), len(queue) >= 7].append(u)
    for (raw, state, occupied, full), group in producers.items():
        definition, options = catalog.units[raw], []
        for made in definition["trains"]:
            m = catalog.units[made]
            if m["hero"]:
                if len(heroes) >= HERO_LIMIT or made in queued or any(h["type_id"] == made for h in heroes):
                    continue
                free = not heroes and not any(catalog.units.get(q, {}).get("hero") for q in queued)
                tiers = m["requires_by_count"] or [m["requires"]]
                requires = tiers[min(len(heroes), len(tiers) - 1)]
                options.append(
                    option(
                        world,
                        f"train {m['name']}",
                        0 if free else m["gold"],
                        0 if free else m["lumber"],
                        m["food"],
                        requires,
                        tech,
                        player,
                    )
                )
            else:
                options.append(
                    option(world, f"train {m['name']}", m["gold"], m["lumber"], m["food"], m["requires"], tech, player)
                )
        for made in definition["upgrades_to"]:
            m = catalog.units[made]
            gold, lumber = catalog.upgrade_cost(raw, made)
            options.append(option(world, f"upgrade to {m['name']}", gold, lumber, 0, m["requires"], tech, player))
        for raw_upgrade in definition["researches"]:
            level = catalog.upgrade_level(raw_upgrade, tech.get(raw_upgrade, 0))
            if level and raw_upgrade not in queued:
                options.append(
                    option(
                        world,
                        f"research {level['name']}",
                        level["gold"],
                        level["lumber"],
                        0,
                        level["requires"],
                        tech,
                        player,
                    )
                )
        if options:
            for i, (label, why) in enumerate(options):
                blocked = ""
                if state == "upgrading":
                    blocked = "building is upgrading"
                elif label.startswith("upgrade to ") and occupied:
                    blocked = "production queue must finish first"
                elif full:
                    blocked = "production queue is full"
                if blocked:
                    options[i] = label, ", ".join(filter(None, (why, blocked)))
            lines.append(option_line(f"{definition['name']} {ids(world, group)}", options))
    workers = [u for u in units if catalog.units.get(u["type_id"], {}).get("builds")]
    if workers:
        built = [catalog.units[raw] for raw in catalog.units[workers[0]["type_id"]]["builds"]]
        lines.append(
            option_line(
                "workers build",
                [option(world, m["name"], m["gold"], m["lumber"], 0, m["requires"], tech, player) for m in built],
            )
        )
    return lines


def structure_work(worker, target):
    """Distinguish constructing a new structure from repairing a finished one."""
    order = worker.get("order") or {}
    if (
        not target
        or not target.get("structure")
        or target.get("owner") != worker["owner"]
        or target["hp"] <= 0
        or worker["hp"] <= 0
        or order.get("target_id") != target["unit_id"]
        or order.get("name") not in ("repair", "smart")
    ):
        return None
    if target.get("state") == "constructing":
        return "building"
    if order["name"] == "repair" or target["hp"] < target["max_hp"]:
        return "repairing"
    return None


def structure_line(world, u, now, workers=()):
    damaged = f" {u['hp']}/{u['max_hp']}hp" if u["hp"] < u["max_hp"] and not u.get("state") else ""
    parts = []
    if u.get("state") == "constructing":
        builders = [w for w in workers if structure_work(w, u) == "building"]
        # Hit points rise with the work done (from a tenth at the start), whoever builds and however many.
        left = round(u["state_seconds"] * max(0.0, 1 - (u["hp"] / u["max_hp"] - 0.1) / 0.9))
        parts.append(
            f"constructing, about {left}s of work left"
            + f"; builders: {ids(world, builders) if builders else 'none observed'}"
            + (
                f"; STOPPED, nobody is building it (repair a worker on {world.references.name(u)} to resume)"
                if not builders and u["unit_id"] in world.stalled
                else ""
            )
        )
    elif u.get("state"):
        left = world.seconds_left(u, "state", u["state_seconds"], now)
        parts.append(u["state"] + (f", {left}s left" if left is not None else ""))
    if u.get("queue"):
        left = world.seconds_left(u, "queue", u["queue_seconds"], now)
        names = [world.catalog.name(raw) for raw in u["queue"]]
        parts.append(
            f"making {names[0]}"
            + (f" ({left}s left)" if left is not None else "")
            + (f", then {', '.join(names[1:])}" if names[1:] else "")
        )
    elif not u.get("state") and (
        world.catalog.units[u["type_id"]]["trains"] or world.catalog.units[u["type_id"]]["researches"]
    ):
        parts.append("idle")
    for live in u.get("abilities", []):
        definition = world.catalog.abilities.get(live["ability_id"], {})
        level = definition.get("levels", {}).get(str(live["level"]), {})
        if any(order.get("order_id") for order in level.get("orders", [])):
            parts.append(
                f"{level['name']} level {live['level']}: {live['mana_cost']} mana, "
                f"{live['cooldown_remaining']:g}s cooldown remaining"
            )
    return f"  {world.references.label(u)}{damaged}" + (": " + "; ".join(parts) if parts else "")


def structure_lines(world, structures, now, workers=()):
    """One line per structure with something to say; finished, undamaged, idle-by-nature ones (Farms) by kind."""
    lines, quiet = [], defaultdict(list)
    for u in structures:
        line = structure_line(world, u, now, workers)
        if line == f"  {world.references.label(u)}":
            quiet[u["type_id"]].append(u)
        else:
            lines.append(line)
    return lines + [f"  {world.catalog.name(raw)} x{len(group)}: {ids(world, group)}" for raw, group in quiet.items()]


def worker_lines(world, workers, obs):
    targets = objects(obs, world.known_units)
    jobs = defaultdict(list)
    for w in workers:
        order = w["order"]
        target = targets.get((order or {}).get("target_id"))
        target_name = world.references.label(target) if target else None
        work = structure_work(w, target)
        if order is None:
            jobs["IDLE"].append(w)
        elif order["name"] in ("harvest", "resumeharvesting", "returnresources"):
            resource = world.gathering.get(w["unit_id"], "resources")
            job = f"returning {resource} to {target_name}" if target and target.get("structure") else resource
            jobs[job].append(w)
        elif order["name"] in world.catalog.units:
            jobs[f"going to build {world.catalog.name(order['name'])}"].append(w)
        elif work:
            jobs[f"{work} {target_name}"].append(w)
        elif order["name"] == "repair":
            jobs[f"repair order on {target_name or 'unobserved target'}"].append(w)
        elif order["name"] == "smart" and target and target.get("type_id") in ("ngol", "ugol", "egol"):
            jobs[f"gathering gold at {target_name}"].append(w)
        elif order["name"] == "smart" and target and target.get("resource") == "lumber":
            jobs[f"gathering lumber at {target_name}"].append(w)
        else:
            # An order without a known name arrives as its numeric id.
            jobs[f"{order['name']}" + (f" on {target_name}" if target_name else "")].append(w)
    inside = {u["unit_id"]: u for u in obs.get("inside", [])}
    for uid, u in inside.items():
        worker = world.known_units.get(uid)
        if not worker or not is_worker(worker, world.catalog):
            continue
        order = u["order"] or {}
        target = targets.get(order.get("target_id"))
        if order.get("name") in ("harvest", "resumeharvesting", "returnresources") and target:
            held = sum((v["order"] or {}).get("target_id") == target["unit_id"] for v in inside.values())
            cap = f" ({held} of {MINE_CAPACITY[target['type_id']]})" if target["type_id"] in MINE_CAPACITY else ""
            jobs[f"inside {world.references.label(target)} gathering gold{cap}"].append(worker)
        elif order.get("name") in world.catalog.units:
            jobs[f"inside the {world.catalog.name(order['name'])} it is building"].append(worker)
        else:
            jobs["inside a building or transport"].append(worker)
    lines = [f"  {job}: {', '.join(world.references.label(w) for w in group)}" for job, group in jobs.items()]
    here = {u["unit_id"] for u in obs["units"]} | set(inside)
    for uid, worker in world.known_units.items():
        if uid not in here and is_worker(worker, world.catalog):
            order = worker["order"] or {}
            job = (
                world.gathering.get(uid, "gathering")
                if order.get("name") in ("harvest", "resumeharvesting", "returnresources")
                else order.get("name", "idle")
            )
            lines.append(f"  {world.references.label(worker)}: currently unobserved; last seen {job}")
    return lines


def hero_skills(world, hero, learned):
    """'skills: Blizzard 2; 1 unspent skill point, learn one of: ...' from the hero's learned ability levels."""
    catalog = world.catalog
    choices = catalog.units[hero["type_id"]]["potential_hero_abilities"]
    have = {raw: learned.get(raw, 0) for raw in choices}
    parts = [f"{catalog.abilities[raw]['levels']['1']['name']} {level}" for raw, level in have.items() if level]
    text = ", skills: " + (", ".join(parts) or "none")
    unspent = hero["level"] - sum(have.values())
    if unspent > 0:
        open_now = [catalog.abilities[raw]["levels"]["1"]["name"] for raw in learnable(catalog, hero, learned)]
        text += f"; {unspent} UNSPENT SKILL POINT(S), learn one of: {', '.join(open_now)}"
    return text


@dataclass
class Scene:
    """One observation's units, sorted into the groups the turn's sections report."""

    now: float
    tech: dict  # research levels plus finished structures, which satisfy requirements
    enemies: list
    creeps: list
    structures: list
    workers: list
    heroes: list
    army: list


def scene(world, obs):
    catalog, units = world.catalog, obs["units"]
    tech = dict(world.tech)
    for u in units:
        if u["structure"] and u.get("state") != "constructing":
            tech[u["type_id"]] = tech.get(u["type_id"], 0) + 1
    enemy_ids = {p["id"] for p in obs["players"] if p["kind"] == "player" and p["relation"] == "enemy"}
    workers = [u for u in units if not u["structure"] and catalog.units.get(u["type_id"], {}).get("builds")]
    return Scene(
        now=obs["game_time_seconds"],
        tech=tech,
        enemies=[u for u in obs["visible_enemies"] if u["owner"] in enemy_ids],
        creeps=[u for u in obs["visible_enemies"] if u["owner"] == NEUTRAL_HOSTILE],
        structures=[u for u in units if u["structure"]],
        workers=workers,
        heroes=[u for u in units if u["hero"]],
        army=[u for u in units if not u["structure"] and not u["hero"] and u not in workers],
    )


def header_lines(world, obs, seen):
    player = obs["player"]
    upkeep = next(label for limit, label in UPKEEP if player["food_used"] <= limit)
    visible = [u for u in seen.enemies + seen.creeps if not u["structure"]]
    return [
        f"TIME {clock(seen.now)}   gold {player['gold']}   lumber {player['lumber']}   "
        f"food {player['food_used']}/{player['food_cap']} ({upkeep})",
        "STRENGTH yours {your_strength}   enemy army in view {enemy_strength}".format(
            **fight_summary(world.catalog, seen.heroes + seen.army, visible)
        ),
    ]


def feedback_lines(world, obs, seen):
    """What became of earlier orders: feedback, unconfirmed production and deferred orders."""
    lines = []
    if world.notes:
        lines += ["ORDER FEEDBACK SINCE LAST REQUEST"] + [f"  {note}" for note in world.notes]
    if world.outcomes.pending:
        lines += ["ORDERS AWAITING CONFIRMATION"] + [
            f"  {order['label']} submitted at {order['ordered_at']:.1f}s; not yet confirmed started"
            for order in world.outcomes.pending
        ]
    if world.control.deferred:
        lines += ["DEFERRED ORDERS (waiting to send; queue appends, ordinary unit orders replace)"] + [
            f"  turn {turn}: {command.text}"
            for pending in world.control.deferred.values()
            for command, turn in pending.commands
        ]
    return lines


def base_lines(world, obs, seen):
    known_workers = sum(is_worker(u, world.catalog) for u in world.known_units.values())
    return [
        "STRUCTURES",
        *structure_lines(world, seen.structures, seen.now, seen.workers),
        f"WORKERS ({len(seen.workers)} on the map; {known_workers} in all, counting those inside mines and buildings)",
        *worker_lines(world, seen.workers, obs),
    ]


def hero_lines(world, obs, seen):
    catalog, lines = world.catalog, ["HEROES"] if seen.heroes or world.fallen else []
    for h in seen.heroes:
        items = [
            f"slot {i['slot'] + 1} {catalog.name(i['type_id'])} [{item_kind(catalog.items.get(i['type_id'], {}))}]"
            for i in obs["inventory"]
            if i["unit_id"] == h["unit_id"]
        ]
        idle = obs["game_time_seconds"] - world.idle_since.get(h["unit_id"], obs["game_time_seconds"])
        order = h["order"]["name"] if h["order"] else "idle"
        if idle >= HERO_IDLE_SECONDS:
            order += f" for {idle:.0f}s: {HERO_IDLE}"
        lines.append(
            f"  {world.references.label(h)} level {h['level']} {h['hp']}/{h['max_hp']}hp "
            f"{h['mana']}/{h['max_mana']}mana at {place(h)}, {order}"
            + (f", items: {', '.join(items)}" if items else "")
            + hero_skills(world, h, world.skills.get(h["unit_id"], {}))
        )
    altars = [u for u in seen.structures if catalog.units[u["type_id"]]["trains"] and all(
        catalog.units.get(raw, {}).get("hero") for raw in catalog.units[u["type_id"]]["trains"])]  # fmt: skip
    for h in world.fallen.values():
        reviving = next((a for a in altars if h["type_id"] in a.get("queue", [])), None)
        how = (
            f"being revived at {world.references.name(reviving)}"
            if reviving
            else f"revive {world.references.name(altars[0])} {world.references.name(h)}"
            if altars
            else "no altar to revive at"
        )
        lines.append(f"  {world.references.label(h)} level {h['level']} DEAD: {how}")
    return lines


def army_lines(world, obs, seen):
    by_type = defaultdict(list)
    for u in seen.army:
        by_type[u["type_id"]].append(u)
    lines = ["ARMY"] if by_type else []
    grouped = set().union(*(group["ids"] for group in world.control.groups.values()))
    for raw, group in by_type.items():
        members = " ".join(
            f"{world.references.name(u)}({u['hp']}hp{'' if u['order'] else ',idle'}"
            f"{'' if u['unit_id'] in grouped else ',no group'})"
            for u in group
        )
        lines.append(f"  {world.catalog.name(raw)} x{len(group)}: {members}")
    loose = [world.references.name(u) for u in seen.army if not u["order"] and u["unit_id"] not in grouped]
    if loose:
        lines.append(f"  {UNGROUPED.format(units=', '.join(loose))}")
    return lines


def group_lines(world, obs, seen):
    groups = world.control.groups
    lines = ["GROUPS (micro owns these units until a direct order or reassignment)"] if groups else []
    by_id = {u["unit_id"]: u for u in obs["units"]}
    for name, group in groups.items():
        members = [by_id[uid] for uid in group["ids"] if uid in by_id]
        absent = " ".join(world.references.label(world.known_units[uid]) for uid in sorted(group["ids"] - by_id.keys()))
        if not members:
            lines.append(f"  {name}: {absent} not currently observed; told: {group['instruction']}")
            continue
        x, y = sum(u["x"] for u in members) / len(members), sum(u["y"] for u in members) / len(members)
        hp = round(100 * sum(u["hp"] for u in members) / max(1, sum(u["max_hp"] for u in members)))
        state = "micro replied since last macro turn" if name in world.fighting else "awaiting micro reply"
        lines.append(
            f"  {name}: {', '.join(world.references.label(u) for u in members)} at ({round(x)},{round(y)}), {hp}% health, strength "
            f"{round(observed_strength(members, world.catalog))}, {state}; told: {group['instruction']}"
            + (f"; not currently observed: {absent}" if absent else "")
        )
    return lines


def option_lines(world, obs, seen):
    return ["WHAT YOU CAN DO NOW (prices are in the lists you were given)", *can_do(world, obs, seen.tech)]


def enemy_lines(world, obs, seen):
    now, lines, by_type = obs["game_time_seconds"], ["ENEMY"], defaultdict(list)
    if world.enemy_seen_at is None:
        if now >= FIRST_SCOUT_SECONDS:
            lines.append(f"  {SCOUT_NEVER}")
    elif now - world.enemy_seen_at >= SCOUT_SECONDS:
        lines.append(f"  {SCOUT_STALE.format(seconds=round(now - world.enemy_seen_at))}")
    if now >= PRESSURE_FROM_SECONDS:
        if world.enemy_fought_at is None:
            lines.append(f"  {NEVER_FOUGHT}")
        elif now - world.enemy_fought_at >= PRESSURE_SECONDS:
            lines.append(f"  {NO_PRESSURE.format(seconds=round(now - world.enemy_fought_at))}")
    for u in seen.enemies:
        by_type[(u["type_id"], u["structure"])].append(u)
    for (raw, _), group in by_type.items():
        lines.append(f"  in view: {world.catalog.name(raw)} x{len(group)} near {place(group[0])} {ids(world, group)}")
    in_view = {u["unit_id"] for u in seen.enemies}
    hidden = [s for uid, s in world.enemy_structures.items() if uid not in in_view]
    if hidden:
        lines.append(
            "  structures seen earlier: " + ", ".join(f"{world.references.label(s)} {place(s)}" for s in hidden)
        )
    return lines if len(lines) > 1 else []


def surroundings_lines(world, obs, seen):
    """Shops we can trade with, ground items, creeps and cleared camps."""
    catalog, lines = world.catalog, []
    trading = trading_shops(obs, catalog)
    if trading and seen.heroes:
        lines.append("SHOPS IN VIEW")
        for s in trading:
            stock = stocked(catalog, s, seen.now, seen.tech) or "none yet"
            lines.append(f"  {world.references.label(s)} {place(s)} eligible items: {stock}")
    if obs["items"]:
        lines.append(
            "ITEMS ON THE GROUND: "
            + ", ".join(
                f"{world.references.label(i)} {place(i)} [{item_kind(catalog.items.get(i['type_id'], {}))}]: "
                f"{catalog.items.get(i['type_id'], {}).get('description', '')}"
                for i in obs["items"]
            )
        )
    if seen.creeps:
        lines.append(
            "CREEPS IN VIEW: " + ", ".join(f"{world.references.label(u)} {u['hp']}hp {place(u)}" for u in seen.creeps)
        )
    if world.cleared:
        lines.append("CAMPS CLEARED: " + ", ".join(str(n) for n in sorted(world.cleared)))
    return lines


def ending_lines(world, obs, seen):
    events = event_lines(world, {u["unit_id"] for u in obs["units"]})
    lines = ["SINCE YOUR LAST TURN", *events] if events else []
    return lines + ([f"GAME OVER: {obs['result']}"] if obs["result"] else [])


SECTIONS = (
    header_lines,
    feedback_lines,
    base_lines,
    hero_lines,
    army_lines,
    group_lines,
    option_lines,
    enemy_lines,
    surroundings_lines,
    ending_lines,
)


def describe(world, obs):
    """One turn's text, from the observation and what the world remembers. Reads only."""
    seen = scene(world, obs)
    return "\n".join(line for section in SECTIONS for line in section(world, obs, seen))


def event_lines(world, own):
    catalog, lines, attacked = world.catalog, [], set()
    known = objects(world.outcomes.observation, world.known_units)

    def named(uid, raw=None):
        entity = (
            {"unit_id": uid, "type_id": raw, "structure": catalog.units.get(raw, {}).get("structure", False)}
            if raw
            else known.get(uid, {"unit_id": uid})
        )
        return world.references.label(entity)

    for e in world.events:
        kind = e["kind"]
        if kind == "death":
            whose = "your" if e["owner"] == world.player else "creep" if e["owner"] == NEUTRAL_HOSTILE else "enemy"
            lines.append(f"  {whose} {named(e['unit_id'], e['type_id'])} died")
        elif kind in ("construct_finish", "upgrade_finish"):
            lines.append(f"  {named(e['unit_id'], e['type_id'])} finished")
        elif kind == "train_finish" and e["type_id"]:
            lines.append(f"  {named(e['trained_id'], e['type_id'])} trained")
        elif kind == "research_finish":
            lines.append(f"  research finished: {catalog.name(e['type_id'])}")
        elif kind == "hero_level":
            lines.append(f"  {named(e['unit_id'])} reached level {e['level']} (it has a skill point to spend)")
        elif kind == "item_sold" and e["type_id"]:
            lines.append(f"  bought {catalog.name(e['type_id'])}")
        elif kind == "item_pickup" and e["type_id"]:
            item = world.references.name(e["item_id"]) if e.get("item_id") else catalog.name(e["type_id"])
            lines.append(f"  {named(e['unit_id'])} picked up {item}")
        elif kind == "attacked" and e["unit_id"] in own:
            attacked.add(e["unit_id"])
    if attacked:
        lines.append("  under attack: " + ", ".join(named(i) for i in sorted(attacked)))
    if world.events_lost:
        lines.append(f"  ({world.events_lost} older events were lost)")
    return lines
