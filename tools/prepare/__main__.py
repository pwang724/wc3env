"""Prepare reusable inputs; never launch Warcraft or call a model."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    reference = commands.add_parser("reference", help="Extract the full stock game reference JSON")
    reference.add_argument("--output", type=Path, default=Path("wc3agent/src/wc3agent/game/data/reference.json"))
    reference.add_argument("--game-dir", type=Path)
    map_info = commands.add_parser("map", help="Extract a stock melee map's start locations, mines, camps and shops")
    map_info.add_argument("--map", default="(2)EchoIsles.w3x")
    map_info.add_argument("--output", type=Path, default=Path("wc3agent/src/wc3agent/game/data/maps"))
    map_info.add_argument("--reference", type=Path, default=Path("wc3agent/src/wc3agent/game/data/reference.json"))
    map_info.add_argument("--game-dir", type=Path)
    table = commands.add_parser("footprints", help="Write the hook's structure footprint table from the reference")
    table.add_argument("--output", type=Path, default=Path("wc3hook/footprints_table.h"))
    table.add_argument("--reference", type=Path, default=Path("wc3agent/src/wc3agent/game/data/reference.json"))
    args = parser.parse_args()
    if args.command == "reference":
        from .reference import prepare_reference

        prepare_reference(args.output, args.game_dir)
    elif args.command == "footprints":
        from .hook_tables import prepare_footprints

        prepare_footprints(args.output, args.reference)
    else:
        from .mapinfo import prepare_map

        prepare_map(args.output, args.reference, args.map, args.game_dir)


if __name__ == "__main__":
    main()
