"""One Jev request: readable state plus one multiple-choice question per controlled unit.

`build_request` returns the request and the `Menu` that maps each answer back to an action;
`map_response` does the mapping. Entity names match the macro observation and base summary.
"""

import math

from ..game.facts import fight_summary
from ..game.policies import (
    BUY_RETRY_SECONDS,
    DISABLED_ORDER,
    ECONOMIC_ORDERS,
    STILL_HIT,
    army_item,
    danger_seconds,
    escape_only,
    low_hero,
)
from ..game.roles import role
from ..game.shops import available_items
from ..game.world import opponents, troops
from ..models.jev import choice_question
from .candidates import unit_candidates
from .prompts import (
    HERO_QUESTION,
    MICRO_RULES,
    OBSERVATION_CONTEXT,
    UNIT_QUESTION,
)
from .unit_prompts import CASTER_RULE, UNIT_PROMPTS

ARMOR_NAMES = {"large": "heavy", "small": "light", "fort": "fortified", "none": "unarmored"}
ATTACK_NAMES = {"pierce": "piercing"}


def action_label(action, names, option=None):
    if option and option.get("label") and action is None:
        return option["label"]
    if action is None:
        return "Keep current order"
    option = option or {}
    command, args = action["command"], action["arguments"]
    if command == "buy":
        return f"Buy {option.get('item_name', args['item_type_id'])} from {names.name(args['shop_id'])}"
    if command == "smart" and "target_id" in args and names.is_item(args["target_id"]):
        return f"Pick up {names.name(args['target_id'])}"
    if command == "harvest":
        target = names.references.entities.get(args["target_id"], {})
        resource = "lumber" if target.get("resource") == "lumber" else "gold"
        return f"Gather {resource} at {names.name(args['target_id'])}"
    if option.get("label"):  # named moves and Town Portal say what the action is for
        target = f" ({names.name(args['target_id'])})" if "target_id" in args else ""
        point = f" ({args['x']}, {args['y']})" if command == "move" else ""
        return option["label"] + target + point
    target = f" {names.name(args['target_id'])}" if "target_id" in args else ""
    point = f" ({args['x']}, {args['y']})" if "x" in args else ""
    if command == "cast":
        aid = option.get("ability_id")
        ability = (
            names.ability(aid)
            if aid
            else next(
                (
                    a["name"]
                    for a in names.ability_data.values()
                    if any(o["name"] == args["order"] for o in a["orders"])
                ),
                "Spell",
            )
        )
        kind = option.get("order_kind") or next(
            (o["kind"] for a in names.ability_data.values() for o in a["orders"] if o["name"] == args["order"]), "cast"
        )
        if kind == "deactivate":
            return f"Turn off {ability}"
        if kind != "cast":
            return f"{'Enable' if kind == 'enable_autocast' else 'Disable'} {ability} autocast"
        return f"{ability}{' on' if target else ' at' if point else ''}{target}{point}"
    if command == "use_item":
        return f"Use {option.get('item_name', option.get('meaning', 'item'))} in slot {args['slot'] + 1}{target}{point}"
    if command == "move":
        return "Move to" + point
    return command.capitalize() + target + point


class Menu:
    """What each controlled unit was offered, and how Jev's answer maps back to it."""

    def __init__(self):
        self.options = {}  # unit id -> {option key: {action, meaning, ...}}
        self.question_names = {}  # unit id -> the question's name (the unit's readable name)
        self.choice_keys = {}  # unit id -> {choice label shown to Jev: option key}


def spell_details(ability, unit, catalog):
    if ability.get("passive"):
        return {"rank": ability["level"], "status": "Passive"}
    missing = ability["missing_requirements"]
    if missing:
        requirements = [
            catalog.requirement_name(raw, ability.get("requirement_counts", {}).get(raw, 1)) for raw in missing
        ]
        return {"rank": ability["level"], "status": "Requires " + ", ".join(requirements)}
    if not any(o["order_id"] and o["target_form"] for o in ability["orders"]):
        return {"rank": ability["level"], "status": "Not directly commandable"}
    status = "Ready" if ability["ready"] else "Cooling down" if ability["cooldown_remaining"] > 0 else "Not enough mana"
    return {
        "rank": ability["level"],
        "mana_cost": ability["mana_cost"],
        "cooldown_seconds": ability["cooldown_seconds"],
        "ready_in_seconds": ability["cooldown_remaining"],
        "status": status,
    }


