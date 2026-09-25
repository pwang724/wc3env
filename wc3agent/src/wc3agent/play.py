"""Run a game: observe, ask the agent, submit its batch, and let the environment step.

The environment owns the clock. This runner owns setup, pacing, recording, and cleanup.
It and the other runners (fusion.py, duel.py) are the only agent-package modules that import wc3env.
"""

from __future__ import annotations

import math
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread

from wc3env import GameConfig, GameSession, MatchSetup, PlayerConfig

from .agent import MIN_TURN_SECONDS, Agent, check_turn_seconds
from .config import PACKAGE_DIR, ROOT, environment
from .game.catalog import Catalog
from .game.mapinfo import MapInfo
from .game.policies import CAMERA_STILL, camera_spot
from .models.chat import ChatModel
from .recording import RunLog
from .replay import Recorder, render

STEP_SECONDS = 1.0
OVERLAY_X, OVERLAY_Y = -1.0, 1.0  # --debug board: as far top-left as the overlay goes
CAMERA_SECONDS = 0.5  # a filmed game's camera moves at most this often (wall time)
OBSERVE_SECONDS = 0.1  # realtime refresh while models are thinking; a completed call wakes this sooner
RACES = {"human": "human", "orc": "orc", "undead": "undead", "nightelf": "night_elf"}
DIFFICULTIES = {"easy": 0, "normal": 1, "insane": 2, "hard": 2}


