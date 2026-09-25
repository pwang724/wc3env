"""The macro loop: observation text in, orders out, everything appended. It knows no game.

`think(text)` asks the model; `finish(...)` parses the reply against the latest observation with
the game's `parse(reply, observation) -> (actions, notes)`. The model is anything with
`complete(system, messages) -> (text, record)`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .conversation import Conversation


@dataclass
class Turn:
    observation: str
    reply: str
    actions: list[dict]
    notes: list[str]
    record: dict = field(default_factory=dict)


class MacroAgent:
    def __init__(self, model, system: str, parse: Callable, conversation: Conversation):
        self.model, self.system, self.parse = model, system, parse
        self.conversation = conversation

    def think(self, text: str) -> tuple[str, str, dict]:
        """Capture the actual retained conversation at request time, before the reply lands."""
        messages = self.conversation.messages(text)
        reply, record = self.model.complete(self.system, messages)
        record.setdefault("request", {"messages": [{"role": "system", "content": self.system}, *messages]})
        return text, reply, record

    def finish(self, observation: dict, text: str, reply: str, record: dict) -> Turn:
        """Turn a reply into orders against `observation`, the latest one, and remember the exchange."""
        actions, notes = self.parse(reply, observation)
        if not reply.strip():
            response = record.get("response") or {}
            choices = response.get("choices") or [{}]
            reason = response.get("stop_reason") or choices[0].get("finish_reason") or "unknown reason"
            notes.append(f"Macro returned no decision (completion ended: {reason}); existing orders continue.")
        self.conversation.append(text, reply)
        return Turn(text, reply, actions, notes, record)
