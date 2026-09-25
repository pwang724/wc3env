"""Raw installed game tables and order IDs; no agent policy or model dependencies."""

import json
import re
from pathlib import Path

from wc3env.settings import settings

from .mpq import Archive

RACES = ("Human", "Orc", "Undead", "NightElf", "Neutral")


def read_all(names: list[str], game_dir: Path | None = None) -> dict[str, str]:
    """each file from the latest archive that has it"""
    out: dict[str, str] = {}
    gd = Path(game_dir) if game_dir is not None else settings().game_exe.parent
    for mpq in ("War3Patch.mpq", "War3x.mpq", "War3.mpq"):
        p = gd / mpq
        if not p.exists():
            continue
        with Archive(p) as a:
            for n in names:
                if n not in out and a.has(n):
                    try:
                        out[n] = a.read(n).decode("latin-1")
                    except OSError:
                        pass
    return out


def slk(text: str) -> dict[str, dict[str, str]]:
    """rows keyed by the first column"""
    cols: dict[int, str] = {}
    rows: dict[int, dict[str, str]] = {}
    x = y = 0
    for line in text.splitlines():
        if not line.startswith("C;"):
            continue
        for part in line.split(";")[1:]:
            if part.startswith("X"):
                x = int(part[1:])
            elif part.startswith("Y"):
                y = int(part[1:])
            elif part.startswith("K"):
                v = part[1:].strip('"')
                if y == 1:
                    cols[x] = v
                else:
                    rows.setdefault(y, {})[cols.get(x, str(x))] = v
    key = cols[1]
    return {r[key]: r for r in rows.values() if key in r}


def profiles(text: str) -> dict[str, dict[str, str]]:
    """[id] sections of a *Func.txt file"""
    out: dict[str, dict[str, str]] = {}
    cur = None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"\[(\w{4})\]$", line)
        if m:
            cur = out.setdefault(m.group(1), {})
        elif cur is not None and "=" in line and not line.startswith("//"):
            k, _, v = line.partition("=")
            cur[k.strip()] = v.strip()
    return out


def order_ids():
    return json.loads((Path(__file__).with_name("data") / "orders.json").read_text())
