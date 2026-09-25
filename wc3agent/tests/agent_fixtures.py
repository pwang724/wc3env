"""Synthetic worlds and model replies shared by agent tests."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from wc3agent.control import Control
from wc3agent.game.catalog import Catalog
from wc3agent.game.mapinfo import MapInfo
from wc3agent.macro.memory import MacroMemory
from wc3agent.scenarios.scenario import Scenario


def unit(name, **fields):
    base = dict(
        name=name, race="human", structure=False, hero=False, gold=0, lumber=0, food=0, food_made=0, build_seconds=10.0,
        hp=100, mana=0, armor=0.0, level=1, damage=None, requires=[], requires_by_count=None, trains=[], builds=[],
        researches=[], upgrades_to=[], sells_items=[], sells_units=[], base_attack_period=1.0, base_attack_type="normal",
        base_armor_class="medium", potential_hero_abilities=[], description="",
    )  # fmt: skip
    return {**base, **fields}


def catalog():
    units = {
        "hpea": unit("Peasant", gold=75, food=1, builds=["htow", "hhou", "hbar"], damage=[5.0, 6.0]),
        "htow": unit(
            "Town Hall",
            structure=True,
            classifications=["TownHall"],
            gold=385,
            lumber=205,
            trains=["hpea"],
            upgrades_to=["hkee"],
            food_made=12,
        ),
        "hkee": unit("Keep", structure=True, classifications=["TownHall"], gold=705, lumber=415, trains=["hpea"]),
        "hhou": unit("Farm", structure=True, gold=80, lumber=20, food_made=6),
        "hbar": unit("Barracks", structure=True, gold=160, lumber=60, trains=["hfoo", "hkni"], researches=["Rhde"]),
        "hfoo": unit("Footman", gold=135, food=2, damage=[12.0, 13.0], hp=420, armor=2.0),
        "hkni": unit("Knight", gold=245, lumber=60, food=4, requires=["hkee"], damage=[30.0, 38.0]),
        "ngol": unit("Gold Mine", race="other", structure=True),
        "hvlt": unit("Arcane Vault", structure=True, gold=130, lumber=30, sells_items=["stwp", "phea", "pnvl"]),
    }
    items = {
        "stwp": dict(name="Scroll of Town Portal", gold=350, lumber=0, usable=True, stock_start_seconds=440.0, stock_max=2, stock_seconds=120.0, requires=["hkee"], description="Teleports home."),
        "phea": dict(name="Potion of Healing", gold=150, lumber=0, usable=True, stock_start_seconds=440.0, stock_max=3, stock_seconds=120.0, requires=[], description="Heals 250."),
        "pnvl": dict(name="Lesser Clarity Potion", gold=70, lumber=0, usable=True, stock_start_seconds=0.0, stock_max=2, stock_seconds=30.0, requires=[], description="Mana over time."),
    }  # fmt: skip
    upgrades = {"Rhde": {"names": ["Defend"], "levels": [{"level": 1, "name": "Defend", "gold": 150, "lumber": 100, "seconds": 45.0, "requires": []}]}}  # fmt: skip
    return Catalog(
        dict(schema_version=1, units=units, abilities={}, items=items, upgrades=upgrades, damage_multipliers={},
             footprints={"htow": [512, 512], "hkee": [512, 512], "hhou": [128, 128], "hbar": [384, 384],
                         "halt": [320, 320], "ngol": [512, 512], "LTlt": [128, 128]})
    )  # fmt: skip


def own(unit_id, type_id, x=0.0, y=0.0, **fields):
    structure = type_id in ("htow", "hkee", "hbar", "hhou", "hvlt")
    base = dict(unit_id=unit_id, type_id=type_id, owner=0, x=x, y=y, hp=100, max_hp=100, mana=0, max_mana=0,
                structure=structure, hero=False, level=0, order=None)  # fmt: skip
    if type_id == "hpea":
        base["abilities"] = [{"ability_id": "Ahar", "level": 1}, {"ability_id": "Ahrp", "level": 1}]
    if structure:
        base.update(state=None, state_seconds=0.0, queue=[], queue_seconds=0.0)
    return {**base, **fields}


def observation(**fields):
    base = dict(
        game_time_seconds=30.0, player=dict(gold=200, lumber=50, food_used=5, food_cap=12), events=[], events_lost=0,
        players=[dict(id=0, kind="player", relation="self"), dict(id=1, kind="player", relation="enemy")],
        units=[own(1, "htow"), own(2, "hbar", queue=["hfoo"], queue_seconds=20.0), own(3, "hpea"),
               own(4, "hpea", order=dict(name="harvest", target_id=50, x=0, y=0))],
        visible_enemies=[dict(unit_id=50, type_id="ngol", owner=15, x=300.0, y=0.0, hp=1, max_hp=1, mana=0, max_mana=0,
                              structure=True, hero=False, level=0)],
        destructables=[dict(id=70, type_id="LTlt", x=-200.0, y=0.0, hp=50, resource="lumber", invulnerable=False)],
        inventory=[], items=[dict(item_id=90, type_id="phea", x=10.0, y=10.0)], result="",
    )  # fmt: skip
    return {**base, **fields}


def world():
    data = dict(schema_version=1, map="test.w3x", start_locations=[dict(player=0, x=0, y=0), dict(player=1, x=9000, y=0)],
                gold_mines=[], creep_camps=[], neutral_buildings=[])  # fmt: skip
    return MacroMemory(catalog(), MapInfo(data))


def control(groups=None, into=None):
    """A Control holding `groups`, changed the way macro's group and disband orders change it."""
    control = into or Control()
    for name in set(control.groups) - set(groups or {}):
        control.disband(name)
    for name, group in (groups or {}).items():
        control.delegate(name, set(group["ids"]), group["instruction"], group.get("at"))
    return control


