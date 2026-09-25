"""One unit's menu: everything it could do right now, from the game's own rules.

Attacks on each enemy it can hit, geometric movement choices, ready spells on valid targets,
usable inventory items and shop purchases. Nothing here chooses a tactic for the model.
"""

import math

from ..game.actions import target_matches
from ..game.maneuvers import maneuvers, pickups
from ..game.policies import (
    BACK_LINE,
    COUPLE,
    ECONOMIC_ORDERS,
    NEARBY,
    SHOP_REACH,
    army_item,
    arrived,
    dead_end,
    far_target,
    keep_offered,
    pairing_partner,
    portal_home_offered,
    repeats_effect,
    spell_targets,
    step_back_offered,
    summons_unit,
    threatened,
    within_attack_reach,
)
from ..game.roles import role
from ..game.shops import available_items
from ..game.world import opponents, troops


def unit_candidates(unit, obs, capability, catalog, dead_ends=(), toggles=None, other_forms=None, threats=None):
    """`dead_ends`: points earlier moves from here failed to reach; moves there are not offered.
    `other_forms`: type id -> the abilities this unit had when last seen as that type (memory.forms).
    `threats`: enemy id -> our back-line units (casters, caster heroes, siege) it just hit."""
    uid = unit["unit_id"]
    own, enemy = troops(obs), opponents(obs)
    threats = threats or {}
    options = {"keep": {"action": None, "meaning": "Continue the current order."}}
    if unit["order"] is None:
        options["keep"].update(label="Wait for now", meaning="Stay idle.")

    def add(key, command, *, metadata=None, **args):
        current = unit["order"] or {}
        if (
            command in ("move", "attack")
            and "x" in args
            and current.get("name") == command
            and not current.get("target_id")
            and all(axis in current and math.isclose(current[axis], args[axis], abs_tol=1) for axis in ("x", "y"))
        ):
            return  # Keep continues this exact movement without restarting it.
        if (
            command == "attack"
            and current.get("name") == "attack"
            and current.get("target_id") == args.get("target_id")
        ):
            return  # Keep already attacks this target; a second option would split its vote.
        if command == "move" and dead_end(args, dead_ends):
            return
        options[key] = {"action": {"unit_id": uid, "command": command, "arguments": args}, **(metadata or {})}

    # No "Stop": it cancels a unit's attack and leaves it idle; Jev chose it 248-325 times per 5 duels.
    groups = obs.get("army_groups", {})
    assigned = {name: group for name, group in groups.items() if uid in group["ids"]}
    # The group's destination, while no enemy is within reach: mid-fight, walking off to it only loses damage.
    in_contact = any(math.hypot(e["x"] - unit["x"], e["y"] - unit["y"]) < NEARBY for e in enemy)
    for name, group in assigned.items():
        point = group.get("at")
        if not point or in_contact or arrived(unit, point, obs, catalog):
            continue
        attack = bool(group.get("attack"))
        add(
            f"objective_{name}",
            "attack" if attack else "move",
            x=point["x"],
            y=point["y"],
            metadata={
                "label": f"{'Attack-move' if attack else 'Move'} to {name}'s destination",
                "meaning": "Go to the group's destination, fighting enemies met on the way."
                if attack
                else "Walk to the group's destination without attacking.",
            },
        )
    home = obs.get("home") or {}
    if "x" in home and not arrived(unit, home, obs, catalog):
        add(
            "recover_home",
            "move",
            x=home["x"],
            y=home["y"],
            metadata={
                "label": "Move toward our base",
                "meaning": "Walk to our town hall; it does not heal.",
            },
        )
    if unit["hero"]:
        carried = sum(entry["unit_id"] == uid for entry in obs["inventory"])
        for shop in obs.get("shops", []):
            items = [
                item
                for item in available_items(catalog, shop, obs["game_time_seconds"], obs.get("tech", {}))
                if army_item(catalog.item(item["type_id"]))
            ]
            if not items:
                continue
            distance = math.hypot(shop["x"] - unit["x"], shop["y"] - unit["y"])
            if (
                distance > SHOP_REACH
            ):  # Move close before attempting a purchase; actual stock/range are checked by the game.
                add(
                    f"visit_shop_{shop['unit_id']}",
                    "move",
                    x=shop["x"],
                    y=shop["y"],
                    metadata={
                        "label": f"Visit {catalog.name(shop['type_id'])} for supplies",
                        "meaning": "Move next to the shop, then buy and use healing or mana recovery if needed. Do not walk through danger.",
                    },
                )
            elif carried < 6:
                for item in items:
                    if item["gold"] <= obs["player"]["gold"] and item["lumber"] <= obs["player"]["lumber"]:
                        already_carried = sum(
                            entry["unit_id"] == uid and entry["type_id"] == item["type_id"]
                            for entry in obs["inventory"]
                        )
                        add(
                            f"buy_{shop['unit_id']}_{item['type_id']}",
                            "buy",
                            shop_id=shop["unit_id"],
                            item_type_id=item["type_id"],
                            metadata={
                                "item_name": item["name"],
                                "meaning": f"Purchase one for {item['gold']} gold, {item['lumber']} lumber; already carrying {already_carried}. Buying adds it to inventory and does not activate its effect. Use a carried copy when its effect is needed now. {item.get('description', '')} Stock remaining is unobserved; confirm inventory after buying.",
                            },
                        )
    attack_targets = catalog.unit(unit["type_id"])["attack_targets"]
    current = (unit["order"] or {}).get("target_id")
    reachable, raid = [], []
    for target in enemy:
        definition = catalog.unit(target["type_id"])
        medium = "air" if definition["movement_type"] == "fly" else "ground"
        # Only enemies the unit can hit after a short step: a named target farther away makes it chase through
        # the enemy army instead of hitting what is in front of it (traced duels: 20 melee attacks against 106).
        if medium in attack_targets and (within_attack_reach(unit, target, catalog) or target["unit_id"] == current):
            reachable.append(target)
        elif medium in attack_targets and target["hp"] > 0 and far_target(unit, target, catalog, threats):
            raid.append(target)  # peel off our back line, or reach the enemy back line (policies.far_target)

    def target_facts(target):
        """What decides whether to hit it: its role (back line or not), health, reach, distance, who is on it."""
        reach = catalog.unit(target["type_id"]).get("base_attack_range", 0)
        kind = role(catalog, target["type_id"])
        on_it = sum((u["order"] or {}).get("target_id") == target["unit_id"] for u in own if u is not unit)
        return ", ".join([
            f"enemy {kind}" + (" (back line)" if kind in BACK_LINE else ""),
            f"{target['hp']}/{target['max_hp']} hp",
            f"{'ranged' if reach >= 300 else 'melee'} (attack range {reach:.0f})",
            f"{math.hypot(target['x'] - unit['x'], target['y'] - unit['y']):.0f} away",
            f"{on_it} of ours attacking it",
        ])  # fmt: skip

    attacking = next((e for e in enemy if e["unit_id"] == current and e["hp"] > 0), None)
    for target in reachable + raid:
        switch = (
            f" Switches away from {catalog.name(attacking['type_id'])} ({attacking['hp']}/{attacking['max_hp']} hp),"
            " losing the attacks already spent on it."
            if attacking
            else ""
        )
        add(
            f"attack_{target['unit_id']}",
            "attack",
            target_id=target["unit_id"],
            metadata={
                "meaning": f"Attack it: {target_facts(target)}.{switch}",
                "hits": threats.get(target["unit_id"], ()),
            },
        )
    if reachable:
        # "Fight here": attack-move at the nearby enemies, hitting whatever is in range (Warcraft's own default).
        centre = {axis: sum(t[axis] for t in reachable) / len(reachable) for axis in ("x", "y")}
        add(
            "fight_here",
            "attack",
            x=round(centre["x"], 1),
            y=round(centre["y"], 1),
            metadata={
                "label": "Fight here",
                "meaning": "Attack-move into the nearby enemies, hitting whatever comes in range.",
            },
        )
    if "keep" in options and not keep_offered(unit, enemy, catalog):
        del options["keep"]
    elif (unit["order"] or {}).get("name") == "attack" and (unit["order"] or {}).get("target_id"):
        options["keep"]["target_id"] = unit["order"]["target_id"]
        target = next((e for e in enemy if e["unit_id"] == unit["order"]["target_id"]), None)
        if target:  # the same facts as the alternatives, so staying on a front-line target is a real comparison
            options["keep"]["meaning"] = f"Keep attacking it: {target_facts(target)}."
            options["keep"]["hits"] = threats.get(target["unit_id"], ())
    bounds = obs.get("map", {}).get("bounds", {})
    nearby_own = [u for u in own if math.hypot(u["x"] - unit["x"], u["y"] - unit["y"]) < NEARBY]
    nearby_enemy = [u for u in enemy if math.hypot(u["x"] - unit["x"], u["y"] - unit["y"]) < NEARBY]
    for key, move in maneuvers(unit, nearby_own, nearby_enemy, catalog).items():
        if key == "behind_line" and not threatened(unit, nearby_enemy, catalog):
            continue  # repositioning when nothing can hit the unit only costs its attacks and casts
        if key in ("back_off", "behind_line") and not step_back_offered(unit, nearby_enemy, catalog):
            continue
        x = max(bounds.get("min_x", -math.inf), min(bounds.get("max_x", math.inf), move["x"]))
        y = max(bounds.get("min_y", -math.inf), min(bounds.get("max_y", math.inf), move["y"]))
        add(key, "move", metadata={"label": move["label"], "meaning": move["meaning"]}, x=x, y=y)

    casting = {((u["order"] or {}).get("name"), (u["order"] or {}).get("target_id")) for u in own if u is not unit}

    def targets_for(form, ability):
        if form == "none":
            return [("", {})]
        targets = [v for v in own + enemy if target_matches(unit, v, ability, catalog)]
        if form == "unit":
            targets = [v for v in targets if not repeats_effect(v, ability, casting)]
            targets = spell_targets(unit, ability, targets, catalog)
            return [(f"_{v['unit_id']}", {"target_id": v["unit_id"]}) for v in targets]
        if form == "point":
            return [(f"_at_{v['unit_id']}", {"x": v["x"], "y": v["y"]}) for v in targets]
        return []

    for ability in capability.get("abilities", []):
        if ability.get("interface_ability") or ability.get("item_ability"):
            continue
        for order in ability["orders"]:
            if order["name"] in ECONOMIC_ORDERS or not order["order_id"] or not order["target_form"]:
                continue
            # Toggling autocast is a command, not an immediate mana-consuming cast.
            if ability["missing_requirements"] or (order["kind"] == "cast" and not ability["ready"]):
                continue
            if order["kind"] == "cast" and not nearby_enemy and summons_unit(catalog, ability["ability_id"]):
                continue
            if order["name"] == COUPLE and not pairing_partner(unit, own, catalog):
                continue  # Mount Hippogryph / Pick up Archer need a free partner of ours nearby
            # Offer only the toggle that changes something; a state never set by us stays open both ways.
            spell = next((o["name"] for o in ability["orders"] if o["kind"] == "cast"), None)
            state = (toggles or {}).get(spell, {})
            toggle = any(o["kind"] == "deactivate" for o in ability["orders"])
            if (order["kind"] == "enable_autocast" and state.get("autocast") is True) or (
                order["kind"] == "disable_autocast" and state.get("autocast") is False
            ):
                continue
            if toggle and (
                (order["kind"] == "cast" and state.get("active"))
                or (order["kind"] == "deactivate" and not state.get("active"))
            ):
                continue
            for suffix, args in targets_for(order["target_form"], ability):
                meaning = f"{ability['name']}: {order['kind']}"
                tags = set(ability.get("targets", []))
                # Not aimed at enemies: friendly spells (Roar), spells on corpses (Raise Dead), and spells naming no
                # targets at all, such as summons and illusions (Feral Spirit's area is where the wolves appear; a
                # "nobody in reach" rule on it kept the Far Seer from summoning in 405 of 424 ready decisions).
                friendly = (
                    not tags
                    or "dead" in tags
                    or not {"enemy", "neutral"} & tags and {"friend", "ally", "player", "self"} & tags
                )
                if order["kind"] == "cast" and order["target_form"] != "unit" and ability.get("area") and not friendly:
                    # How many enemies an area spell would hit now: around the caster (War Stomp) or the point.
                    x, y = args.get("x", unit["x"]), args.get("y", unit["y"])
                    hit = sum(
                        1
                        for e in enemy
                        if e["hp"] > 0 and not e["structure"] and math.hypot(e["x"] - x, e["y"] - y) <= ability["area"]
                    )
                    meaning += f"; {hit} {'enemy' if hit == 1 else 'enemies'} within its {ability['area']:g} area now"
                    if order["target_form"] == "none":
                        # Cast around the caster (War Stomp): offer the step to where it would hit the most.
                        spots = [
                            (
                                sum(
                                    1
                                    for o in nearby_enemy
                                    if o["hp"] > 0
                                    and not o["structure"]
                                    and math.hypot(o["x"] - e["x"], o["y"] - e["y"]) <= ability["area"]
                                ),
                                e,
                            )
                            for e in nearby_enemy
                            if e["hp"] > 0 and not e["structure"]
                        ]
                        best, spot = max(spots, key=lambda t: t[0], default=(0, None))
                        if best >= 3 and best > hit:
                            add(
                                f"clump_{ability['ability_id']}",
                                "move",
                                x=round(spot["x"], 1),
                                y=round(spot["y"], 1),
                                metadata={
                                    "label": f"Step into the enemy clump for {ability['name']}",
                                    "meaning": f"Walk into the enemies there, where {best} would be within its {ability['area']:g} area, then cast it.",
                                },
                            )
                        if hit == 0:
                            continue  # War Stomp or Taunt with nobody in reach only spends the cooldown
                add(
                    f"{order['name']}_{ability['ability_id']}{suffix}",
                    "cast",
                    order=order["name"],
                    metadata={
                        "ability_id": ability["ability_id"],
                        "order_kind": order["kind"],
                        "meaning": meaning,
                    },
                    **args,
                )
    for entry in obs["inventory"]:
        if entry["unit_id"] != uid:
            continue
        item = catalog.item(entry["type_id"])
        if (
            not army_item(item)
            or not item["usable"]
            or not item["target_form"]
            or (item["perishable"] and entry["charges"] <= 0)
        ):
            continue
        live_item_abilities = [
            a
            for a in capability.get("abilities", [])
            if a["ability_id"] in {v["ability_id"] for v in item["abilities"]}
        ]
        if any(not a["ready"] for a in live_item_abilities):
            continue
        ability = item["abilities"][0]
        aims = targets_for(item["target_form"], ability)
        if item["target_form"] == "unit" and "structure" in ability["targets"]:
            hall = (obs.get("home") or {}).get("unit_id")
            aims = [("_home", {"target_id": hall})] if hall and portal_home_offered(obs) else []
        for suffix, args in aims:
            home = (
                {"label": f"Use {item['name']}: teleport yourself and nearby troops to base"}
                if suffix == "_home"
                else {}
            )
            add(
                f"item_slot_{entry['slot']}{suffix}",
                "use_item",
                slot=entry["slot"],
                metadata={
                    "item_type": entry["type_id"],
                    "item_name": item["name"],
                    "meaning": f"Activate it now. {item.get('description', '')}".strip(),
                    **home,
                },
                **args,
            )
    # Spells of the unit's other form (a bear's Rejuvenation): leave this form, then cast once changed back.
    for form in capability.get("abilities", []):
        effects = form.get("effects", {})
        back = next((o for o in form.get("orders", []) if o["kind"] == "deactivate"), None)
        if not back or effects.get("UnitID") != unit["type_id"] or not (effects.get("DataA") or "").strip():
            continue
        original = effects["DataA"].strip()
        for ability in (other_forms or {}).get(original, []):
            if any(ability.get(k) for k in ("interface_ability", "item_ability", "passive", "missing_requirements")):
                continue
            if any(o["kind"] == "deactivate" for o in ability["orders"]):
                continue  # a toggle, such as the form itself
            cast = next(
                (o for o in ability["orders"] if o["kind"] == "cast" and o["order_id"] and o["target_form"]), None
            )
            ready_in = ability["cooldown_remaining"] - (obs["game_time_seconds"] - ability["observed_at"])
            if not cast or unit["mana"] < ability["mana_cost"] or ready_in > 0:
                continue
            for suffix, args in targets_for(cast["target_form"], ability):
                add(
                    f"via_{back['name']}_{ability['ability_id']}{suffix}",
                    "cast",
                    order=back["name"],
                    metadata={
                        "meaning": f"Leave {form['name']}, then cast {ability['name']}. {ability.get('description', '')}".strip(),
                        "then": {"command": "cast", "arguments": {"order": cast["name"], **args}},
                        "then_type": original,
                        "then_label": (ability["name"], args.get("target_id"), form["name"]),
                    },
                )
    for pickup in pickups(unit, obs, catalog):
        add(
            f"pickup_{pickup['item_id']}",
            "smart",
            target_id=pickup["item_id"],
            metadata={
                "label": pickup["label"],
                "meaning": pickup["meaning"],
            },
        )
    return options
