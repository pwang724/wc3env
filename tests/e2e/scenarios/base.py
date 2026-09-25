"""A small step-machine for scripted verification scenarios.

A scenario is an ordered list of Steps. Each step issues its actions once when entered and
completes when its `done` predicate holds; a step that exceeds `timeout` ticks fails (and
the scenario continues with the next step, so one failure does not hide the others).
Results are collected as checks the runner writes into the local report.

A step's `act` returns protocol Actions and, for staging, `Stage` ops (the server's `debug`
method: spawn, kill, give, ...), which the runner sends before the actions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from wc3env.protocol import Action, Observation


@dataclass(frozen=True)
class Stage:
    """A staging op for the server's `debug` method: test state, not an agent action."""

    op: str
    args: dict


def stage(op: str, **args) -> Stage:
    return Stage(op, args)


class View:
    """Convenience accessors over the observation payload for scenario code."""

    def __init__(self, observation: Observation, seen_events: list[dict]):
        self.obs = observation
        self.p = observation.payload
        self.tick = observation.sequence
        self.gold = self.p["player"]["gold"]
        self.lumber = self.p["player"]["lumber"]
        self.food_used = self.p["player"]["food_used"]
        self.food_cap = self.p["player"]["food_cap"]
        self.units = self.p["units"]
        self.enemies = self.p["visible_enemies"]
        self.events = self.p["events"]
        self.seen_events = seen_events
        self.result = self.p.get("result", "")

    def own(self, type_id: str) -> list[dict]:
        return [u for u in self.units if u["type_id"] == type_id]

    def first(self, type_id: str) -> dict | None:
        found = self.own(type_id)
        return found[0] if found else None

    def visible(self, type_id: str) -> list[dict]:
        return [u for u in self.enemies if u["type_id"] == type_id]

    def happened(self, kind: str, **fields) -> bool:
        """True if an event of `kind` with these field values has been seen at any tick."""
        return any(e["kind"] == kind and all(e.get(k) == v for k, v in fields.items()) for e in self.seen_events)

    def nearest(self, x: float, y: float, candidates: list[dict]) -> dict | None:
        return min(candidates, key=lambda u: (u["x"] - x) ** 2 + (u["y"] - y) ** 2, default=None)


@dataclass
class Step:
    name: str
    act: Callable[[View], list[Action | Stage]]
    done: Callable[[View], bool]
    timeout: int = 90  # ticks before the step fails
    every: int | None = None  # re-run `act` every N ticks while waiting (retries)
    skip: Callable[[View], bool] | None = None  # skip the step (status "skipped") when true at entry


@dataclass
class Check:
    step: str
    status: str  # "pass" | "fail" | "skipped"
    started_tick: int
    finished_tick: int | None = None
    note: str = ""


@dataclass
class ScenarioAgent:
    """Base class: subclasses fill `steps` in __init__. Implements the Agent protocol."""

    steps: list[Step] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    seen_events: list[dict] = field(default_factory=list)
    _index: int = 0
    _entered_tick: int | None = None

    @property
    def finished(self) -> bool:
        return self._index >= len(self.steps)

    def act(self, observation: Observation) -> list[Action | Stage]:
        self.seen_events.extend(dict(e, tick=observation.sequence) for e in observation.payload["events"])
        view = View(observation, self.seen_events)
        actions: list[Action | Stage] = []
        while not self.finished:
            step = self.steps[self._index]
            if self._entered_tick is None:
                self._entered_tick = view.tick
                self.checks.append(Check(step.name, "running", view.tick))
                if step.skip and step.skip(view):
                    self._close("skipped", view.tick, "skip condition held")
                    continue
                actions.extend(step.act(view))
                break  # give the game a tick to react before testing `done`
            if step.done(view):
                self._close("pass", view.tick)
                continue  # enter the next step in the same tick
            elapsed = view.tick - self._entered_tick
            if elapsed > step.timeout:
                self._close("fail", view.tick, f"timeout after {step.timeout} ticks")
                continue
            if step.every and elapsed % step.every == 0:
                actions.extend(step.act(view))
            break
        return actions

    def _close(self, status: str, tick: int, note: str = "") -> None:
        self.checks[-1].status = status
        self.checks[-1].finished_tick = tick
        self.checks[-1].note = note
        self._index += 1
        self._entered_tick = None

    def summary(self) -> dict:
        return {
            "passed": all(c.status in ("pass", "skipped") for c in self.checks) and self.finished,
            "checks": [c.__dict__ for c in self.checks],
        }
