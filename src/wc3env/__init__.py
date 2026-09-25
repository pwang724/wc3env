"""Gym-style environment for Warcraft III Legacy; see README.md for setup and use."""

from .env import WC3Env
from .pool import StepPool
from .protocol import Action, Observation, ProtocolError
from .session import GameConfig, GameSession, MatchSetup, PlayerConfig

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
