"""Run tools/bench.py in one or more Wine prefixes at once, each with its own wineserver, and add them up.

    python3 bench_prefixes.py --output-dir /sessions/x --prefixes 4 --instances 2 --observation binary

Without ntsync every wait, event and pipe message of every game in a prefix queues through that
prefix's one wineserver, which stopped a single prefix from scaling past about four games. The first
prefix is the image's own ($WINEPREFIX); the others are copies of it in /tmp, sharing the game files
through the drive_c/wc3 link. Arguments other than these two go to every bench.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefixes", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    args, bench = parser.parse_known_args()
    base = Path(os.environ["WINEPREFIX"])
    runs = []
    started = time.monotonic()
    for i in range(args.prefixes):
        prefix = base if i == 0 else Path(f"/tmp/wine-prefix-{i}")
        if not prefix.exists():
            shutil.copytree(base, prefix, symlinks=True)
        output = args.output_dir / f"bench-{i}.json"
        windows_output = subprocess.check_output(["winepath", "-w", str(output)], text=True).strip()
        command = ["wine", r"C:\Python311\python.exe", r"Z:\opt\worker\bench.py", "--output", windows_output, *bench]
        log = open(args.output_dir / f"bench-{i}.log", "w")
        runs.append(
            (
                output,
                subprocess.Popen(
                    command, env={**os.environ, "WINEPREFIX": str(prefix)}, stdout=log, stderr=subprocess.STDOUT
                ),
            )
        )
    codes = [process.wait() for _, process in runs]
    wall = time.monotonic() - started
    summaries = [json.loads(output.read_text())["summary"] for output, _ in runs if output.is_file()]
    game_seconds = sum(s["game_seconds"] for s in summaries)
    steps = sum(s["steps"] for s in summaries)
    total = {
        "prefixes": args.prefixes,
        "failed_prefixes": sum(1 for code in codes if code),
        "failed_instances": sum(s["failed_instances"] for s in summaries),
        "wall_seconds": round(wall, 1),  # from before the first copy to the last exit
        "game_seconds": game_seconds,
        "steps": steps,
        "aggregate_realtime_factor": round(game_seconds / wall, 1),
        "steps_per_second": round(steps / wall, 1),
    }
    (args.output_dir / "bench.json").write_text(json.dumps({"summary": total, "prefixes": summaries}, indent=2))
    print(json.dumps(total, indent=2), flush=True)
    return 1 if total["failed_prefixes"] or total["failed_instances"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