def short_description(definition):
    """Keep the installed role/initial skill, omitting future research and flavor."""
    description = definition.get("description", "")
    if not description:
        return ""
    sentences = description.split(". ")
    summary = ". ".join(
        sentence
        for sentence in sentences[:2]
        if not sentence.startswith(("Can learn", "Can also learn", "Can gain", "Attacks "))
    )
    return summary.rstrip(".") + "." if summary else ""


def base_stats(definition):
    """What decides a fight: hit points, damage per second, range, armor and speed."""
    damage = definition.get("damage") or [0, 0]
    period = definition.get("base_attack_period") or 0
    return {
        "hp": definition.get("hp"),
        "damage_per_second": round(sum(damage) / 2 / period, 1) if period else 0,
        "attack_type": ATTACK_NAMES.get(definition.get("base_attack_type"), definition.get("base_attack_type")),
        "attack_range": definition.get("base_attack_range"),
        "armor": definition.get("armor"),
        "armor_class": ARMOR_NAMES.get(definition.get("base_armor_class"), definition.get("base_armor_class")),
        "move_speed": definition.get("base_move_speed"),
    }


def order_phrase(order, names):
    """'attacking grunt3', 'attack-moving to (500, 2900)', 'moving to (500, 2900)', 'idle'."""
    if not order:
        return "idle"
    name = str(order["name"])
    if name == DISABLED_ORDER:
        by = f" by {names.name(order['target_id'])}" if order.get("target_id") else ""
        return f"disabled (stunned, cycloned or asleep){by}"
    if order.get("target_id"):
        return f"{'attacking' if name == 'attack' else name} {names.name(order['target_id'])}"
    if "x" in order and "y" in order:
        verb = {"attack": "attack-moving to", "move": "moving to"}.get(name, f"{name} at")
        return f"{verb} ({round(order['x'])}, {round(order['y'])})"
    return name


