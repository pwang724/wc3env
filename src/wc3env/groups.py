"""Named, per-player control groups expanded into normal validated actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .protocol import Action, ProtocolError, normalize_actions

if TYPE_CHECKING:
    from .session import GameSession


@dataclass(frozen=True)
class ControlGroups:
    _session: GameSession
    player: int

    def assign(self, name: str, unit_ids) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("group name must be a nonempty string")
        ids = tuple(unit_ids)
        if any(type(uid) is not int for uid in ids):
            raise ProtocolError("group members must be integer unit IDs")
        with self._session._lock:
            with self._session._state_lock:
                self._session._check_open()
            obs = self._session.observations.get(self.player)
            if obs is None:
                raise RuntimeError("call reset() before assigning groups")
            own = {u["unit_id"] for u in obs["units"]}
            if not set(ids) <= own:
                raise ProtocolError("group members must be currently observed own units")
            self._session._groups[self.player][name] = tuple(dict.fromkeys(ids))

    def members(self, name: str) -> tuple[int, ...]:
        with self._session._lock:
            with self._session._state_lock:
                self._session._check_open()
            ids = self._session._groups[self.player][name]
            own = {u["unit_id"] for u in self._session.observations.get(self.player, {}).get("units", [])}
            # Keep stored IDs: workers may temporarily disappear inside mines/transports.
            return tuple(uid for uid in ids if uid in own)

    def actions(self, name: str, command: str, arguments: dict | None = None) -> list[Action]:
        """One command per currently observed member, in group order."""
        return normalize_actions(
            [Action(uid, command, arguments if arguments is not None else {}) for uid in self.members(name)]
        )

    def clear(self) -> None:
        with self._session._lock:
            with self._session._state_lock:
                self._session._check_open()
            self._session._groups[self.player].clear()
