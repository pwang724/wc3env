"""Write model calls, submitted action batches, and the run summary."""

import json

from .console import Console
from .models.cost import Ledger


class RunLog:
    def __init__(self, out, config, model, scenario, debug=False):
        self.out = out
        self.ledger = Ledger()
        self.console = Console() if debug else None
        self.summary = {
            "config": {k: str(v) if hasattr(v, "__fspath__") else v for k, v in vars(config).items()},
            "scenario": scenario.name if scenario else None,
            "model": getattr(model, "model", type(model).__name__),
            "turns": 0,
            "micro_calls": 0,
            "result": "",
            "cost": {},
        }

    def calls(self, records):
        for original in records:
            record = dict(original)
            if record["kind"] == "macro":
                self.summary["turns"] = record["turn"]
                system = record.pop("system_prompt", None)
                pinned = record.pop("pinned", "")
                if system is not None:
                    (self.out / "system_prompt.txt").write_text(system, encoding="utf-8")
                if pinned:
                    (self.out / "pinned.txt").write_text(pinned, encoding="utf-8")
                if not self.console:
                    print(
                        f"[{record['at_game_time']:7.1f}s -> {record['landed_at_game_time']:5.1f}s] "
                        f"macro turn {record['turn']}: {len(record['actions'])} proposed orders, "
                        f"{len(record['problems'])} problems, model {record['latency_ms'] / 1000:.1f}s",
                        flush=True,
                    )
            else:
                self.summary["micro_calls"] += 1
            record["turn"] = self.summary["turns"]
            if self.console:
                self.console.show(record)
            record["dollars"] = self.ledger.add(record["model"], record.get("usage", {}))
            self.write("calls.jsonl", record)

    def submitted(self, actions, rejected, game_time):
        self.write("actions.jsonl", {"at_game_time": game_time, "actions": actions, "rejected": rejected})

    def outcomes(self, rows):
        for row in rows:
            self.write("outcomes.jsonl", row)

    def write(self, name, record):
        with (self.out / name).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def close(self):
        self.summary["cost"] = self.ledger.summary()
        (self.out / "summary.json").write_text(json.dumps(self.summary, indent=2), encoding="utf-8")
