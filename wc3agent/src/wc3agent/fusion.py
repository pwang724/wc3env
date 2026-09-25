"""Fusion: Warcraft's melee AI plays our side, and Jev fights.

The AI decides the economy, buildings, heroes, skills and where the army goes. When army units come
within FIGHT_RANGE of hostile units, code hands them to Jev as the "fight" group; Jev picks every
unit's action about once a second. The AI's own group controller reissues its orders within about
three seconds, so code resends Jev's latest attack or move for a unit once the AI has replaced it.
The fight ends after CALM_SECONDS without hostiles near, or turns into a RETREAT_SECONDS retreat
when our strength falls below RETREAT_RATIO of the enemy's; then the AI takes the units back.
With `jev=False` nothing is handed over: the same AI plays alone, the baseline for comparison.
"""

from __future__ import annotations

import math
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from wc3env import GameConfig, GameSession, MatchSetup, PlayerConfig

from .config import PACKAGE_DIR, ROOT, environment
from .control import Control
from .game.catalog import Catalog
from .game.policies import (
    CALM_SECONDS,
    DELIBERATE_HOLD,
    RETREAT_AFTER,
    RETREAT_RATIO,
    RETREAT_SECONDS,
    hostiles,
    in_fight,
    losing,
    overridden,
)
from .game.world import town_hall
from .micro.agent import MicroAgent
from .micro.prompts import FIGHT_OBJECTIVE, RETREAT_OBJECTIVE
from .play import DIFFICULTIES, OVERLAY_X, OVERLAY_Y, RACES
from .recording import RunLog

STEP_MS = 250  # stepped play: game time per step, so resent orders lag the AI by at most this
OBSERVE_SECONDS = 0.05  # realtime pause when there is nothing to send
FIGHT = "fight"
MAX_FAILURES = 5  # Jev calls failing in a row (no credits, bad key, outage) stop the run


@dataclass(frozen=True)
class FusionConfig:
    map: str = "(2)EchoIsles.w3x"
    race: str = "human"
    opponent_race: str | None = None  # None: the map's choice
    difficulty: str = "insane"  # for both AIs
    jev: bool = True
    seed: int | None = None
    max_game_minutes: float = 30.0
    speed: float = 1.0
    hidden: bool = False
    realtime: bool = False
    debug: bool = False  # print Jev's choices live, in color, in the terminal and on the game screen
    out: Path | None = None

    def __post_init__(self):
        for race in (self.race, self.opponent_race or "human"):
            if race not in RACES:
                raise ValueError(f"Unknown race: {race}")
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(f"Unknown difficulty: {self.difficulty}")
        if not math.isfinite(self.max_game_minutes) or self.max_game_minutes <= 0:
            raise ValueError("max_game_minutes must be a positive number")


