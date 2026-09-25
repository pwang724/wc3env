"""Fill the site template with real calls: python build.py (python tools/site/build.py from the repo root)."""

import json
from html import escape
from pathlib import Path

HERE = Path(__file__).parent  # tools/site
FULL = Path("sessions/melee-orc-opus3/calls.jsonl")
DUEL = Path("sessions/replays/orc/orc-jev-2/calls.jsonl")


def rows(path, kind):
    return [r for r in map(json.loads, path.open(encoding="utf-8")) if r.get("kind") == kind]


def clock(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


macro = next(r for r in rows(FULL, "macro") if 655 < r["at_game_time"] < 662)
system = macro["request"]["system"][-1]["text"]
micro = rows(DUEL, "micro")[106]
state = dict(micro["request"]["state"])
rules = state.pop("game_knowledge")
state.pop("format", None)
unit = "farseer1"
question = micro["request"]["questions"][unit]
answer = micro["response"]["answers"][unit]
options = question["criteria"]
question_text = question["instructions"] + "\n\nOPTIONS\n" + "\n".join(f"- {k}: {v}" for k, v in options.items())
top = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])[:8]
bars = "".join(
    f'<li class="{"top" if i == 0 else ""}"><span>{escape(k)}</span><span class="track"><span class="fill" style="width:{p * 100:.0f}%"></span></span><span class="pct">{p * 100:.0f}%</span></li>'
    for i, (k, p) in enumerate(top)
)
usage = macro["usage"]
fill = {
    "MACRO_META": escape(
        f"Orc vs Orc on Echo Isles against the Insane AI, turn {macro['turn']} at {clock(macro['at_game_time'])}. "
        f"Claude Opus 5.5 answered in {macro['latency_ms'] / 1000:.1f}s; {usage['cache_read_input_tokens']:,} of its input tokens came from the cache. "
        "This is the order that sent the army past the enemy's expansion, where it later lost the fight."
    ),
    "MACRO_SYSTEM_SIZE": f"{len(system) // 1000}k characters",
    "MACRO_SYSTEM": escape(system),
    "MACRO_OBS": escape(macro["observation"].strip()),
    "MACRO_REPLY": escape(macro["reply"].strip()),
    "MICRO_META": escape(
        f"The Far Seer in the Orc duel above, {micro['at_game_time']:.1f}s into the fight. "
        f"Jev answered in {micro['latency_ms'] / 1000:.2f}s and picked {answer['choice']}."
    ),
    "MICRO_RULES": escape(rules),
    "MICRO_STATE": escape(json.dumps(state, indent=2, ensure_ascii=False)),
    "MICRO_OPTION_COUNT": str(len(options)),
    "MICRO_QUESTION": escape(question_text),
    "MICRO_ANSWER": bars,
    "MICRO_GUIDANCE": escape(question["instructions"]),
    "MICRO_OPTIONS": "".join(f"<li><b>{escape(k)}</b><span>{escape(v)}</span></li>" for k, v in options.items()),
    "MICRO_LATENCY": f"{micro['latency_ms'] / 1000:.2f}",
}
page = (HERE / "template.html").read_text(encoding="utf-8")
for key, value in fill.items():
    page = page.replace("{{" + key + "}}", value)
assert "{{" not in page
Path("docs/index.html").write_text(page, encoding="utf-8")
print(f"docs/index.html {len(page) // 1000}k")
