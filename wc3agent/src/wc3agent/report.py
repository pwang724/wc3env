"""Turn a recorded session into one HTML page: every model call, readable, searchable, offline.

Reads a session's `calls.jsonl`: macro turns with micro calls grouped under the active macro turn.
Each shows its observation, response and orders. `system_prompt.txt`
and `summary.json`, with a scenario's metrics, are shown when present.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from .game.references import TOKEN

ORDER_VERBS = (
    "train",
    "upgrade",
    "research",
    "cancel",
    "build",
    "rally",
    "gold",
    "lumber",
    "repair",
    "attack",
    "move",
    "stop",
    "learn",
    "cast",
    "buy",
    "use",
    "take",
)

STYLE = """
:root{--bg:#fbfaf7;--panel:#fff;--ink:#1d1f23;--dim:#6b7280;--line:#e2e0da;--accent:#2f6f4f;--warn:#b4412b;--bar:#cfe3d8;--code:#f3f1ec}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,Segoe UI,sans-serif}
main{margin:0;padding:24px 24px 80px}h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 8px}
.sub{color:var(--dim);margin-bottom:16px}.stats{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:110px}
.stat b{display:block;font-size:18px;font-variant-numeric:tabular-nums}.stat span{color:var(--dim);font-size:12px}
input{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font:inherit;position:sticky;top:8px;z-index:2}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:8px 0}
summary{cursor:pointer;padding:9px 12px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
summary .t{font-variant-numeric:tabular-nums;font-weight:600;min-width:52px}summary .plan{flex:1;min-width:240px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{font-size:12px;border:1px solid var(--line);border-radius:99px;padding:1px 8px;color:var(--dim);white-space:nowrap}.tag.warn{color:var(--warn);border-color:var(--warn)}
.body{padding:0 12px 12px;display:grid;gap:10px}.cols{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:12px}
@media (max-width:800px){.cols{grid-template-columns:minmax(0,1fr)}}
pre{margin:0;background:var(--code);border-radius:6px;padding:10px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font:12.5px/1.45 ui-monospace,Consolas,monospace}
.label{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:3px}
.nested{margin-left:28px}.order{color:var(--accent);font-weight:600}.problem{color:var(--warn)}
.choice{display:grid;grid-template-columns:minmax(0,1fr) 120px 48px;gap:8px;align-items:center;font-size:13px;padding:1px 0}
.choice .barbox{background:var(--code);border-radius:4px;height:10px}.choice .bar{background:var(--bar);height:10px;border-radius:4px}
.choice.picked{font-weight:700;color:var(--accent)}.choice.picked .bar{background:var(--accent)}.num{text-align:right;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;margin:12px 0}th,td{text-align:left;padding:6px 12px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
"""

SCRIPT = """
const box=document.getElementById('q');box.addEventListener('input',()=>{const q=box.value.toLowerCase();
document.querySelectorAll('details.call').forEach(d=>{const text=d.querySelector('summary').textContent+' '+d.querySelector('.cols').textContent;
d.style.display=!q||text.toLowerCase().includes(q)?'':'none'})});
"""


def clock(seconds):
    minutes, seconds = divmod(round(seconds, 1), 60)
    return f"{int(minutes)}:{seconds:04.1f}"


def call_time(record):
    requested = clock(record.get("at_game_time", 0))
    responded = record.get("landed_at_game_time")
    return f"{requested} → {clock(responded)}" if responded is not None else f"{requested} → not recorded"


def block(label, text, cls=""):
    return f'<div><div class="label">{escape(label)}</div><pre class="{cls}">{text}</pre></div>'


def stat(value, label):
    return f'<div class="stat"><b>{escape(str(value))}</b><span>{escape(label)}</span></div>'


def median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else 0


def is_order(line):
    words = line.strip().lstrip("-*• ").split()
    if words and words[0].lower() == "queue":
        words = words[1:]
    return len(words) > 1 and words[0].lower() in ORDER_VERBS and re.fullmatch(TOKEN, words[1], re.I) is not None


def raw_exchange(r):
    request = r.get("request")
    if request is None:
        return '<details><summary>LLM request — not recorded in this older session</summary><div class="body">Only the current observation and reply were recorded; the exact retained conversation is unavailable.</div></details>'
    messages = list(request.get("messages", []))
    if "system" in request:
        messages.insert(0, {"role": "system", "content": request["system"]})
    parts = []
    for index, message in enumerate(messages, 1):
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, indent=2, ensure_ascii=False)
        role = escape(message.get("role", "unknown"))
        parts.append(
            f'<details><summary>Message {index} · {role}</summary><div class="body">{block(role, escape(content))}</div></details>'
        )
    parts.append(block("Assistant response to this request", escape(r["reply"])))
    parts.append(
        '<details><summary>Raw request / response JSON</summary><div class="body">'
        + block("Request body (no authentication headers)", escape(json.dumps(request, indent=2, ensure_ascii=False)))
        + block("Response", escape(json.dumps(r.get("response"), indent=2, ensure_ascii=False)))
        + "</div></details>"
    )
    return f'<details><summary>LLM request / response — {len(messages)} message blocks</summary><div class="body">{"".join(parts)}</div></details>'


def macro_call(r, outcomes=()):
    reply = "\n".join(
        f'<span class="order">{escape(line)}</span>' if is_order(line) else escape(line)
        for line in r["reply"].splitlines()
    )
    plan = next((line for line in r["reply"].splitlines() if line.strip()), "")
    usage = r.get("usage", {})
    tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    tags = [
        f'<span class="tag">{len(r["actions"])} orders</span>',
        f'<span class="tag">{r["latency_ms"] / 1000:.1f}s</span>',
    ]
    if r.get("dollars") is not None:
        tags.append(f'<span class="tag">${r["dollars"]:.4f}</span>')
    if tokens is not None:
        tags.append(f'<span class="tag">{tokens:,} tokens in</span>')
    if r["problems"]:
        tags.append(f'<span class="tag warn">{len(r["problems"])} problems</span>')
    sent = "\n".join(escape(action_text(a)) for a in r["actions"] + r.get("chores", []))
    right = block("Reply", reply) + block(
        f"Actions proposed ({len(r['actions'])} ordered, {len(r.get('chores', []))} idle-worker chores)", sent
    )
    if r["problems"]:
        right += block(
            "Problems with these orders (reported back next turn)", escape("\n".join(r["problems"])), "problem"
        )
    warnings = [o for o in outcomes if o["status"] in ("rejected", "not_observed", "unconfirmed")]
    if warnings:
        tags.append(f'<span class="tag warn">{len(warnings)} orders need attention</span>')
    if outcomes:
        evidence = []
        for o in outcomes:
            line = escape(
                f"Order {o['id']} · submitted {o['ordered_at']:.1f}s · checked {o['at_game_time']:.1f}s\n"
                f"{o['label']}: {o['status']}\n{o['evidence']}"
            )
            evidence.append(f'<span class="problem">{line}</span>' if o in warnings else line)
        right += block("Observed outcomes of this turn's orders", "\n\n".join(evidence))
    if any("did NOT start" in n for n in r.get("told", [])):
        tags.append('<span class="tag warn">earlier build not observed</span>')
        right += block(
            "Legacy feedback",
            "The older logger inferred that construction did not start. It did not record an engine failure reason; its suggested causes and cost claim were not verified.",
        )
    return (
        f'<details class="call" id="macro-turn-{r["turn"]}"><summary><span class="t">Macro turn {r["turn"]}</span><span class="tag">{call_time(r)}</span>'
        f'<span class="plan">{escape(plan[:160])}</span>{"".join(tags)}</summary><div class="body"><div class="cols">'
        f'{block("Observation (what the model read)", escape(r["observation"]))}<div style="display:grid;gap:10px;align-content:start">{right}</div>'
        f"</div>{raw_exchange(r)}</div></details>"
    )


def readable(value, depth=0):
    """Nested JSON as indented plain text: `key: value` lines, nested sections indented under their
    key, lists of plain values joined with commas, multi-line text as an indented paragraph."""
    pad = "  " * depth
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            name = str(key).replace("_", " ")
            if isinstance(item, (dict, list)) and item and not _flat(item):
                lines.append(f"{pad}{name}:")
                lines.extend(readable(item, depth + 1))
            elif isinstance(item, str) and "\n" in item:
                lines.append(f"{pad}{name}:")
                lines.extend(f"{pad}  {line}" for line in item.strip().splitlines())
            else:
                lines.append(f"{pad}{name}: {_inline(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and not _flat(item):
                sub = readable(item, depth + 1)
                lines.append(f"{pad}- " + sub[0].lstrip())
                lines.extend(sub[1:])
            else:
                lines.append(f"{pad}- {_inline(item)}")
        return lines
    return [f"{pad}{_inline(value)}"]


def _flat(value):
    """Short enough to show on one line: a handful of plain values, none of them a long phrase."""
    items = list(value.values() if isinstance(value, dict) else value)
    return len(items) <= 6 and all(
        not isinstance(v, (dict, list)) and not (isinstance(v, str) and len(v) > 24) for v in items
    )


def _inline(value):
    if isinstance(value, dict):
        return ", ".join(f"{str(k).replace('_', ' ')} {_inline(v)}" for k, v in value.items()) or "none"
    if isinstance(value, list):
        return ", ".join(_inline(v) for v in value) or "none"
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def action_text(action):
    """One order as words: 'unit 14387 attack target 50'."""
    args = " ".join(f"{k.replace('_', ' ')} {_inline(v)}" for k, v in action.get("arguments", {}).items())
    return f"unit {action.get('unit_id')} {action.get('command')} {args}".strip()


def micro_call(r, number):
    answers = r.get("response", {}).get("answers", {}) if isinstance(r.get("response"), dict) else {}
    questions = r.get("request", {}).get("questions", {})
    picks = ", ".join(f"{name}: {a.get('choice')}" for name, a in answers.items())
    tags = [
        f'<span class="tag">{escape(str(r.get("control_group", "")))}</span>',
        f'<span class="tag">{r.get("latency_ms", 0) / 1000:.2f}s</span>',
    ]
    if r.get("dollars") is not None:
        tags.append(f'<span class="tag">${r["dollars"]:.5f}</span>')
    if "error" in r:
        tags.append('<span class="tag warn">error</span>')
    if r.get("dropped_actions"):
        tags.append(f'<span class="tag warn">{len(r["dropped_actions"])} discarded orders</span>')
    parts = []
    for name, q in questions.items():
        answer = answers.get(name, {})
        probabilities = answer.get("probabilities", {})
        rows = "".join(
            f'<div class="choice{" picked" if key == answer.get("choice") else ""}"><span>{escape(f"{key}: {text}")}</span>'
            f'<span class="barbox"><div class="bar" style="width:{100 * probabilities.get(key, 0):.0f}%"></div></span>'
            f'<span class="num">{100 * probabilities.get(key, 0):.0f}%</span></div>'
            for key, text in (q["criteria"].items() if isinstance(q["criteria"], dict) else enumerate(q["criteria"]))
        )
        parts.append(
            f'<div><div class="label">{escape(name)}</div>'
            f'<div style="white-space:pre-wrap;font-size:12px">{escape(q.get("instructions", ""))}</div>{rows}</div>'
        )
    state = r.get("request", {}).get("state", {})
    outcome = []
    if "landed_at_game_time" in r:
        outcome.append(f"answer arrived at {clock(r['landed_at_game_time'])}")
    outcome += [f"sent: {action_text(a)}" for a in r.get("actions", [])]
    outcome += [f"discarded ({d['reason']}): {action_text(d['action'])}" for d in r.get("dropped_actions", [])]
    outcome += [f"error: {r['error']}"] if "error" in r else []
    outcome += [f"warning: {w}" for w in r.get("probability_warnings", [])]
    right = "".join(parts) + (block("Outcome", escape("\n".join(outcome))) if outcome else "")
    # The raw request and response stay in calls.jsonl: copying them here tripled the page (64 MB for 736 calls).
    raw = f'<div class="label">Raw request and response: calls.jsonl, call index {r.get("call_index", "?")}</div>'
    return (
        f'<details class="call" id="micro-call-{number}"><summary><span class="t">Micro call {number}</span><span class="tag">{call_time(r)}</span>'
        f'<span class="plan">{escape(picks[:160])}</span>{"".join(tags)}</summary><div class="body"><div class="cols">'
        f'{block("State sent", escape(chr(10).join(readable(state))))}<div style="display:grid;gap:10px;align-content:start">{right}</div></div>{raw}</div></details>'
    )


def find_calls(session: Path) -> Path | None:
    if (session / "calls.jsonl").is_file():
        return session / "calls.jsonl"
    # A match can end before its first model response is logged.
    if (session / "summary.json").is_file():
        return None
    raise FileNotFoundError(f"No calls.jsonl in {session}")


def report(session, output=None):
    session = Path(session)
    calls_path = find_calls(session)
    calls = (
        [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if calls_path is not None
        else []
    )
    # Reports still read sessions recorded before the macro/micro rename.
    for call in calls:
        call["kind"] = {"system2": "macro", "jev": "micro"}.get(call.get("kind"), call.get("kind"))
    # Async replies are logged on completion, which may differ from request order.
    calls.sort(key=lambda c: (c.get("at_game_time", 0), c.get("kind") != "macro", c.get("call_index", 0)))
    macro = [c for c in calls if c.get("kind") == "macro"]
    micro = [c for c in calls if c.get("kind") != "macro"]
    folder = session
    outcomes_path = folder / "outcomes.jsonl"
    outcomes = {}
    if outcomes_path.is_file():
        for line in outcomes_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                outcomes[row["id"]] = row
    summary = (
        json.loads((folder / "summary.json").read_text(encoding="utf-8")) if (folder / "summary.json").is_file() else {}
    )
    stats = [stat(summary.get("result", "?"), "result")]
    if (limit := summary.get("config", {}).get("micro_call_limit")) is not None:
        stats.append(stat(limit, "Micro calls per group (scenario cap)"))
    if outcomes:
        stats.append(
            stat(
                sum(o["status"] in ("rejected", "not_observed", "unconfirmed") for o in outcomes.values()),
                "orders needing attention",
            )
        )
    money = summary.get("cost") or {}
    if money:
        stats.append(stat(f"${money['total_dollars']:.4f}", "cost"))
        for model, row in money.get("by_model", {}).items():
            stats.append(
                stat(
                    f"${row['dollars']:.4f}" if row["priced"] else "no rate",
                    f"{model}: {row['calls']} calls, {row['input']:,} in, {row['cached']:,} cached, {row['output']:,} out",
                )
            )
    if calls or "game_seconds" in summary:
        game_time = summary.get(
            "game_seconds", max((c.get("landed_at_game_time", c.get("at_game_time", 0)) for c in calls), default=0)
        )
        stats.append(stat(clock(game_time), "game time"))
    if macro:
        stats.append(stat(len(macro), "Macro turns"))
        stats.append(stat(f"{median([c['latency_ms'] for c in macro]) / 1000:.1f}s", "Macro median latency"))
        stats.append(stat(sum(len(c["actions"]) for c in macro), "orders proposed"))
        stats.append(stat(sum(len(c["problems"]) for c in macro), "problems"))
        stats.append(stat(summary.get("model", "?"), "Macro model"))
    if micro:
        stats.append(stat(len(micro), "Micro calls"))
        stats.append(stat(f"{median([c.get('latency_ms', 0) for c in micro]) / 1000:.2f}s", "Micro median latency"))
        stats.append(stat(sum("error" in c for c in micro), "Micro failed calls"))
        stats.append(stat(sum(len(c.get("dropped_actions", [])) for c in micro), "Micro dropped actions"))
    prompt = folder / "system_prompt.txt"
    prompt_html = (
        f'<details><summary><span class="t">Macro system prompt</span><span class="plan">sent with every macro turn, unchanged</span></summary>'
        f'<div class="body">{block("system_prompt.txt", escape(prompt.read_text(encoding="utf-8")))}</div></details>'
        if prompt.is_file()
        else ""
    )
    pinned = folder / "pinned.txt"
    if pinned.is_file():
        prompt_html += (
            '<details open><summary><span class="t">Pinned goal</span><span class="plan">the first message of the conversation, kept when old turns are cut</span></summary>'
            f'<div class="body">{block("pinned.txt", escape(pinned.read_text(encoding="utf-8")))}</div></details>'
        )
    grouped = {None: [], **{c["turn"]: [] for c in macro}}
    for number, c in enumerate(micro, 1):
        # A response that lands during this micro call did not supply its original objective.
        parent = next(
            (m for m in reversed(macro) if m.get("landed_at_game_time", m["at_game_time"]) <= c.get("at_game_time", 0)),
            None,
        )
        grouped[parent["turn"] if parent else None].append(micro_call(c, c.get("call_index", number - 1) + 1))
    body = []
    if macro and grouped[None]:
        body.append('<p class="sub">Before the first macro response</p>')
    body.extend(grouped[None])
    for c in macro:
        body.append(macro_call(c, [o for o in outcomes.values() if o["turn"] == c["turn"]]))
        if children := grouped[c["turn"]]:
            body.append(f'<div class="nested" data-macro-turn="{c["turn"]}">{"".join(children)}</div>')
    body = "".join(body)
    if not calls:
        body = '<p class="sub">No model calls were recorded for this session.</p>'
    other = [o for o in outcomes.values() if o["turn"] is None]
    if other:
        body = (
            block(
                "Other order outcomes",
                escape(
                    "\n".join(f"{o['at_game_time']:.1f}s {o['label']}: {o['status']} — {o['evidence']}" for o in other)
                ),
            )
            + body
        )
    metrics = summary.get("metrics", [])
    gates = ""
    if metrics:
        rows = "".join(
            f"<tr><td>{escape(m['metric'])}</td><td>{escape(str(m['measured']))}</td>"
            f"<td>{escape(m['op'])} {escape(str(m['value']))}</td>"
            f'<td class="{"" if m["ok"] else "problem"}">{"PASS" if m["ok"] else "FAIL"}</td></tr>'
            for m in metrics
        )
        gates = f"<h2>Scenario gates: {sum(m['ok'] for m in metrics)}/{len(metrics)} passed</h2><table><thead><tr><th>Metric</th><th>Measured</th><th>Gate</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table>"
    title = (
        f"{' + '.join(n for n, part in (('Macro', macro), ('Micro', micro)) if part)} calls — {session.name}"
        if calls
        else f"Session report — {session.name}"
    )
    page = (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head><body><main><h1>{escape(title)}</h1>"
        f'<div class="sub">{escape(str(calls_path or folder / "summary.json"))}</div><div class="stats">{"".join(stats)}</div>{gates}{prompt_html}'
        '<p class="sub">Submitted = accepted by the interface. Started / queued = observed in the game. '
        "Not observed = expected change was missing; the engine supplied no cause. Unconfirmed = the run ended while pending.</p>"
        '<h2>Model calls</h2><p class="sub">Micro calls sit under the macro turn active when they started, ordered by request time. '
        "Times show request → response in game time; macro turns and micro calls are numbered separately.</p>"
        f'<input id="q" placeholder="Filter: Macro turn 6, Micro call 6, a unit id, an order..."/>{body}'
        f"</main><script>{SCRIPT}</script></body></html>"
    )
    target = Path(output) if output else folder / "report.html"
    target.write_text(page, encoding="utf-8")
    print(f"Report: {target} ({len(calls)} calls)")
    return target