class Fights:
    """Who Jev fights with, and the orders code resends while the AI tries to take them back."""

    def __init__(self, catalog, micro, retreat_ratio=RETREAT_RATIO):
        """`retreat_ratio`: below this share of the enemy's strength the group retreats (duels pass their lower
        decision ratio, so a duel is decided before any retreat)."""
        self.catalog, self.micro, self.retreat_ratio = catalog, micro, retreat_ratio
        self.control = Control()
        self.intended = {}  # unit id -> Jev's latest attack or move for it
        self.calm_since = self.started = self.retreat_until = None
        self.tech = {}
        self.fights = self.retreats = self.resent = 0
        self.failures = 0  # Jev calls failed in a row
        self.idle_restarts = 0  # idle fight members code sent back into the fight
        self.hold_until = {}  # unit id -> game time until which an idle unit is left as Jev deliberately placed it

    def act(self, obs, wait):
        now = obs["game_time_seconds"]
        for event in obs["events"]:
            if event["kind"] == "research_finish":
                self.tech[event["type_id"]] = self.tech.get(event["type_id"], 0) + 1
        self._update_group(obs, now)
        hall = town_hall(obs, self.catalog)
        actions, records = self.micro.act(obs, self.control, hall and hall["unit_id"], self.tech, wait=wait)
        for record in records:
            for uid, key in (record.get("choices") or {}).items():
                # Jev repositioned this unit: leave it a moment rather than undo that. Keeping or waiting while idle
                # is no reason to stand out of the fight (idle casters 18% of the time when that was held too).
                if key in ("back_off", "behind_line", "close_in", "recover_home") or key.startswith(
                    ("objective_", "visit_shop")
                ):
                    self.hold_until[int(uid)] = now + DELIBERATE_HOLD
            self.failures = self.failures + 1 if "error" in record else 0
            if self.failures >= MAX_FAILURES:
                # Without answers nobody commands the fight; a run like that would be scored as Jev's.
                raise RuntimeError(f"Jev failed {self.failures} calls in a row: {record['error']}")
        for action in actions:
            if action["command"] in ("attack", "move"):
                self.intended[action["unit_id"]] = action
            else:
                self.intended.pop(action["unit_id"], None)
        members = self.control.groups.get(FIGHT, {}).get("ids", set())
        sent = {a["unit_id"] for a in actions}
        for unit in obs["units"]:
            action = self.intended.get(unit["unit_id"])
            collecting = unit["unit_id"] in self.micro.loot_sent  # a pickup is not the AI taking over
            if (
                unit["unit_id"] in members
                and unit["unit_id"] not in sent
                and not collecting
                and action
                and overridden(action, unit, obs)
            ):
                actions.append(action)
                self.resent += 1
        actions += self._no_idle_fighters(obs, members, {a["unit_id"] for a in actions})
        self.micro.memory.record(now, actions)
        return actions, records

    def _no_idle_fighters(self, obs, members, sent):
        """A fight member with no order stands still until Jev answers again; send it into the nearby enemies on
        attack-move, Warcraft's own default (traced duels: idle casters 33% of the time, far fewer attacks)."""
        enemies = [e for e in hostiles(obs)]
        orders = []
        for unit in obs["units"]:
            uid = unit["unit_id"]
            if uid not in members or uid in sent or unit["order"] or uid in self.micro.loot_sent or not enemies:
                continue
            if self.hold_until.get(uid, -1) > obs["game_time_seconds"]:
                continue
            if self.catalog.units.get(unit["type_id"], {}).get("base_move_speed", 0) <= 0:
                continue
            near = sorted(enemies, key=lambda e: math.hypot(e["x"] - unit["x"], e["y"] - unit["y"]))[:5]
            at = {
                "x": round(sum(e["x"] for e in near) / len(near), 1),
                "y": round(sum(e["y"] for e in near) / len(near), 1),
            }
            orders.append({"unit_id": uid, "command": "attack", "arguments": at})
        self.idle_restarts += len(orders)
        return orders

    def _update_group(self, obs, now):
        group = self.control.groups.get(FIGHT)
        members = group["ids"] if group else set()
        if group and group["instruction"] == RETREAT_OBJECTIVE:
            if now >= self.retreat_until:
                self._release()
            return
        ids, enemies = in_fight(obs, self.catalog, members)
        if not group:
            if ids:
                self.control.delegate(FIGHT, ids, FIGHT_OBJECTIVE, None)
                self.started, self.calm_since = now, None
                self.fights += 1
            return
        if not enemies:
            self.calm_since = self.calm_since if self.calm_since is not None else now
            if now - self.calm_since >= CALM_SECONDS:
                self._release()
            return
        self.calm_since = None
        if ids != members:
            self.control.delegate(FIGHT, ids, FIGHT_OBJECTIVE, None)
        if now - self.started >= RETREAT_AFTER and losing(obs, self.catalog, ids, enemies, self.retreat_ratio):
            hall = town_hall(obs, self.catalog)
            home = {"x": hall["x"], "y": hall["y"]} if hall else None
            self.control.delegate(FIGHT, ids, RETREAT_OBJECTIVE, home)
            self.retreat_until = now + RETREAT_SECONDS
            self.retreats += 1

    def _release(self):
        self.control.disband(FIGHT)
        self.intended.clear()
        self.calm_since = self.started = self.retreat_until = None