def fighting_catalog():
    c = catalog()
    common = dict(
        attack_targets=["ground"],
        movement_type="foot",
        base_move_speed=270.0,
        base_attack_range=90.0,
        classifications=[],
    )
    for raw in ("hfoo", "hpea", "hkni"):
        c.units[raw].update(common)
    c.units["Hamg"] = unit("Archmage", hero=True, hp=450, damage=[21.0, 27.0], **{**common, "base_attack_range": 600.0})
    c.units["ogru"] = unit("Grunt", race="orc", hp=700, damage=[19.0, 22.0], **common)
    c.items = {
        "stwp": dict(name="Scroll of Town Portal", usable=True, perishable=True, target_form="unit", description="Teleports home.",
                     abilities=[dict(ability_id="AItp", targets=["structure"])], **{"class": "Purchasable"}),
        "tint": dict(name="Tome of Intelligence", usable=True, perishable=True, target_form=None, description="+1 Intelligence.",
                     abilities=[], **{"class": "PowerUp"}),
    }  # fmt: skip
    c.damage_multipliers = {"normal": {"medium": 1.0}}
    return c


def fight(hero_hp=450):
    hero = own(10, "Hamg", x=0.0, y=0.0, hero=True, level=2, hp=hero_hp, max_hp=450)
    footmen = [own(11 + i, "hfoo", x=200.0, y=60.0 * i, hp=420, max_hp=420) for i in range(2)]
    grunt = dict(
        unit_id=50,
        type_id="ogru",
        owner=1,
        x=300.0,
        y=0.0,
        hp=700,
        max_hp=700,
        mana=0,
        max_mana=0,
        structure=False,
        hero=False,
        level=0,
    )
    return observation(observer=0, units=[hero, *footmen], visible_enemies=[grunt], items=[], map={"bounds": {}},
                       inventory=[dict(unit_id=10, slot=0, type_id="stwp", charges=1)], home={"unit_id": 1})  # fmt: skip


def worker(uid=3, ability="Ahar", **fields):
    return own(uid, "hpea", abilities=[{"ability_id": ability}], **fields)


def state(now=1, **fields):
    return observation(observer=0, game_time_seconds=now, units=[own(1, "htow"), worker()], **fields)


def action(command, **args):
    return {"unit_id": 3, "command": command, "arguments": args}


def answer(prefix):
    def choose(request, key, record):
        record["response"] = {
            "answers": {
                name: {"choice": next(label for label in question["criteria"] if label.startswith(prefix))}
                for name, question in request["questions"].items()
            },
            "usage": {},
        }

    return choose


def scenario_fixture(**fields):
    definition = {"title": "Test", "goal": "Test objective", "minutes": 1, "setup": [], "checks": [], **fields}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / "test.json").write_text(json.dumps(definition), encoding="utf-8")
        with patch("wc3agent.scenarios.scenario.DEFINITIONS", path):
            return Scenario("test")
