"""The macro model's conversation: every observation and reply, appended; only the last few turns are re-read.

Each observation is a full snapshot of the game, so older turns add little but the model's own earlier plans.
Sending the whole game grew a request to 264k tokens by minute 12, when play also got worse."""

from __future__ import annotations

from pathlib import Path

CHARS_PER_TOKEN = 4  # a planning estimate; providers count differently
KEEP_TURNS = 5  # past turns (observation and reply) kept when the history is cut back
MAX_TURNS = 10  # the history is cut back once it reaches this many turns
# Cutting in steps rather than a turn every time keeps the conversation's start, and the prompt cache, fixed
# between cuts.


class Conversation:
    def __init__(
        self,
        transcript: Path | None = None,
        token_limit: int = 200_000,
        keep_turns: int = KEEP_TURNS,
        max_turns: int = MAX_TURNS,
        pinned: str = "",
    ):
        self.turns: list[tuple[str, str]] = []  # (observation text, reply text), whole game
        self.first = 0  # the oldest turn still sent to the model
        self.transcript, self.token_limit, self.keep_turns, self.max_turns = (
            transcript,
            token_limit,
            keep_turns,
            max_turns,
        )
        # A message that opens the conversation and stays when old turns are cut: the scenario's goal.
        self.pinned = pinned
        if transcript:
            transcript.parent.mkdir(parents=True, exist_ok=True)

    def _tokens(self, extra=""):
        sent = self.turns[self.first :]
        return (sum(len(o) + len(r) for o, r in sent) + len(extra) + len(self.pinned)) // CHARS_PER_TOKEN

    def messages(self, observation: str) -> list[dict]:
        """The recent turns (between keep_turns and max_turns) plus this observation, fewer if they would pass
        the token limit."""
        if len(self.turns) - self.first >= self.max_turns:
            self.first = len(self.turns) - self.keep_turns
        while self._tokens(observation) > self.token_limit and self.first < len(self.turns):
            self.first += 1
        messages = (
            [{"role": "user", "content": self.pinned}, {"role": "assistant", "content": "Understood."}]
            if self.pinned
            else []
        )
        for seen, reply in self.turns[self.first :]:
            messages += [{"role": "user", "content": seen}, {"role": "assistant", "content": reply}]
        return [*messages, {"role": "user", "content": observation}]

    def append(self, observation: str, reply: str) -> None:
        self.turns.append((observation, reply))
        if self.transcript:
            with self.transcript.open("a", encoding="utf-8") as stream:
                if len(self.turns) == 1 and self.pinned:
                    stream.write(f"===== PINNED (sent first, every turn) =====\n{self.pinned}\n")
                stream.write(f"\n===== TURN {len(self.turns)} =====\n{observation}\n\n----- REPLY -----\n{reply}\n")