def fuse(config: FusionConfig):
    settings = environment()
    label = "fusion" if config.jev else "ai-only"
    out = config.out or ROOT / "sessions" / f"{label}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    catalog = Catalog.load(PACKAGE_DIR / "game/data/reference.json")
    log = RunLog(out, config, None, None, debug=config.debug)
    log.summary["model"] = settings.get("TYPESAFE_DEFAULT_MODEL", "jev-latest") if config.jev else "none"
    opponent = PlayerConfig(1, race=RACES[config.opponent_race] if config.opponent_race else None, control="computer")
    with ExitStack() as resources:
        resources.callback(log.close)
        session = GameSession(
            GameConfig(
                map=config.map,
                players=(PlayerConfig(0, race=RACES[config.race]), opponent),
                mode="realtime" if config.realtime else "stepping",
                step_ms=STEP_MS,
                window_mode="background",
                render=not config.hidden,
                background_visible=not config.hidden,
                ai_difficulty=DIFFICULTIES[config.difficulty],
                ai_agents=(0,),
                setup=MatchSetup(seed=config.seed),
                max_episodes_per_process=None,
                output_dir=out / "env",
            )
        )
        resources.callback(session.close)
        micro = MicroAgent(
            catalog,
            model=settings.get("TYPESAFE_DEFAULT_MODEL", "jev-latest"),
            key=settings.get("TYPESAFE_API_KEY", "") if config.jev else "",
            loot_every_hero=True,
        )
        resources.callback(micro.close)
        fights = Fights(catalog, micro)
        lost = killed = 0
        try:
            obs = session.reset()[0]
            session.debug("speed", factor=config.speed)
            limit = config.max_game_minutes * 60
            while not obs["result"] and obs["game_time_seconds"] < limit:
                actions, records = fights.act(obs, wait=not config.realtime) if config.jev else ([], [])
                log.calls(records)
                if log.console and (panel := log.console.panel()):
                    session.debug("overlay", panel=panel, x=OVERLAY_X, y=OVERLAY_Y, seconds=3600)
                if config.realtime and not actions:
                    micro.ready.wait(OBSERVE_SECONDS)
                    micro.ready.clear()
                submitted_at = obs["game_time_seconds"]
                observations, _, info = session.step({0: actions})
                if actions:
                    log.submitted(actions, info["rejected"][0], submitted_at)
                obs = observations[0]
                for event in obs["events"]:
                    if event["kind"] == "death":
                        lost += event.get("owner") == 0
                        killed += event.get("owner") not in (0, None)
            if config.jev:
                log.calls(micro.finish(obs))
            log.summary.update(
                result=obs["result"] or "time_limit",
                game_seconds=obs["game_time_seconds"],
                seed=config.seed,
                jev=config.jev,
                fights=fights.fights,
                retreats=fights.retreats,
                orders_resent=fights.resent,
                units_lost=lost,
                enemies_killed=killed,
                score=obs.get("score", {}),
            )
            try:
                session.save_replay(out / "replay.w3g")
            except Exception as problem:  # noqa: BLE001  a replay is a convenience
                log.summary["replay_error"] = str(problem)
        except Exception as problem:
            log.summary.update(result="error", error=str(problem))
            raise
    print(
        f"{label}: {log.summary['result']} after {log.summary.get('game_seconds', 0):.0f}s; session in {out}",
        flush=True,
    )
    return log.summary