@dataclass(frozen=True)
class MeleeConfig:
    map: str = "(2)EchoIsles.w3x"
    race: str = "human"
    opponent_race: str | None = None  # None: the map's choice
    difficulty: str = "easy"
    max_game_minutes: float = 30.0
    turn_interval_seconds: float = MIN_TURN_SECONDS  # minimum game time between macro request starts
    speed: float = 1.0
    hidden: bool = False
    realtime: bool = False
    debug: bool = False  # print macro replies and micro choices live, in color
    feedback: bool = False  # lines typed in the terminal reach the macro model on its next request
    record: bool = False  # film the game window with sound and write replay.mp4 with Jev's choices beside it
    micro_call_limit: int | None = None  # optional total calls per Jev group/type
    reference: Path = PACKAGE_DIR / "game/data/reference.json"
    maps: Path = PACKAGE_DIR / "game/data/maps"
    out: Path | None = None

    def __post_init__(self):
        if self.race not in RACES:
            raise ValueError(f"Unknown race: {self.race}")
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(f"Unknown difficulty: {self.difficulty}")
        for name in ("max_game_minutes", "turn_interval_seconds", "speed"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.speed > 2048:
            raise ValueError("speed must be at most 2048")
        check_turn_seconds(self.turn_interval_seconds, "turn_interval_seconds")
        if not isinstance(self.map, str) or not self.map.strip():
            raise ValueError("map must be a nonempty name")
        if any(type(getattr(self, name)) is not bool for name in ("hidden", "realtime", "debug", "feedback", "record")):
            raise ValueError("hidden, realtime, debug, feedback and record must be booleans")
        if self.record and self.hidden:
            raise ValueError("record needs a visible game")
        if self.micro_call_limit is not None and (type(self.micro_call_limit) is not int or self.micro_call_limit < 1):
            raise ValueError("micro_call_limit must be a positive integer or None")


def advance(session, actions, opponent=(), ms=None):
    sent = {0: actions}
    if 1 in session.config.agent_slots:
        # Scripted opponents retain their staged IDs after units die. Use their own
        # observation so surviving units still receive orders outside our vision.
        enemy = session.observations[1]
        controlled = {u["unit_id"] for u in enemy["units"]}
        sent[1] = [] if enemy.get("result") else [a for a in opponent if a["unit_id"] in controlled]
    observations, _, info = session.step(sent, ms)
    for site in info.get("placements", {}).get(0, []):
        # Acknowledged native coordinates, used only to track the submitted order; `near` keeps the anchor.
        args = actions[site["index"]]["arguments"]
        args.update(near=[args["x"], args["y"]], x=site["x"], y=site["y"])
    return observations[0], info["rejected"][0]


def warm_up(session, obs, seconds, config):
    """Advance idle game time before staging, for example until shops have stock."""
    target = obs["game_time_seconds"] + seconds
    if config.realtime:
        session.debug("speed", factor=16.0)
    try:
        while not obs["result"] and obs["game_time_seconds"] < target:
            remaining = target - obs["game_time_seconds"]
            if config.realtime:
                time.sleep(min(1.0, remaining / 16.0))
                obs, _ = advance(session, [])
            else:
                ms = max(25, min(60000, round(remaining * 1000 / 25) * 25))
                obs, _ = advance(session, [], ms=ms)
    finally:
        if config.realtime:
            session.debug("speed", factor=config.speed)
    return obs


def listen(agent):
    """Pass each line typed in the terminal to the macro model."""
    print("Type a message and press Enter to tell the macro model; it answers on its next request.", flush=True)
    for line in sys.stdin:
        if line.strip():
            agent.tell(line.strip())
            print(f"[you] {line.strip()}", flush=True)


def play(config: MeleeConfig, model=None, scenario=None):
    settings = environment()
    if model is None:
        model = ChatModel.from_environment(settings)
    out = config.out or ROOT / "sessions" / f"melee-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    catalog = Catalog.load(config.reference)
    mapinfo = MapInfo.load(config.maps / f"{Path(config.map).stem}.json")
    scripted = bool(scenario and scenario.scripted_opponent)
    log = RunLog(out, config, model, scenario, debug=config.debug)
    with ExitStack() as resources:
        resources.callback(log.close)
        session = GameSession(
            GameConfig(
                map=config.map,
                players=(
                    PlayerConfig(0, race=RACES[config.race]),
                    PlayerConfig(
                        1,
                        race=RACES[config.opponent_race] if config.opponent_race else None,
                        control="agent" if scripted else "computer",
                    ),
                ),
                mode="realtime" if config.realtime else "stepping",
                step_ms=round(STEP_SECONDS * 1000),
                window_mode="background",
                render=not config.hidden,
                background_visible=not config.hidden,
                ai_difficulty=DIFFICULTIES[config.difficulty],
                setup=MatchSetup(),
                max_episodes_per_process=None,
                output_dir=out / "env",
            )
        )
        resources.callback(session.close)
        try:
            obs = session.reset()[0]
            session.debug("speed", factor=config.speed)
            if scenario:
                obs = warm_up(session, obs, scenario.skip_seconds, config)
                scenario.stage(session, catalog, mapinfo, obs)
                obs, _ = advance(session, [])
            agent = Agent(
                catalog,
                mapinfo,
                model,
                micro_model=settings.get("TYPESAFE_DEFAULT_MODEL", "jev-latest"),
                micro_key=settings.get("TYPESAFE_API_KEY", ""),
                transcript=out / "transcript.txt",
                goal=scenario.goal if scenario else "",
                turn_seconds=config.turn_interval_seconds,
                micro_call_limit=config.micro_call_limit,
            )
            resources.callback(agent.close)
            if config.feedback:
                Thread(target=listen, args=(agent,), daemon=True, name="feedback").start()
            limit = obs["game_time_seconds"] + (scenario.minutes if scenario else config.max_game_minutes) * 60
            recorder = Recorder(out) if config.record else None
            if recorder:
                recorder.start()
                resources.callback(recorder.stop)  # before the game closes: callbacks run last-in first-out
            camera, camera_at = None, 0.0
            while True:
                if recorder:
                    recorder.note(obs["game_time_seconds"])
                    spot = camera_spot(obs, catalog)
                    if (
                        spot
                        and time.monotonic() - camera_at >= CAMERA_SECONDS
                        and (camera is None or math.dist(spot, camera) >= CAMERA_STILL)
                    ):
                        session.debug("camera", x=round(spot[0]), y=round(spot[1]))
                        camera, camera_at = spot, time.monotonic()
                for line in obs.get("chat", ()):  # typed into the game's chat box: the macro model hears it next
                    agent.tell(line)
                    print(f"[you, in game] {line}", flush=True)
                if scenario:
                    scenario.saw(obs)
                finished = bool(scenario and scenario.finished(obs))
                if obs["result"] or finished or obs["game_time_seconds"] >= limit:
                    break
                step = agent.act(obs, wait=not config.realtime)
                log.calls(step.records)
                if log.console and (panel := log.console.panel()):
                    session.debug("overlay", panel=panel, x=OVERLAY_X, y=OVERLAY_Y, seconds=3600)
                log.outcomes(agent.macro_memory.outcomes.drain())
                opponent = scenario.opponent_actions(obs) if scripted else ()
                if config.realtime and not step.actions and not opponent:
                    agent.ready.wait(OBSERVE_SECONDS)
                submitted_at = obs["game_time_seconds"]
                obs, rejected = advance(session, step.actions, opponent)
                # Realtime's acknowledgement observation supplies the current game timestamp.
                if config.realtime:
                    submitted_at = obs["game_time_seconds"]
                agent.submitted(step, rejected, submitted_at)
                log.submitted(step.actions, rejected, submitted_at)
                log.outcomes(agent.macro_memory.outcomes.drain())
            agent.macro_memory.update(obs)
            log.calls(agent.micro.finish(obs))
            agent.macro_memory.outcomes.finish(obs["game_time_seconds"])
            log.outcomes(agent.macro_memory.outcomes.drain())
            log.summary.update(
                result=obs["result"] or ("scenario_finished" if finished else "time_limit"),
                game_seconds=obs["game_time_seconds"],
                score=obs.get("score", {}),
            )
            if scenario:
                log.summary["metrics"] = scenario.score(obs)
            try:
                session.save_replay(out / "replay.w3g")
            except Exception as problem:  # noqa: BLE001  a replay is a convenience
                log.summary["replay_error"] = str(problem)
        except Exception as problem:
            log.summary.update(result="error", error=str(problem))
            raise
    if config.record and (out / "frames.json").exists():
        title = f"Jev · {config.race} vs {config.opponent_race or 'the AI'}"
        print(f"Replay: {render(out, title)}", flush=True)
    print(f"{log.summary['result']} after {log.summary.get('game_seconds', 0):.0f}s of game time; session in {out}")
    return log.summary
