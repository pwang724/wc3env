"""Observation/action contract, ownership validation, and independent JSON action snapshots."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_JSON_DEPTH = 64
QUEUABLE_COMMANDS = frozenset(("move", "stop", "attack", "smart", "harvest", "build", "cast", "use_item"))
SCORE_FIELDS = (
    "units_trained",
    "units_killed",
    "structures_built",
    "structures_razed",
    "tech_percent",
    "food_max_produced",
    "food_max_used",
    "heroes_killed",
    "items_gained",
    "mercenaries_hired",
    "gold_mined",
    "gold_mined_upkeep",
    "gold_lost_upkeep",
    "gold_lost_tax",
    "gold_given",
    "gold_received",
    "lumber_total",
    "lumber_lost_upkeep",
    "lumber_lost_tax",
    "lumber_given",
    "lumber_received",
    "unit_total",
    "hero_total",
    "resource_total",
    "total",
)


class ProtocolError(ValueError):
    """The agent or the game violated the protocol contract."""


@dataclass(frozen=True)
class Observation:
    sequence: int
    game_time_seconds: float
    unit_ids: frozenset[int]  # the observer's own units: what it may command
    payload: dict[str, Any]

    @classmethod
    def from_dict(cls, message: dict[str, Any]) -> Observation:
        if message.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError(
                f"unsupported protocol version {message.get('protocol_version')!r}; expected {PROTOCOL_VERSION}"
            )
        return cls(
            sequence=int(message["sequence"]),
            game_time_seconds=float(message["game_time_seconds"]),
            unit_ids=frozenset(int(u["unit_id"]) for u in message["units"]),
            payload=message,
        )


@dataclass(frozen=True)
class Action:
    unit_id: int
    command: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"unit_id": self.unit_id, "command": self.command, "arguments": self.arguments}


def validate_actions(observation: Observation, actions: list[Action]) -> None:
    for action in actions:
        queued = action.arguments.get("queued", False)
        if type(queued) is not bool or (queued and action.command not in QUEUABLE_COMMANDS):
            raise ProtocolError("queued must be a bool and is only supported for unit orders")
        if "auto_place" in action.arguments and (
            type(action.arguments["auto_place"]) is not bool or action.command != "build"
        ):
            raise ProtocolError("auto_place must be a bool and is only supported for build")
        if action.unit_id not in observation.unit_ids:
            raise ProtocolError(f"agent attempted to command uncontrolled unit {action.unit_id}")


def _copy_json(value: Any, depth: int) -> Any:
    """Copy JSON values without coercion; bound recursion (including cyclic inputs)."""
    kind = type(value)
    if value is None or kind is bool:
        return value
    if kind is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProtocolError("JSON strings must contain valid Unicode") from exc
        return value
    if kind is int and -(2**63) <= value < 2**63:
        return value
    if kind is float and math.isfinite(value):
        return value
    if kind in (list, dict):
        if depth > MAX_JSON_DEPTH:
            raise ProtocolError("action arguments exceed the JSON nesting limit or contain a cycle")
        if kind is list:
            return [_copy_json(v, depth + 1) for v in value]
        if any(type(k) is not str for k in value):
            raise ProtocolError("JSON object keys must be strings")
        return {_copy_json(k, depth): _copy_json(v, depth + 1) for k, v in value.items()}
    raise ProtocolError("arguments must be JSON values with finite numbers and signed 64-bit integers")


def normalize_actions(actions: list[Action] | list[dict]) -> list[Action]:
    """Validate and snapshot a complete batch before any commands reach the game."""
    if not isinstance(actions, list):
        raise ProtocolError("each player's batch must be a list of actions")
    batch = []
    for action in actions:
        a = action.to_dict() if isinstance(action, Action) else action
        if (
            not isinstance(a, dict)
            or type(a.get("unit_id")) is not int
            or type(a.get("command")) is not str
            or type(a.get("arguments", {})) is not dict
        ):
            raise ProtocolError("actions must have integer unit_id, string command and object arguments")
        # On the wire: root object, params, actions array, action object, arguments.
        batch.append(Action(a["unit_id"], _copy_json(a["command"], 5), _copy_json(a.get("arguments", {}), 5)))
    return batch