def compare(games, jobs, out, visible=False, **options):
    """Play `games` seeds twice each, AI alone and AI with Jev; write comparison.txt, a report for
    each Jev game, and comparison.html linking them. `visible` shows every game window."""
    from concurrent.futures import ThreadPoolExecutor

    from .report import report

    out = out or ROOT / "sessions" / f"compare-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    runs = [
        FusionConfig(seed=seed, jev=jev, hidden=not visible, out=out / f"{'jev' if jev else 'ai'}-{seed}", **options)
        for seed in range(1, games + 1)
        for jev in (False, True)
    ]

    def run(config):
        try:
            return fuse(config)
        except Exception as problem:  # noqa: BLE001  one broken game should not end the comparison
            return {"result": "error", "error": str(problem), "jev": config.jev, "seed": config.seed}

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run, runs))
    lines = [f"{'side':6} {'seed':>4} {'result':10} {'minutes':>7} {'lost':>5} {'killed':>6} {'fights':>6}"]
    for side in (False, True):
        rows = [r for r in results if r.get("jev") is side]
        for r in rows:
            lines.append(
                f"{'jev' if side else 'ai':6} {r.get('seed', 0):>4} {r['result']:10} "
                f"{r.get('game_seconds', 0) / 60:7.1f} {r.get('units_lost', 0):>5} "
                f"{r.get('enemies_killed', 0):>6} {r.get('fights', 0):>6}"
            )
        wins = sum(r["result"] == "victory" for r in rows)
        lines.append(f"{'jev' if side else 'ai':6} wins {wins}/{len(rows)}")
    lines.append(f"wall time {(time.monotonic() - started) / 60:.1f} min")
    text = "\n".join(lines)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.txt").write_text(text + "\n", encoding="utf-8")
    for config in runs:
        if config.jev and (config.out / "calls.jsonl").exists():
            report(config.out)
    (out / "comparison.html").write_text(comparison_page(results, runs, out), encoding="utf-8")
    print(text, flush=True)
    print(f"Report: {out / 'comparison.html'}", flush=True)
    return results


def comparison_page(results, runs, out):
    """One table: each seed's AI-alone and AI-with-Jev game side by side, linking Jev's call reports."""
    from html import escape

    by = {(r.get("seed"), r.get("jev")): r for r in results}

    def cells(r):
        values = (
            f"{r.get('game_seconds', 0) / 60:.1f}",
            r.get("units_lost", "-"),
            r.get("enemies_killed", "-"),
            r.get("fights", "-"),
            r.get("orders_resent", "-"),
        )
        return f"<td class={escape(r['result'])}>{escape(r['result'])}</td>" + "".join(f"<td>{v}</td>" for v in values)

    rows = []
    for seed in sorted({c.seed for c in runs}):
        ai, jev = by.get((seed, False), {"result": "missing"}), by.get((seed, True), {"result": "missing"})
        link = f"jev-{seed}/report.html"
        anchor = f"<a href={link}>Jev calls</a>" if (out / link).exists() else ""
        rows.append(f"<tr><td>{seed}</td>{cells(ai)}{cells(jev)}<td>{anchor}</td></tr>")
    wins = {side: sum(r["result"] == "victory" for r in results if r.get("jev") is side) for side in (False, True)}
    head = "<th>result</th><th>minutes</th><th>lost</th><th>killed</th><th>fights</th><th>resent</th>"
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Jev vs AI</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--line:#ddd;--win:#1a7f37;--loss:#c62828}}
@media (prefers-color-scheme: dark){{:root{{--bg:#16161a;--fg:#e8e8e8;--line:#333;--win:#4cc26a;--loss:#ef6b6b}}}}
body{{background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif;margin:24px 16px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse}}
td,th{{border-bottom:1px solid var(--line);padding:6px 10px;text-align:right}}
.victory{{color:var(--win);font-weight:600}} .defeat{{color:var(--loss);font-weight:600}} a{{color:inherit}}
</style></head><body>
<h1>Warcraft AI alone vs with Jev fighting</h1>
<p>Our side: insane melee AI playing {escape(runs[0].race)}; opponent: insane melee AI.
Wins: AI alone {wins[False]}/{len(runs) // 2}, with Jev {wins[True]}/{len(runs) // 2}.
Lost and killed count units that died in view.</p>
<div class=wrap><table>
<tr><th rowspan=2>seed</th><th colspan=6>AI alone</th><th colspan=6>AI + Jev</th><th rowspan=2></th></tr>
<tr>{head}{head}</tr>{"".join(rows)}</table></div></body></html>
"""