def readable_units(units, obs, capabilities, catalog, names, history, matchups, controlled_ids):
    groups = {}
    for unit in units:
        raw, uid = unit["type_id"], unit["unit_id"]
        detailed = uid in controlled_ids
        group_name = names.groups[raw]
        if group_name not in groups:
            definition = catalog.unit(raw)
            groups[group_name] = {
                "role": role(catalog, raw),
                "description": short_description(definition),
                "base_stats": base_stats(definition),
                "members": {},
            }
        group = groups[group_name]
        delta = next((d for d in history.get("observed_changes", []) if d["unit_id"] == uid), {})
        change = f" ({delta['hp_change']:+g} in the last {delta['over_seconds']}s)" if delta.get("hp_change") else ""
        recent = history.get("health", {}).get(uid)
        if recent and (recent["hp_lost"] or recent["hp_gained"]):  # the recent-health record, in the hit points text
            change += f", lost {recent['hp_lost']:g} and gained {recent['hp_gained']:g} in the last {recent['over_seconds']:g}s"
            if "seconds_since_hp_loss" in recent:
                change += f", last hit {recent['seconds_since_hp_loss']:g}s ago"
        member = {"hp": f"{unit['hp']}/{unit['max_hp']}{change}", "x": round(unit["x"]), "y": round(unit["y"])}
        attackers = {
            e["attacker_id"]: e["observed_at"]
            for e in history.get("recent_observed_events", [])
            if e["kind"] == "attacked"
            and e["unit_id"] == uid
            and e.get("attacker_id")
            and obs["game_time_seconds"] - e["observed_at"] <= 5
        }
        if attackers:
            member["recent_attackers"] = {
                names.name(attacker): {"seconds_ago": round(obs["game_time_seconds"] - time, 2)}
                for attacker, time in attackers.items()
            }
        if summon := history.get("summons", {}).get(uid):
            age = obs["game_time_seconds"] - summon["observed_at"]
            member["summon"] = {
                "summoner": names.name(summon["summoner_id"]) if summon["summoner_id"] else "Not observed",
                "age_seconds": round(age, 1),
                "estimated_seconds_remaining": max(0, round(summon["lifetime_seconds"] - age, 1))
                if summon["lifetime_seconds"]
                else "Unknown",
            }
        buffs = [
            f"{catalog.buffs[b]['name']} {obs['game_time_seconds'] - since:.0f}s"
            for b, since in history.get("buffs", {}).get(uid, {}).items()
            if b in catalog.buffs and not catalog.aura_buff(b)
        ]
        if buffs:
            member["buffs"] = ", ".join(buffs)
        if "order" in unit:
            member["current_order"] = order_phrase(unit["order"], names)
        if delta.get("dx") or delta.get("dy"):
            member["moving"] = (
                f"{round(math.hypot(delta['dx'], delta['dy']))} toward ({round(unit['x'] + delta['dx'])}, {round(unit['y'] + delta['dy'])})"
            )
        if unit["hero"]:
            member["hero_level"] = unit["level"]
        if unit["hero"] or unit.get("max_mana", 0):
            member["mana"] = f"{unit['mana']}/{unit['max_mana']}"
        cap = capabilities.get(str(uid))
        if cap:
            skills = {}
            for ability in cap["abilities"] if detailed else []:
                if ability.get("orders") and all(o["name"] in ECONOMIC_ORDERS for o in ability["orders"]):
                    continue
                if ability.get("interface_ability") or ability.get("item_ability") or ability["missing_requirements"]:
                    continue  # a skill the unit cannot use yet (Defend before its research) is not worth a word
                name = names.ability(ability["ability_id"])
                rank_name = f"{name} (rank {ability['level']})"
                # The game's tooltips carry effect meanings; unlabeled editor
                # DataA/DataB and native IDs stay in local capability diagnostics.
                group.setdefault("skill_descriptions", {})[rank_name] = {
                    "effect": ability.get("description") or "No effect description in installed data.",
                    **{
                        k: ability[k]
                        for k in ("range", "area", "duration_seconds", "hero_duration_seconds")
                        if ability.get(k)
                    },
                }
                skills[name] = spell_details(ability, unit, catalog)
                spell = next((o["name"] for o in ability["orders"] if o["kind"] == "cast"), None)
                state = history.get("toggles", {}).get(uid, {}).get(spell, {})
                if "autocast" in state:
                    skills[name]["autocast"] = "on" if state["autocast"] else "off"
                if "active" in state:
                    skills[name]["switched"] = "on" if state["active"] else "off"
            if skills:
                member["skills"] = skills
        inventory = []
        for entry in obs.get("inventory", []):
            if entry["unit_id"] != uid:
                continue
            item = catalog.item(entry["type_id"])
            row = {"name": item["name"], "charges": entry["charges"]}
            if not detailed:
                inventory.append(row)
                continue
            row.update(slot=entry["slot"] + 1, effect=item["description"])
            item_ids = {a["ability_id"] for a in item["abilities"]}
            live = [a for a in (cap or {}).get("abilities", []) if a["ability_id"] in item_ids]
            row["cooldown"] = (
                {names.ability(a["ability_id"]): a["cooldown_remaining"] for a in live} if live else "Not observed"
            )
            inventory.append(row)
        if inventory:
            member["items"] = inventory
        group["members"][names.name(uid)] = member
    return groups


