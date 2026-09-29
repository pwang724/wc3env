"""Gym-style environment for Warcraft III Legacy; see README.md for setup and use."""

from .env import WC3Env
from .protocol import Action, Observation, ProtocolError
from .session import GameConfig, GameSession, MatchSetup, PlayerConfig


def __getattr__(name: str):
    # StepPool launches games itself, which needs Windows: loading it lazily lets the rest of the
    # package (a Linux host's VectorSession) import anywhere.
    if name == "StepPool":
        from .pool import StepPool

        return StepPool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Action",
    "GameConfig",
    "GameSession",
    "MatchSetup",
    "Observation",
    "PlayerConfig",
    "ProtocolError",
    "StepPool",
    "WC3Env",
]
