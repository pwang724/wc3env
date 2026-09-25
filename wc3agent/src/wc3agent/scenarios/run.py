"""Run scenarios, several games at a time, and gather the results on one page.

Each scenario runs in its own process and folder (calls.jsonl, transcript, report.html, summary.json
with its metrics); `index.html` in the parent folder lists every scenario's checks and links to its report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from ..config import ROOT
from ..play import MeleeConfig, play
from ..report import report
from .scenario import Scenario, names

STYLE = """
:root{--bg:#fbfaf7;--panel:#fff;--ink:#1d1f23;--dim:#6b7280;--line:#e2e0da;--good:#2f6f4f;--bad:#b4412b;--goodbg:#e4f1ea;--badbg:#f8e4df}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,Segoe UI,sans-serif}
main{margin:0;padding:24px 24px 80px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--dim);margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:10px 0;padding:12px 14px}
.head{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}.head a{font-weight:600;font-size:16px;color:var(--ink)}
.score{font-variant-numeric:tabular-nums;font-weight:700}.goal{color:var(--dim);margin:4px 0 8px}
.checks{display:flex;flex-wrap:wrap;gap:6px}.check{border-radius:6px;padding:3px 9px;font-size:13px;font-variant-numeric:tabular-nums;border:1px solid var(--line)}
.ok{background:var(--goodbg);color:var(--good);border-color:transparent}.no{background:var(--badbg);color:var(--bad);border-color:transparent}
.meta{color:var(--dim);font-size:12px;margin-left:auto}
"""


def run_one(name, out, speed=None, hidden=True, realtime=False, debug=False, feedback=False):
    # Visibility and clock mode are independent; unattended stepped runs default to 8x.
    speed = speed if speed is not None else (1.0 if realtime or not hidden else 8.0)
    scenario = Scenario(name)
    summary = play(
        MeleeConfig(
            out=Path(out),
            race=scenario.race,
            speed=speed,
            hidden=hidden,
            realtime=realtime,
            debug=debug,
            feedback=feedback,
        ),
        scenario=scenario,
    )
    report(out)
    return summary


def index(root: Path):
    cards, passed, total, spent = [], 0, 0, 0.0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        summary_path = folder / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        scenario = Scenario(folder.name) if folder.name in names() else None
        checks = summary.get("metrics") or []
        good = sum(1 for c in checks if c["ok"])
        passed, total = passed + good, total + len(checks)
        spent += (summary.get("cost") or {}).get("total_dollars", 0)
        chips = "".join(
            f'<span class="check {"ok" if c["ok"] else "no"}">{escape(c["metric"])}: {escape(str(c["measured"]))} '
            f'<span style="opacity:.7">(want {escape(c["op"])} {escape(str(c["value"]))})</span></span>'
            for c in checks
        )
        failure = (
            ""
            if checks
            else f'<span class="check no">did not finish: {escape(str(summary.get("result") or "see run.log"))}</span>'
        )
        cards.append(
            f'<div class="card"><div class="head"><a href="{escape(folder.name)}/report.html">{escape(scenario.title if scenario else folder.name)}</a>'
            f'<span class="score">{good}/{len(checks)}</span>'
            f'<span class="meta">{summary.get("turns", 0)} turns · {summary.get("micro_calls", summary.get("jev_calls", 0))} micro calls · '
            f"{summary.get('game_seconds', 0):.0f}s game · ${(summary.get('cost') or {}).get('total_dollars', 0):.4f} · {escape(str(summary.get('model', '')))}</span></div>"
            f'<div class="goal">{escape(scenario.goal if scenario else "")}</div><div class="checks">{chips}{failure}</div></div>'
        )
    page = (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Scenarios — {escape(root.name)}</title><style>{STYLE}</style></head><body><main><h1>Scenarios — {escape(root.name)}</h1>"
        f'<div class="sub">{passed} of {total} checks met; ${spent:.4f} in model calls. Each title opens that game\'s calls.</div>{"".join(cards)}</main></body></html>'
    )
    (root / "index.html").write_text(page, encoding="utf-8")
    return root / "index.html", passed, total


def run_many(wanted, jobs=3, out=None, speed=8.0):
    wanted = wanted or names()
    unknown = [n for n in wanted if n not in names()]
    if unknown:
        raise SystemExit(f"Unknown scenarios {unknown}; there are: {', '.join(names())}")
    root = Path(out) if out else ROOT / "sessions" / f"scenarios-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    root.mkdir(parents=True, exist_ok=True)

    def launch(name):
        folder = root / name
        if folder.exists():  # a re-run replaces the earlier result
            shutil.rmtree(folder)
        folder.mkdir()
        with (folder / "run.log").open("w", encoding="utf-8") as log:
            code = subprocess.run(
                [sys.executable, "-m", "wc3agent", "scenario", name, "--out", str(folder), "--speed", str(speed)],
                stdout=log, stderr=subprocess.STDOUT,
            ).returncode  # fmt: skip
        print(f"{name}: " + ("done" if code == 0 else f"FAILED (exit {code}), see {folder / 'run.log'}"), flush=True)
        return code

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        codes = list(pool.map(launch, wanted))
    page, passed, total = index(root)
    print(
        f"{passed}/{total} checks met across {len(wanted)} scenarios ({sum(c != 0 for c in codes)} crashed). Results: {page}"
    )
    return page