def attach_unit_history(own, enemies, history, names, controlled_ids):
    """Keep local observations with their subjects, including recently departed units."""
    members = {
        name: member
        for groups in (own, enemies)
        for group in groups.values()
        for name, member in group["members"].items()
    }

    def member_for(uid):
        identity = names.unit_identity.get(uid)
        if identity is None or identity[2]:
            return None
        name = names.name(uid)
        if name not in members:
            raw, friendly, _ = identity
            groups = own if friendly else enemies
            group = groups.setdefault(names.groups[raw], {"members": {}})
            members[name] = group["members"][name] = {"status": "No longer observed"}
        return members[name]

    for unit in history.get("last_seen_enemies", []):
        if names.name(unit["unit_id"]) in members:
            continue
        member = member_for(unit["unit_id"])
        if member is not None:
            member.clear()
            member.update(
                visibility="Out of sight",
                last_seen={
                    "time_seconds": unit["last_seen_at"],
                    "hp": f"{unit['hp']}/{unit['max_hp']}",
                    "x": unit["x"],
                    "y": unit["y"],
                },
            )
    # One timeline per unit: what it was told, what it then did, what happened to it. Oldest first.
    lines = {}

    def note(uid, time, text):
        if names.name(uid) in members:  # only units this state renders; the rest of our side is summarized
            lines.setdefault(names.name(uid), []).append((time, text))

    for uid_text, old in history.get("unit_history", {}).items():
        uid = int(uid_text)
        for v in old.get("submitted_orders", []):
            note(uid, v["submitted_at"], f"told: {action_label(v['action'], names)}")
        for v in old.get("observed_order_changes", []):
            note(uid, v["observed_at"], f"order became {(v['order'] or 'none').replace('_', ' ')}")
    for event in history.get("recent_observed_events", []):
        kind, t = event["kind"], event["observed_at"]
        if kind in ("hero_level", "hero_learn", "item_pickup"):  # rank, skills and inventory already show these
            continue
        if kind == "attacked" and "attacker_id" in event:
            note(event["unit_id"], t, f"attacked by {names.name(event['attacker_id'])}")
            note(event["attacker_id"], t, f"attacked {names.name(event['unit_id'])}")
        elif kind == "death":
            note(event["unit_id"], t, "died")
            member = members.get(names.name(event["unit_id"]))
            if member is not None and "hp" not in member:
                member.pop("visibility", None)
                member["status"] = "Dead (observed)"
        elif kind == "spell_effect":
            note(event["unit_id"], t, f"cast {names.ability(event['ability_id'])}")
        elif kind == "summon":
            note(event["unit_id"], t, f"summoned {names.catalog.unit(event['type_id'])['name']}")
        elif kind == "item_use":  # a consumed item can be gone before its type is read
            used = names.catalog.items.get(event["type_id"], {}).get("name", "an item")
            note(event["unit_id"], t, f"used {used}")
    controlled_names = {names.name(uid) for uid in controlled_ids}
    for name, entries in lines.items():
        if name not in controlled_names:
            continue  # allies and enemies show their current state; only commanded units carry a timeline
        entries.sort(key=lambda e: e[0])
        members[name]["timeline"] = collapse([f"{t}s: {text}" for t, text in entries])[-6:]


