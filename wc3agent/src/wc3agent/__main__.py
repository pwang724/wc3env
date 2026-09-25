"""Entry point: python -m wc3agent melee | scenario | scenarios | report."""

import argparse
from pathlib import Path

from .duel import ARMIES, duels
from .fusion import FusionConfig, compare, fuse
from .play import DIFFICULTIES, MIN_TURN_SECONDS, RACES, MeleeConfig, play
from .report import report as render
from .scenarios.run import run_many, run_one
from .scenarios.scenario import Scenario, names


def main():
    parser = argparse.ArgumentParser(description="Play Warcraft III with a macro model and Jev for micro control")
    commands = parser.add_subparsers(dest="command", required=True)
    melee = commands.add_parser("melee", help="Play a full melee game against the built-in computer")
    melee.add_argument("--map", default="(2)EchoIsles.w3x")
    melee.add_argument("--race", default="human", choices=tuple(RACES))
    melee.add_argument("--opponent-race", choices=tuple(RACES))
    melee.add_argument(
        "--difficulty",
        default="easy",
        choices=tuple(DIFFICULTIES),
        help="insane is the highest difficulty; hard is an alias",
    )
    melee.add_argument("--max-game-minutes", type=float, default=30.0)
    melee.add_argument(
        "--turn-interval-seconds",
        type=float,
        default=MIN_TURN_SECONDS,
        help="minimum game seconds between macro request starts (default/minimum: 5); one request at a time",
    )
    melee.add_argument("--speed", type=float, default=1.0)
    melee.add_argument("--hidden", action="store_true")
    melee.add_argument(
        "--realtime", action="store_true", help="the game runs on its own clock instead of being stepped"
    )
    melee.add_argument("--debug", action="store_true", help="print macro replies and micro choices live, in color")
    melee.add_argument("--feedback", action="store_true", help="type lines in the terminal to tell the macro model")
    melee.add_argument("--record", action="store_true", help="film the game with sound; replay.mp4 with Jev's choices")
    melee.add_argument("--out", type=Path)
    fusion = commands.add_parser("fusion", help="Warcraft's AI plays our side; Jev fights")
    fusion.add_argument("--map", default="(2)EchoIsles.w3x")
    fusion.add_argument("--race", default="human", choices=tuple(RACES))
    fusion.add_argument("--opponent-race", choices=tuple(RACES))
    fusion.add_argument("--difficulty", default="insane", choices=tuple(DIFFICULTIES), help="both AIs")
    fusion.add_argument("--no-jev", dest="jev", action="store_false", help="the AI plays alone (baseline)")
    fusion.add_argument("--seed", type=int)
    fusion.add_argument("--max-game-minutes", type=float, default=30.0)
    fusion.add_argument("--speed", type=float, default=1.0)
    fusion.add_argument("--hidden", action="store_true")
    fusion.add_argument("--realtime", action="store_true")
    fusion.add_argument("--debug", action="store_true", help="print Jev's choices live, in color, also on screen")
    fusion.add_argument("--out", type=Path)
    versus = commands.add_parser("compare", help="Same seeds with and without Jev; prints both sides' results")
    versus.add_argument("--games", type=int, default=5, help="seeds 1..N, each played twice")
    versus.add_argument("--jobs", type=int, default=2, help="games at once")
    versus.add_argument("--race", default="human", choices=tuple(RACES))
    versus.add_argument("--opponent-race", choices=tuple(RACES))
    versus.add_argument("--max-game-minutes", type=float, default=30.0)
    versus.add_argument("--visible", action="store_true", help="show every game window")
    versus.add_argument("--out", type=Path)
    mirror = commands.add_parser("duels", help="Identical armies per race: Warcraft's fighting vs Jev")
    mirror.add_argument("races", nargs="*", default=list(ARMIES), choices=list(ARMIES))
    mirror.add_argument("--runs", type=int, default=5, help="duels per race and side")
    mirror.add_argument("--jobs", type=int, default=2, help="game processes at once")
    mirror.add_argument("--visible", action="store_true")
    mirror.add_argument(
        "--realtime", action="store_true", help="the game runs on its own clock; Jev answers while it runs"
    )
    mirror.add_argument("--out", type=Path)
    one = commands.add_parser("scenario", help="Run one scenario and render its report")
    one.add_argument("name")
    one.add_argument("--out", type=Path, required=True)
    one.add_argument(
        "--speed", type=float, help="game seconds per wall second; default 8, or 1 with --visible / --realtime"
    )
    one.add_argument("--visible", action="store_true", help="show the game window without changing clock mode")
    one.add_argument("--realtime", action="store_true", help="run continuously while models think")
    one.add_argument("--debug", action="store_true", help="print macro replies and micro choices live, in color")
    one.add_argument("--feedback", action="store_true", help="type lines in the terminal to tell the macro model")
    many = commands.add_parser("scenarios", help="Run scenarios in parallel and gather the results on one page")
    many.add_argument("names", nargs="*", help="default: all of them")
    many.add_argument("--jobs", type=int, default=3, help="games at once; the macro model's rate limit is the bound")
    many.add_argument("--speed", type=float, default=8.0)
    many.add_argument("--out", type=Path)
    many.add_argument("--list", action="store_true")
    report = commands.add_parser("report", help="Render a session's macro and micro calls as one HTML page")
    report.add_argument("session", type=Path)
    report.add_argument("--output", type=Path)
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command == "melee":
        play(MeleeConfig(**args))
    elif command == "fusion":
        fuse(FusionConfig(**args))
    elif command == "duels":
        duels(args["races"] or list(ARMIES), args["runs"], args["jobs"], args["out"], args["visible"], args["realtime"])
    elif command == "compare":
        compare(args.pop("games"), args.pop("jobs"), args.pop("out"), **args)
    elif command == "scenario":
        run_one(
            args["name"],
            args["out"],
            args["speed"],
            hidden=not args["visible"],
            realtime=args["realtime"],
            debug=args["debug"],
            feedback=args["feedback"],
        )
    elif command == "scenarios":
        if args["list"]:
            for name in names():
                print(f"{name:20} {Scenario(name).title}")
        else:
            run_many(args["names"], args["jobs"], args["out"], args["speed"])
    elif command == "report":
        render(args["session"], args["output"])


if __name__ == "__main__":
    main()
