"""Duels: two identical armies fight; does Jev beat Warcraft's own fighting, all else equal?

Runs alternate sides and nudge every unit by a jitter seeded by the run number, so run k starts
from the same positions with and without Jev. Duels run on a flat arena (tools/prepare/arena.py) so terrain cannot favour a side. Each race has one mirror army: two heroes, melee, ranged, casters, siege and more. Both copies are
staged GAP either side of the midpoint between the starts (the map is point-symmetric, so both
stand on mirrored ground), creeps in sight are removed, and heroes learn the same skills (the
code default) with every autocast switched on, both players own every upgrade of their race, and every unit starts with full mana. Both slots are computer slots without an AI, since
Warcraft has only computer players' units cast spells by themselves; runs alternate sides. The enemy always fights the way Warcraft does: every few seconds its units are told
to attack-move at our army and each picks its own targets. In the baseline our side does exactly the
same; with Jev, our side attack-moves until contact and then Jev fights (fusion.Fights: same
hand-over and resend rules). A duel is decided as soon as one side's strength falls below DECIDED_RATIO of the
other's: that side has lost. Jev's fight group gets the same ratio, so it never retreats first. Undecided after
DUEL_SECONDS is a draw.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from wc3env import GameConfig, GameSession, MatchSetup, PlayerConfig

from .config import PACKAGE_DIR, ROOT, environment
from .fusion import Fights
from .game.catalog import Catalog
from .game.facts import learned_skills
from .game.mapinfo import MapInfo
from .game.policies import skill_to_learn
from .game.strength import observed_strength
from .micro.agent import MicroAgent
from .play import RACES
from .recording import RunLog
from .replay import Recorder, render
from .report import report

# A flat, open copy of Echo Isles (tools/prepare/arena.py): no hills, trees, water, creeps or buildings.
MAP = ROOT / "build" / "maps" / "(2)FlatArena.w3x"
MAP_INFO = "(2)EchoIsles"  # the same start locations
STEP_MS = 250
FULL_MANA = 100000.0  # above any unit's maximum mana
REALTIME_POLL = 0.05  # realtime: seconds between observations when there is nothing to send
DUEL_SECONDS = 150.0
ENDED_SECONDS = 3.0  # a recorded win ends this long after the enemy's last unit died
DECIDED_RATIO = 0.4  # a duel is decided once one side's strength falls below this share of the other's (games retreat at policies.RETREAT_RATIO)
ORDER_SECONDS = 3.0  # how often an attack-moving side is re-aimed at the other army
GAP = 750.0  # each army stands this far from the map's midpoint, on the line between the starts
HERO_LEVELS = (5, 3)  # first and second hero

# (type, count); heroes first. Roughly 60 food each: a mid-game army with every role.
# Row depth behind the front line, facing the enemy; each row is spread sideways.
FRONT, HEROES, RANGED, CASTERS, SIEGE = 0.0, 150.0, 280.0, 420.0, 560.0
SPACING = 100.0  # sideways gap between units in a row
JITTER = 40.0  # each unit is nudged up to this far, seeded by the run: Warcraft vs Warcraft is deterministic,
# so without it every run from one side would be the same fight

# (type, count, row); heroes first. Roughly 60 food each: a mid-game army with every role.
ARMIES = {
    "human": [("Hamg", 1, HEROES), ("Hmkg", 1, HEROES), ("hfoo", 4, FRONT), ("hkni", 2, FRONT),
              ("hrif", 3, RANGED), ("hgyr", 2, RANGED), ("hmpr", 2, CASTERS), ("hsor", 2, CASTERS),
              ("hspt", 1, CASTERS), ("hmtm", 1, SIEGE)],
    "orc": [("Ofar", 1, HEROES), ("Otch", 1, HEROES), ("ogru", 4, FRONT), ("otau", 1, FRONT),
            ("orai", 2, FRONT), ("ohun", 3, RANGED), ("ospw", 1, RANGED), ("oshm", 2, CASTERS),
            ("odoc", 2, CASTERS), ("ocat", 1, SIEGE)],
    "undead": [("Udea", 1, HEROES), ("Ulic", 1, HEROES), ("ugho", 4, FRONT), ("uabo", 2, FRONT),
               ("ucry", 3, RANGED), ("ugar", 2, RANGED), ("unec", 2, CASTERS), ("uban", 2, CASTERS),
               ("umtw", 1, SIEGE)],
    "nightelf": [("Edem", 1, HEROES), ("Emoo", 1, HEROES), ("esen", 3, FRONT), ("edoc", 2, FRONT),
                 ("emtg", 1, FRONT), ("earc", 3, RANGED), ("ehip", 1, RANGED), ("edry", 2, CASTERS),
                 ("edot", 2, CASTERS), ("ebal", 1, SIEGE)],
}  # fmt: skip


def _toward(home, target, distance):
    gap = math.hypot(target["x"] - home["x"], target["y"] - home["y"]) or 1.0
    k = distance / gap
    return {"x": home["x"] + (target["x"] - home["x"]) * k, "y": home["y"] + (target["y"] - home["y"]) * k}


def _centre(units):
    return {"x": sum(u["x"] for u in units) / len(units), "y": sum(u["y"] for u in units) / len(units)}


def _alive(obs, ids):
    return [u for u in obs["units"] if u["unit_id"] in ids and u["hp"] > 0]


class Duel:
    def __init__(self, race, jev, catalog, mapinfo, session, micro, swapped=False, seed=0):
        """`swapped`: the armies trade places, so alternate runs cancel any edge one side of the map gives.
        `seed` picks the jitter; the same run number gives both modes the same starting positions."""
        self.jitter = random.Random(seed)
        self.race, self.jev, self.catalog, self.map, self.session = race, jev, catalog, mapinfo, session
        self.swapped = swapped
        self.micro = micro
        self.ids = {0: set(), 1: set()}
        self.start = {}
        self.last_order = {0: -1e9, 1: -1e9}
        self.tech = {}  # upgrade id -> level both players own
        self.learned = defaultdict(Counter)  # hero id -> skills this duel told it to learn

    def stage(self, observations):
        hall = next(u for u in observations[0]["units"] if u["structure"])
        home = self.map.home(hall)
        enemy_home = self.map.enemy_starts(home)[0]
        half = math.hypot(enemy_home["x"] - home["x"], enemy_home["y"] - home["y"]) / 2
        gap = -GAP if self.swapped else GAP
        # Unit vectors along the line between the starts and across it; each side faces the other.
        length = 2 * half
        along = ((enemy_home["x"] - home["x"]) / length, (enemy_home["y"] - home["y"]) / length)
        across = (-along[1], along[0])
        centre = _toward(home, enemy_home, half)
        places = {0: _toward(home, enemy_home, half - gap), 1: _toward(home, enemy_home, half + gap)}
        rows = {}
        for raw, n, depth in ARMIES[self.race]:
            rows.setdefault(depth, []).extend([raw] * n)
        # One nudge per slot, in the army's own frame (toward the enemy, sideways), so both armies stay mirror images.
        nudges = {
            (depth, k): (self.jitter.uniform(-JITTER, JITTER), self.jitter.uniform(-JITTER, JITTER))
            for depth, types in rows.items()
            for k in range(len(types))
        }
        for player, front in places.items():
            # +1 when the enemy lies further along the line, so rows extend back, away from it.
            facing = 1.0 if (centre["x"] - front["x"]) * along[0] + (centre["y"] - front["y"]) * along[1] > 0 else -1.0
            heroes = 0
            for depth, types in rows.items():
                for k, raw in enumerate(types):
                    ahead, sideways = nudges[(depth, k)]
                    back = depth - ahead
                    side = (k - (len(types) - 1) / 2) * SPACING + sideways
                    x = front["x"] - facing * back * along[0] + side * across[0]
                    y = front["y"] - facing * back * along[1] + side * across[1]
                    (uid,) = self.session.debug("spawn", type_id=raw, player=player, n=1, x=x, y=y)["unit_ids"]
                    self.ids[player].add(uid)
                    if self.catalog.units[raw].get("hero"):
                        self.session.debug("level", unit_id=uid, level=HERO_LEVELS[min(heroes, 1)])
                        heroes += 1
        self.session.debug("camera", **centre)
        self.research()
        # Spawned casters start with part of their mana (a Witch Doctor with 153 of 400, below one Healing Ward);
        # an army walking into a fight has full mana. The game caps the value at each unit's maximum.
        for player in (0, 1):
            for uid in self.ids[player]:
                self.session.debug("mana", unit_id=uid, value=FULL_MANA)

    def research(self):
        """Both players own every upgrade of their race at its highest level: weapons, armor and caster
        training, so every unit fights with its full spells."""
        self.tech = {
            raw: len(upgrade["levels"])
            for raw, upgrade in self.catalog.upgrades.items()
            if upgrade.get("race") == self.race
        }
        for player in (0, 1):
            for raw, level in self.tech.items():
                self.session.debug("research", player=player, type_id=raw, level=level)

    def clear_creeps(self, observations):
        """Remove every creep either army can see: Echo Isles has a camp at the midpoint, and it joined
        the fight against whichever army it aggroed, deciding the duel. Returns how many were removed."""
        creeps = {e["unit_id"] for obs in observations.values() for e in obs["visible_enemies"] if e["owner"] == 12}
        for uid in creeps:
            self.session.debug("remove", unit_id=uid)
        return len(creeps)

    def learn(self, observations):
        """Both sides spend every skill point the same way (policies.skill_to_learn)."""
        actions = {0: [], 1: []}
        for player, obs in observations.items():
            skills = learned_skills(self.catalog, obs)
            for hero in (u for u in obs["units"] if u["hero"] and u["unit_id"] in self.ids[player]):
                # In realtime the observation can trail the learn just sent; count what was sent too, or the
                # same skill is learned twice (a Lich ended with Dark Ritual 2 and Frost Nova 1).
                seen, sent = skills.get(hero["unit_id"], {}), self.learned[hero["unit_id"]]
                learned = {raw: max(seen.get(raw, 0), sent.get(raw, 0)) for raw in set(seen) | set(sent)}
                raw = skill_to_learn(self.catalog, hero, learned)
                if raw:
                    sent[raw] += 1
                    actions[player].append(
                        {"unit_id": hero["unit_id"], "command": "learn", "arguments": {"ability_id": raw}}
                    )
        return actions

    def autocast(self, observations):
        """Switch every autocast ability on for both sides. Warcraft turns them on only for computer
        slots, which gave the enemy free Heal, Slow and Curse while ours stayed off."""
        actions = {0: [], 1: []}
        for player, obs in observations.items():
            for unit in (u for u in obs["units"] if u["unit_id"] in self.ids[player] and u["hp"] > 0):
                for ability in unit.get("abilities", []):
                    definition = self.catalog.abilities.get(ability["ability_id"], {}).get("levels", {}).get("1", {})
                    for order in definition.get("orders", []):
                        if order["kind"] == "enable_autocast":
                            actions[player].append(
                                {"unit_id": unit["unit_id"], "command": "cast", "arguments": {"order": order["name"]}}
                            )
        return actions

    def attack_move(self, player, observations, now, only=None):
        """Warcraft's own fighting: re-aim this side's units at the other army every ORDER_SECONDS."""
        other = _alive(observations[1 - player], self.ids[1 - player])
        if not other or now - self.last_order[player] < ORDER_SECONDS:
            return []
        self.last_order[player] = now
        at = _centre(other)
        return [
            {"unit_id": u["unit_id"], "command": "attack", "arguments": at}
            for u in _alive(observations[player], self.ids[player])
            if only is None or u["unit_id"] in only
        ]

    def strength(self, obs, player):
        return observed_strength(_alive(obs, self.ids[player]), self.catalog)


def duel(race, jev, runs, out, hidden=True, speed=8.0, realtime=False, record=False, seconds=DUEL_SECONDS):
    """`runs` duels of one race in one game process (reset between them). Returns one summary per duel.
    `realtime`: the game runs on its own clock at normal speed and Jev answers while it runs, as in fusion.
    `record`: also film the game window and write replay.mp4 with Jev's choices beside it (needs visible). A
    recorded win plays on until ENDED_SECONDS after the enemy's last unit died, a loss stops when decided, and sides do not alternate,
    so our army is on the same side of every replay. Results are taken when the duel is decided either way.
    `seconds`: a duel undecided by then ends as a draw."""
    settings = environment()
    catalog = Catalog.load(PACKAGE_DIR / "game/data/reference.json")
    mapinfo = MapInfo.load(PACKAGE_DIR / "game/data/maps" / f"{MAP_INFO}.json")
    if not MAP.exists():
        raise FileNotFoundError(f"{MAP} is missing; build it with: python -m tools.prepare.arena")
    side = "jev" if jev else "warcraft"
    out = Path(out)
    session = GameSession(
        GameConfig(
            map=str(MAP),
            players=(PlayerConfig(0, race=RACES[race]), PlayerConfig(1, race=RACES[race])),
            mode="realtime" if realtime else "stepping",
            step_ms=STEP_MS,
            render=not hidden,
            background_visible=not hidden,
            computer_agents=(0, 1),  # both sides' units use spells on their own, as computer players' do
            setup=MatchSetup(seed=1),
            max_episodes_per_process=None,
            output_dir=out / f"{race}-{side}-env",
        )
    )
    results, recorded = [], []  # recorded: run folders to render once the game has closed
    try:
        for run in range(1, runs + 1):
            folder = out / f"{race}-{side}-{run}"
            folder.mkdir(parents=True, exist_ok=True)
            log = RunLog(folder, type("Config", (), {"race": race, "jev": jev, "run": run})(), None, None)
            log.summary["model"] = settings.get("TYPESAFE_DEFAULT_MODEL", "jev-latest") if jev else "none"
            micro = MicroAgent(
                catalog,
                model=settings.get("TYPESAFE_DEFAULT_MODEL", "jev-latest"),
                key=settings.get("TYPESAFE_API_KEY", "") if jev else "",
                loot_every_hero=True,
            )
            try:
                observations = session.reset()
                session.debug("speed", factor=1.0 if realtime else speed)
                game = Duel(race, jev, catalog, mapinfo, session, micro, swapped=run % 2 == 0 and not record, seed=run)
                game.stage(observations)
                observations = session.step({0: [], 1: []})[0]
                game.clear_creeps(observations)
                observations = session.step({0: [], 1: []})[0]
                for _ in range(8):  # skill points: at most five per hero, one learn a step
                    learns = game.learn(observations)
                    if not learns[0] and not learns[1]:
                        break
                    observations = session.step(learns)[0]
                autocasts = game.autocast(observations)
                micro.memory.record(observations[0]["game_time_seconds"], autocasts[0])
                observations = session.step(autocasts)[0]
                fights = Fights(catalog, micro, retreat_ratio=DECIDED_RATIO)
                fights.tech.update(game.tech)  # researched by staging, so no research events announce it
                start = observations[0]["game_time_seconds"]
                begin = {p: game.strength(observations[p], p) for p in (0, 1)}
                recorder = Recorder(folder) if record else None
                decided = None  # both sides' strength when the duel was decided
                wiped = None  # game time the enemy had no units left in play
                if recorder:
                    recorder.start()
                while observations[0]["game_time_seconds"] - start < seconds:
                    now = observations[0]["game_time_seconds"]
                    if recorder:
                        recorder.note(now)
                    strength = {p: game.strength(observations[p], p) for p in (0, 1)}
                    if decided is None and min(strength.values()) < DECIDED_RATIO * max(strength.values()):
                        decided = strength  # the weaker side would retreat now (the fusion rule), so it has lost
                    left = _alive(observations[1], game.ids[1])  # the enemy's duel army; its base and workers stay
                    if left:
                        wiped = None
                    elif wiped is None:
                        wiped = now
                    ended = wiped is not None and now - wiped >= ENDED_SECONDS
                    if decided and (not record or decided[0] < decided[1] or ended):
                        break  # a recorded win plays on until ENDED_SECONDS after the enemy's last unit; a loss stops here
                    ours, records = [], []
                    if jev:
                        ours, records = fights.act(observations[0], wait=not realtime)
                        log.calls(records)
                        fighting = fights.control.groups.get("fight", {}).get("ids", set())
                        ours += game.attack_move(0, observations, now, only=game.ids[0] - fighting)
                    else:
                        ours = game.attack_move(0, observations, now)
                    theirs = game.attack_move(1, observations, now)
                    if realtime and not ours and not theirs:
                        micro.ready.wait(REALTIME_POLL)  # a finished Jev call wakes this early
                        micro.ready.clear()
                    observations = session.step({0: ours, 1: theirs})[0]
                if recorder:
                    recorder.note(observations[0]["game_time_seconds"])
                    recorder.stop()
                if jev:
                    log.calls(micro.finish(observations[0]))
                end = decided or {p: game.strength(observations[p], p) for p in (0, 1)}
                kept = {p: round(100 * end[p] / begin[p]) if begin[p] else 0 for p in (0, 1)}
                log.summary.update(
                    race=race,
                    jev=jev,
                    run=run,
                    seconds=round(observations[0]["game_time_seconds"] - start, 1),
                    our_kept_percent=kept[0],
                    enemy_kept_percent=kept[1],
                    our_units_left=len(_alive(observations[0], game.ids[0])),
                    enemy_units_left=len(_alive(observations[1], game.ids[1])),
                    units_each=len(game.ids[0]),
                    result="win"
                    if end[1] < DECIDED_RATIO * end[0]
                    else "loss"
                    if end[0] < DECIDED_RATIO * end[1]
                    else "draw",
                    fights=fights.fights,
                    orders_resent=fights.resent,
                    idle_restarts=fights.idle_restarts,
                )
            except Exception as problem:
                log.summary.update(race=race, jev=jev, run=run, result="error", error=str(problem))
                raise
            finally:
                micro.close()
                log.close()
            if jev and (folder / "calls.jsonl").exists():
                report(folder)
                if record:
                    recorded.append(folder)
            print(
                f"{race} {side} {run}: {log.summary['result']} ours {log.summary.get('our_kept_percent')}% "
                f"theirs {log.summary.get('enemy_kept_percent')}%",
                flush=True,
            )
            results.append(dict(log.summary))
    finally:
        session.close()  # the game window closes before the replays render
    for folder in recorded:
        print(f"Replay: {render(folder, f'Jev · {race} mirror')}", flush=True)
    return results


def duels(races, runs, jobs, out=None, visible=False, realtime=False):
    """Every race both ways, `runs` duels each; writes duels.html linking each Jev duel's report."""
    out = Path(out or ROOT / "sessions" / f"duels-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
    out.mkdir(parents=True, exist_ok=True)
    work = [(race, jev) for race in races for jev in (False, True)]

    def one(item):
        race, jev = item
        try:
            return duel(race, jev, runs, out, hidden=not visible, realtime=realtime)
        except Exception as problem:  # noqa: BLE001  one broken race should not end the rest
            return [{"race": race, "jev": jev, "result": "error", "error": str(problem)}]

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = [r for batch in pool.map(one, work) for r in batch]
    (out / "duels.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (out / "duels.html").write_text(page(results, races), encoding="utf-8")
    print(f"Report: {out / 'duels.html'}", flush=True)
    return results


def page(results, races):
    """Per race: wins and average strength kept, Warcraft vs Jev, then every duel."""

    def mean(rows, key):
        values = [r[key] for r in rows if key in r]
        return f"{sum(values) / len(values):.0f}%" if values else "-"

    summary, detail = [], []
    for race in races:
        cells = [f"<td>{escape(race)}</td>"]
        for jev in (False, True):
            rows = [r for r in results if r.get("race") == race and r.get("jev") is jev]
            wins = sum(r["result"] == "win" for r in rows)
            cells += [f"<td>{wins}/{len(rows)}</td>", f"<td>{mean(rows, 'our_kept_percent')}</td>",
                      f"<td>{mean(rows, 'enemy_kept_percent')}</td>"]  # fmt: skip
        summary.append(f"<tr>{''.join(cells)}</tr>")
        for r in (r for r in results if r.get("race") == race):
            side = "jev" if r.get("jev") else "warcraft"
            link = f"{race}-{side}-{r.get('run')}/report.html"
            detail.append(
                f"<tr><td>{escape(race)}</td><td>{side}</td><td>{r.get('run', '-')}</td>"
                f"<td class={escape(r['result'])}>{escape(r['result'])}</td><td>{r.get('our_kept_percent', '-')}%</td>"
                f"<td>{r.get('enemy_kept_percent', '-')}%</td><td>{r.get('seconds', '-')}</td>"
                f"<td>{r.get('orders_resent', '-')}</td>"
                f"<td>{f'<a href={link}>Jev calls</a>' if r.get('jev') and r['result'] != 'error' else escape(r.get('error', ''))}</td></tr>"
            )
    armies = "".join(
        f"<li><b>{escape(race)}</b>: {escape(', '.join(f'{n}x {raw}' for raw, n, _ in ARMIES[race]))}</li>"
        for race in races
    )
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Mirror duels</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>
:root{{--bg:#fbfaf7;--fg:#1d1f23;--dim:#6b7280;--line:#e2e0da;--win:#2f6f4f;--loss:#b4412b}}
@media (prefers-color-scheme: dark){{:root{{--bg:#16161a;--fg:#e8e8e8;--dim:#9a9a9a;--line:#333;--win:#4cc26a;--loss:#ef6b6b}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:24px 16px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;margin:8px 0 24px}}
td,th{{border-bottom:1px solid var(--line);padding:5px 10px;text-align:right}} td:first-child,th:first-child{{text-align:left}}
.win{{color:var(--win);font-weight:600}} .loss{{color:var(--loss);font-weight:600}} .dim{{color:var(--dim)}} a{{color:inherit}}
</style></head><body>
<h1>Mirror duels: Warcraft's fighting vs Jev</h1>
<p class=dim>Identical armies. The enemy always attack-moves at our army and lets each unit pick targets.
Our side does the same ("Warcraft"), or Jev fights once in contact ("Jev"). A win means we kept a larger
share of our army's strength. Duels last up to {DUEL_SECONDS:.0f}s.</p>
<ul>{armies}</ul>
<div class=wrap><table><tr><th rowspan=2>race</th><th colspan=3>Warcraft</th><th colspan=3>Jev</th></tr>
<tr><th>wins</th><th>ours kept</th><th>theirs kept</th><th>wins</th><th>ours kept</th><th>theirs kept</th></tr>
{"".join(summary)}</table></div>
<div class=wrap><table><tr><th>race</th><th>side</th><th>run</th><th>result</th><th>ours kept</th><th>theirs kept</th>
<th>seconds</th><th>resent</th><th></th></tr>{"".join(detail)}</table></div></body></html>
"""