def plain(value):
    """A nested value as short text: 'x 1, y 2' for a dict, 'a, b' for a list."""
    if isinstance(value, dict):
        return ", ".join(f"{k.replace('_', ' ')} {plain(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(plain(v) for v in value)
    return f"{value:g}" if isinstance(value, float) else str(value)


def member_line(member):
    """An ally or enemy as one line with every field it had: 'hp 380/420, at (712, 3635), attacking grunt3,
    hit by grunt1 0.5s ago'."""
    parts = []
    for key, value in member.items():
        if key in ("x", "y"):
            if key == "x":
                parts.append(f"at ({member['x']}, {member['y']})")
        elif key == "current_order":
            parts.append(value)
        elif key == "recent_attackers":
            parts.append("hit by " + ", ".join(f"{n} {v['seconds_ago']:g}s ago" for n, v in value.items()))
        else:
            parts.append(f"{key.replace('_', ' ')} {plain(value)}")
    return ", ".join(parts)


def one_line_members(groups):
    """Each group's members as one line per unit; the type's role, description and stats stay once per group."""
    for group in groups.values():
        group["members"] = {name: member_line(member) for name, member in group.get("members", {}).items()}
    return groups


def collapse(rows):
    """Identical consecutive event lines become one line with a count."""
    out = []
    for row in rows:
        base = out[-1].rsplit(" x", 1)[0] if out else None
        if out and (out[-1] == row or base == row):
            count = int(out[-1].rsplit(" x", 1)[1]) + 1 if out[-1] != row else 2
            out[-1] = f"{row} x{count}"
        else:
            out.append(row)
    return out


def buff_glossary(obs, catalog):
    """What each buff in view does, once per request: {'Purge': 'This unit is Purged; ...'}."""
    present = {b for u in obs["units"] + obs["visible_enemies"] for b in u.get("buffs", [])}
    return {
        catalog.buffs[b]["name"]: catalog.buffs[b]["description"]
        for b in sorted(present)
        if b in catalog.buffs and catalog.buffs[b]["description"] and not catalog.aura_buff(b)
    }


def unit_prompt(unit, catalog):
    """The unit type's prompt, without a hero's lines about skills it has not learned: a Tauren Chieftain
    at level 2 was told Reincarnation lets him fight at the very front, and died fighting at 10% health."""
    lines = UNIT_PROMPTS[unit["type_id"]].rstrip("\n").splitlines()
    if not unit["hero"]:
        return "\n".join(lines)
    learned = {a["ability_id"] for a in unit.get("abilities", ()) if a.get("level", 0) > 0}
    skills = catalog.units.get(unit["type_id"], {}).get("potential_hero_abilities") or ()
    names = {aid: catalog.ability(aid)["name"] for aid in skills}
    missing = [name for aid, name in names.items() if aid not in learned]
    have = [name for aid, name in names.items() if aid in learned]
    return "\n".join(line for line in lines if not any(n in line for n in missing) or any(n in line for n in have))


def build_request(obs, controlled_ids, instruction, *, memory, names, capabilities, catalog, model):
    """(request, menu). Jev decides for `controlled_ids`; the rest of our side is summarized."""
    own = troops(obs)
    history = memory.context(obs)
    names.remember(obs, capabilities)
    menu, questions = Menu(), {}
    controlled = [u for u in own if u["unit_id"] in controlled_ids]
    if not 1 <= len(controlled) <= 64:
        raise ValueError("Need 1..64 living controlled units")
    # Enemies that hit our casters, caster heroes or siege in the last 3 seconds: our fighters may peel them off.
    guarded = {u["unit_id"] for u in own if role(catalog, u["type_id"]) in ("caster", "caster hero", "siege")}
    threats = {}
    for e in history.get("recent_observed_events", []):
        if (
            e["kind"] == "attacked"
            and e["unit_id"] in guarded
            and e.get("attacker_id")
            and obs["game_time_seconds"] - e["observed_at"] <= 3
        ):
            threats.setdefault(e["attacker_id"], set()).add(e["unit_id"])
    for unit in controlled:
        uid = unit["unit_id"]
        cap = capabilities.get(str(uid), {})
        options = unit_candidates(
            unit,
            obs,
            cap,
            catalog,
            memory.dead_ends.get(uid, {}).get("targets", ()),
            memory.toggles.get(uid, {}),
            memory.forms.get(uid, {}),
            threats,
        )
        for option in options.values():
            if option.get("hits"):  # an enemy hitting our back line says whom
                option["meaning"] += f" It is hitting our {', '.join(sorted(names.name(v) for v in option['hits']))}."
            if option.get("then_label"):  # "Rejuvenation on priestessofthemoon1 (leaves Bear Form first)"
                spell, target, form = option["then_label"]
                on = f" on {names.name(target)}" if target else ""
                option["label"] = f"{spell}{on} (leaves {form} first)"
        if danger_seconds(unit, catalog) is not None:  # heroes and siege
            health = history.get("health", {}).get(uid, {})
            lost, over = health.get("recent_net_loss", (0, 0))
            rate = lost / over if over else 0
            # Only while still being hit: a unit that has stepped out gets its attacks back at once
            # (Night Elf heroes waited about 4 seconds out of the fight on an estimate from before they left).
            hit = health.get("seconds_since_hp_loss", float("inf")) < STILL_HIT
            dies_in = unit["hp"] / rate if rate > 0 and hit else None
            low = low_hero(unit, opponents(obs))
            options = escape_only(unit, options, 0.0 if low else dies_in, catalog)
            for key in ("back_off", "behind_line"):
                if key in options:  # the number the hero rule turns on, where the choice to step back is made
                    options[key]["meaning"] += (
                        f" At the rate it lost health over the last {over:g}s, this unit dies in about {dies_in:.0f} seconds."
                        if dies_in is not None
                        else f" It has {unit['hp'] / unit['max_hp']:.0%} health with the enemy army near."
                        if low
                        else " This unit is not losing health."
                    )
        options = {
            key: option
            for key, option in options.items()
            if (option["action"] or {}).get("command") != "buy"
            or obs["game_time_seconds"] - memory.buys.get((uid, option["action"]["arguments"]["item_type_id"]), -1e9)
            >= BUY_RETRY_SECONDS
        }
        menu.options[uid] = options
        menu.question_names[uid] = names.name(uid)
        labels, criteria = {}, {}
        for key, option in options.items():
            label = action_label(option["action"], names, option)
            if key == "keep" and option.get("target_id"):
                label = f"Keep attacking {names.name(option['target_id'])}"
            unique, variant = label, 1
            while unique in labels:
                variant += 1
                unique = f"{label} (alternative {variant})"
            labels[unique] = key
            command = (option["action"] or {}).get("command")
            criteria[unique] = (
                option.get("meaning")
                or {
                    None: "Continue useful action",
                    "move": "Move",
                    "smart": "Interact",
                    "harvest": "Gather",
                    "attack": "Attack",
                    "cast": "Cast",
                    "use_item": "Item",
                    "buy": "Buy item",
                    "stop": "Stop",
                }[command]
            )
        menu.choice_keys[uid] = labels
        question = HERO_QUESTION if unit["hero"] else UNIT_QUESTION
        instructions = question.rstrip("\n")
        if unit["type_id"] in UNIT_PROMPTS:
            instructions += "\n" + unit_prompt(unit, catalog)
        if any(
            order["kind"] == "cast"
            for ability in cap.get("abilities", [])
            if not (ability.get("interface_ability") or ability.get("item_ability") or ability.get("passive"))
            for order in ability.get("orders", [])
        ):
            instructions += "\n" + CASTER_RULE.rstrip("\n")
        questions[names.name(uid)] = choice_question(instructions, criteria)
    definitions = {u["type_id"]: catalog.unit(u["type_id"]) for u in own + opponents(obs)}
    matchups = catalog.attack_matchups(definitions)["multipliers"]
    # Render all sides with the same factual fields; only controlled units get questions and skill menus.
    yours = readable_units(controlled, obs, capabilities, catalog, names, history, matchups, controlled_ids)
    others = [u for u in own if u["unit_id"] not in controlled_ids]
    allies = readable_units(others, obs, capabilities, catalog, names, history, matchups, set())
    enemies = readable_units(opponents(obs), obs, {}, catalog, names, history, matchups, set())
    attach_unit_history(yours, enemies, history, names, controlled_ids)
    attach_unit_history(allies, {}, history, names, set())
    state = {
        "format": "readable-units-v7",
        "time_seconds": obs["game_time_seconds"],
        "objective": instruction.strip(),
        "game_knowledge": MICRO_RULES,
        "situation": fight_summary(catalog, own, opponents(obs)),
        "context": OBSERVATION_CONTEXT,
        "buffs": buff_glossary(obs, catalog),
        "you_control": {name: member for group in yours.values() for name, member in group["members"].items()},
        # One entry per controlled type: what it is, its base stats and what its skills do.
        "your_type": {name: {k: v for k, v in group.items() if k != "members"} for name, group in yours.items()},
        "your_side": one_line_members(allies),
        "enemies": one_line_members(enemies),
        "army_groups": {
            name: {
                "objective": group["instruction"],
                "center": group["center"],
                "destination": group.get("at"),
                "fight_on_the_way": bool(group.get("attack")),
                "members": [names.name(uid) for uid in sorted(group["ids"])],
            }
            for name, group in obs.get("army_groups", {}).items()
        },
    }
    if any(u["hero"] for u in controlled):
        state["resources"] = obs["player"]
        state["shop_availability"] = (
            "Listed items meet tech and initial stock timing requirements; remaining stock is unobserved."
        )
        state["shops"] = [
            {
                "name": names.name(shop["unit_id"]),
                "x": shop["x"],
                "y": shop["y"],
                "items": [
                    {k: item[k] for k in ("name", "gold", "lumber", "description")}
                    for item in available_items(catalog, shop, obs["game_time_seconds"], obs.get("tech", {}))
                    if army_item(catalog.item(item["type_id"]))
                ],
            }
            for shop in obs.get("shops", [])
        ]
    return {"model": model, "state": state, "questions": questions}, menu


def map_response(answers, menu):
    """(choices, selected): each unit's chosen option key, and the options with an action to send."""
    choices, selected = {}, []
    for uid, options in menu.options.items():
        returned = answers[menu.question_names[uid]]["choice"]
        choice = menu.choice_keys[uid][returned]
        choices[str(uid)] = choice
        if options[choice]["action"] is not None:
            selected.append(options[choice])
    return choices, selected
