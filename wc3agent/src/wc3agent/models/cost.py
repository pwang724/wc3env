"""What a call cost, from the provider's usage report and a rates table.

Rates are dollars per million tokens, by model name prefix, in `rates.json` next to this file: edit it
when prices change or a model is added. A model with no rate is counted as tokens only, cost null.
"""

from __future__ import annotations

import json
from pathlib import Path

RATES = json.loads((Path(__file__).with_name("rates.json")).read_text(encoding="utf-8"))


def rate_for(model):
    """The longest rate key that prefixes the model name (gpt-5.6-luna matches gpt-5.6-luna, then gpt-5.6)."""
    keys = [k for k in RATES if model and model.startswith(k)]
    return RATES[max(keys, key=len)] if keys else None


def tokens(usage):
    """One shape for both providers: {input (uncached), cached, cache_write, output}."""
    if not usage:
        return {"input": 0, "cached": 0, "cache_write": 0, "output": 0}
    if "prompt_tokens" in usage:  # OpenAI: prompt_tokens includes the cached and cache-written ones
        details = usage.get("prompt_tokens_details") or {}
        cached, written = details.get("cached_tokens", 0), details.get("cache_write_tokens", 0)
        return {
            "input": usage["prompt_tokens"] - cached - written,
            "cached": cached,
            "cache_write": written,
            "output": usage.get("completion_tokens", 0),
        }
    # Anthropic: input_tokens excludes the cache ones; Jev: input_tokens and output_tokens only
    return {
        "input": usage.get("input_tokens", 0),
        "cached": usage.get("cache_read_input_tokens", 0),
        "cache_write": usage.get("cache_creation_input_tokens", 0),
        "output": usage.get("output_tokens", 0),
    }


def cost(model, usage):
    """Dollars for one call, or None without a rate."""
    rate, t = rate_for(model), tokens(usage)
    if not rate:
        return None
    return round(
        (
            t["input"] * rate["input"]
            + t["cached"] * rate.get("cached", rate["input"])
            + t["cache_write"] * rate.get("cache_write", rate["input"])
            + t["output"] * rate["output"]
        )
        / 1e6,
        6,
    )


class Ledger:
    """Running totals per model: tokens by kind, calls and dollars."""

    def __init__(self):
        self.by_model = {}

    def add(self, model, usage):
        row = self.by_model.setdefault(
            model, {"calls": 0, "input": 0, "cached": 0, "cache_write": 0, "output": 0, "dollars": 0.0, "priced": True}
        )
        row["calls"] += 1
        for k, v in tokens(usage).items():
            row[k] += v
        dollars = cost(model, usage)
        if dollars is None:
            row["priced"] = False
        else:
            row["dollars"] = round(row["dollars"] + dollars, 6)
        return dollars

    def total(self):
        return round(sum(r["dollars"] for r in self.by_model.values()), 4)

    def summary(self):
        return {"total_dollars": self.total(), "by_model": self.by_model}
