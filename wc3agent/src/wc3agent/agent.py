"""Observation in, one valid action batch out.

Macro owns strategic history; Micro owns recent combat history. Macro calls run in
one background worker. Stepped play waits for the reply; realtime play checks again next step.
The environment owns game time. Call submitted() after sending the returned actions.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty, SimpleQueue
from threading import Event

from .game.featurize import describe, system_prompt
from .game.orders import Orders
from .game.policies import GOLD_PER_MINE, gold_overflow, skill_to_learn, unassigned_fighters
from .game.workers import MINES, harvest_kinds
from .macro.agent import MacroAgent
from .macro.conversation import Conversation
from .macro.memory import MacroMemory
from .macro.prompts import HUMAN_FEEDBACK
from .micro.agent import MicroAgent

MIN_TURN_SECONDS = 5.0


def check_turn_seconds(value, name="turn_seconds"):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < MIN_TURN_SECONDS
    ):
        raise ValueError(f"{name} must be a finite number of at least {MIN_TURN_SECONDS:g}")


@dataclass
class Step:
    """Actions ready for the environment, plus model call records for the runner."""

    actions: list[dict] = field(default_factory=list)
    turns: list[int | None] = field(default_factory=list)  # macro turn behind each action; None for micro
    records: list[dict] = field(default_factory=list)
    macro_turn: int | None = None  # the macro reply that landed in this step

    def add(self, actions, turns):
        self.actions += actions
        self.turns += turns


class Agent:
    def __init__(
        self,
        catalog,
        mapinfo,
        model,
        *,
        micro_model,
        micro_key,
        transcript=None,
        goal="",
        turn_seconds=MIN_TURN_SECONDS,
        micro_call_limit=None,
    ):
        check_turn_seconds(turn_seconds)
        self.macro_memory = MacroMemory(catalog, mapinfo)
        self.model, self.turn_seconds = model, turn_seconds
        self.conversation = Conversation(transcript, pinned=f"THIS GAME IS A TEST OF ONE THING. {goal}" if goal else "")
        self.orders = Orders(self.macro_memory)
        self.ready = Event()
        self.micro = MicroAgent(
            catalog,
            model=micro_model,
            key=micro_key,
            ready=self.ready,
            call_limit=micro_call_limit,
            references=self.macro_memory.references,
        )
        self.macro = None  # race and home become known from the first observation
        self.thinker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="macro")
        self.thinking = None  # (future, requested game time, feedback included in that request)
        self.next_turn = 0.0
        self.awaiting_submission = None  # landed macro turn whose orders have not been acknowledged
        self.turns = 0
        self.feedback = SimpleQueue()  # lines a watching human typed, for the next macro request

    def tell(self, text):
        """Human feedback: the next macro request starts as soon as none is in flight, and includes it."""
        self.feedback.put(text)
        self.ready.set()

    def heard(self):
        lines = []
        while True:
            try:
                lines.append(self.feedback.get_nowait())
            except Empty:
                return lines

    def close(self):
        try:
            self.micro.close()
        finally:
            self.thinker.shutdown(wait=False, cancel_futures=True)

    def act(self, obs, wait=False) -> Step:
        self.ready.clear()
        memory = self.macro_memory
        memory.update(obs)
        step = Step()
        if (
            not self.thinking
            and self.awaiting_submission is None
            and (obs["game_time_seconds"] >= self.next_turn or not self.feedback.empty())
            and not obs["result"]
        ):
            macro = self.macro_agent()
            text = describe(memory, obs)
            if heard := self.heard():
                text += "\n\n" + HUMAN_FEEDBACK.format(text="\n".join(heard))
            told = memory.consume()
            self.thinking = (self.thinker.submit(macro.think, text), obs["game_time_seconds"], told)
            self.next_turn = obs["game_time_seconds"] + self.turn_seconds
            self.thinking[0].add_done_callback(lambda _: self.ready.set())
        if self.thinking and (wait or self.thinking[0].done()):
            self.land(obs, step)
        released, notes, turns = self.orders.release(obs)
        memory.notes.extend(notes)
        self.adopt_idle_fighters(obs)
        step.add(released, turns)
        # Ready macro orders reach the game before micro decides again from this observation.
        micro, records = self.micro.act(
            obs, memory.control, memory.hall, memory.tech, wait=wait, start=not step.actions
        )
        memory.fighting.update(r["group"] for r in records)
        step.add(micro, [None] * len(micro))
        step.records.extend(records)
        return step

    def adopt_idle_fighters(self, obs):
        """Idle fighters in no group join the army with our heroes, under micro (policies.unassigned_fighters)."""
        memory = self.macro_memory
        for uid, group in unassigned_fighters(obs["units"], memory.control.groups, memory.catalog).items():
            memory.control.groups[group]["ids"].add(uid)
            memory.control.touch([uid])
            memory.notes.append(f"Code added idle {memory.references.name(uid)} to group {group}.")

    def spend_skill_points(self, obs, actions):
        """Learn one skill for each hero whose points this macro turn left unspent."""
        memory, learns = self.macro_memory, []
        for hero in (u for u in obs["units"] if u["hero"] and u["hp"] > 0):
            learned = dict(memory.skills.get(hero["unit_id"], {}))
            for a in actions:
                if a["unit_id"] == hero["unit_id"] and a["command"] == "learn":
                    learned[a["arguments"]["ability_id"]] = learned.get(a["arguments"]["ability_id"], 0) + 1
            raw = skill_to_learn(memory.catalog, hero, learned)
            if raw:
                learns.append({"unit_id": hero["unit_id"], "command": "learn", "arguments": {"ability_id": raw}})
                name = memory.catalog.abilities[raw]["levels"]["1"]["name"]
                memory.notes.append(
                    f"Code spent an unspent skill point: {memory.references.name(hero)} learned {name}."
                )
        return learns

    def cap_gold_workers(self, obs, actions):
        """Send gold workers beyond GOLD_PER_MINE on a mine to the nearest trees (policies.gold_overflow).
        Workers this macro turn ordered, grouped workers and workers with deferred orders stay put."""
        memory = self.macro_memory
        gathering = ("harvest", "resumeharvesting", "returnresources")
        mines = {u["unit_id"]: u for u in obs["units"] + obs["visible_enemies"] if u["type_id"] in MINES}
        ordered = {a["unit_id"]: a for a in actions}
        grouped = set().union(*(g["ids"] for g in memory.control.groups.values()))
        outside = {u["unit_id"]: u for u in obs["units"]}
        miners, walking = {}, set()
        for w in obs["units"] + obs.get("inside", []):
            uid, order = w["unit_id"], w.get("order") or {}
            if uid in ordered:
                target = ordered[uid]["arguments"].get("target_id")
                if ordered[uid]["command"] == "harvest" and target in mines:
                    miners.setdefault(target, []).append(uid)
                continue
            mine = memory.gold_mine.get(uid)
            if memory.gathering.get(uid) != "gold" or order.get("name") not in gathering or mine not in mines:
                continue
            miners.setdefault(mine, []).append(uid)
            if (
                uid in outside
                and order["name"] != "returnresources"
                and order.get("target_id") == mine
                and uid not in grouped
                and uid not in memory.control.deferred
                and harvest_kinds(outside[uid])[1]
            ):
                walking.add(uid)
        trees = [d for d in obs["destructables"] if d["resource"] == "lumber" and d["hp"] > 0]
        moves = []
        for uid in gold_overflow(miners, walking) if trees else []:
            w = outside[uid]
            tree = min(trees, key=lambda d: math.hypot(d["x"] - w["x"], d["y"] - w["y"]))
            moves.append({"unit_id": uid, "command": "harvest", "arguments": {"target_id": tree["id"]}})
            mine = mines[memory.gold_mine[uid]]
            memory.notes.append(
                f"Code sent {memory.references.name(w)} to lumber: {memory.references.name(mine)} already had "
                f"{GOLD_PER_MINE} gold workers."
            )
        return moves

    def macro_agent(self):
        if self.macro is None:
            self.macro = MacroAgent(
                self.model,
                system_prompt(self.macro_memory),
                parse=lambda text, obs: self.orders.parse(text, obs, turn=self.turns + 1),
                conversation=self.conversation,
            )
        return self.macro

    def land(self, obs, step):
        """Parse the macro reply against the latest observation; parsing updates Control."""
        future, asked_at, told = self.thinking
        self.thinking = None
        turn = self.macro_agent().finish(obs, *future.result())
        self.macro_memory.notes.extend(turn.notes)
        self.turns += 1
        self.awaiting_submission = step.macro_turn = self.turns
        step.add(turn.actions, [self.turns] * len(turn.actions))
        learns = self.spend_skill_points(obs, turn.actions)
        step.add(learns, [None] * len(learns))
        moves = self.cap_gold_workers(obs, turn.actions)
        step.add(moves, [None] * len(moves))
        record = {
            "kind": "macro",
            "model": getattr(self.model, "model", type(self.model).__name__),
            "turn": self.turns,
            "at_game_time": asked_at,
            "landed_at_game_time": obs["game_time_seconds"],
            "latency_ms": round((turn.record.get("seconds", 0.0) - turn.record.get("waited", 0.0)) * 1000),
            "messages_sent": len(turn.record["request"]["messages"]),
            "usage": turn.record.get("usage", {}),
            "request": turn.record["request"],
            "response": turn.record.get("response"),
            "observation": turn.observation,
            "reply": turn.reply,
            "actions": turn.actions,
            "problems": turn.notes,
            "told": told,
        }
        if self.turns == 1:
            record.update(system_prompt=self.macro.system, pinned=self.conversation.pinned)
        step.records.append(record)

    def submitted(self, step, rejected, game_time):
        """Record the batch's actual outcome after the environment accepts or rejects its orders."""
        refused = {r["index"] for r in rejected}
        accepted = [a for i, a in enumerate(step.actions) if i not in refused]
        self.macro_memory.submitted(step.actions, game_time, step.turns, rejected)
        self.micro.memory.record(game_time, accepted)
        if step.macro_turn is not None and step.macro_turn == self.awaiting_submission:
            self.awaiting_submission = None
