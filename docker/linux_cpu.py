"""Run a command and report the Linux CPU it cost: the container total and each process by name.

    python3 linux_cpu.py --output /sessions/x/cpu.json -- wine python.exe bench.py ...

Wine's own processes (wineserver, the Windows services) and Xvfb spend CPU that the Windows
view inside Wine never sees. The container total comes from its cgroup; per-process time is
sampled from /proc once a second, so a process that exits between samples loses its last second.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TICKS = os.sysconf("SC_CLK_TCK")


def cgroup_cpu_seconds() -> float | None:
    v2 = Path("/sys/fs/cgroup/cpu.stat")
    if v2.is_file():
        for line in v2.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec":
                return int(value) / 1e6
    v1 = Path("/sys/fs/cgroup/cpuacct/cpuacct.usage")
    return int(v1.read_text()) / 1e9 if v1.is_file() else None


def processes() -> dict[int, tuple[str, float]]:
    found = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            cmdline = (entry / "cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
        except OSError:
            continue
        fields = stat[stat.rindex(")") + 2 :].split()
        name = Path(cmdline.replace("\\", "/")).name or stat[stat.index("(") + 1 : stat.rindex(")")]
        found[int(entry.name)] = (name, (int(fields[11]) + int(fields[12])) / TICKS)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    before = {pid: cpu for pid, (_, cpu) in processes().items()}
    cgroup0, wall0 = cgroup_cpu_seconds(), time.monotonic()
    seen: dict[int, tuple[str, float]] = {}
    child = subprocess.Popen(command)
    while child.poll() is None:
        for pid, (name, cpu) in processes().items():
            seen[pid] = (name, cpu - before.get(pid, 0.0))
        time.sleep(1.0)
    wall = time.monotonic() - wall0
    cgroup1 = cgroup_cpu_seconds()
    by_name: dict[str, float] = {}
    for name, cpu in seen.values():
        by_name[name] = by_name.get(name, 0.0) + cpu
    report = {
        "command": command,
        "exit_code": child.returncode,
        "wall_seconds": round(wall, 1),
        "cpu_count": os.cpu_count(),
        "container_cpu_seconds": round(cgroup1 - cgroup0, 1) if cgroup0 is not None and cgroup1 is not None else None,
        "process_cpu_seconds": {k: round(v, 1) for k, v in sorted(by_name.items(), key=lambda kv: -kv[1]) if v >= 0.1},
    }
    if report["container_cpu_seconds"] is not None:
        report["container_cores"] = round(report["container_cpu_seconds"] / wall, 2)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    return child.returncode


if __name__ == "__main__":
    sys.exit(main())
