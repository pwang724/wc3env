"""WC3Env: the gym-style wrapper over the RPC in docs/design.md.

    env = WC3Env(GameConfig(map="(2)EchoIsles.w3x"))
    obs = env.reset()
    while not env.done:
        obs, done, info = env.step(agent.act(Observation.from_dict(obs)))

This convenience wrapper owns a GameSession with one agent. Use GameSession directly for
multiple agents: one batch per player, one shared step. reset() reloads the map and periodically
recycles the process according to GameConfig.max_episodes_per_process.
"""

from __future__ import annotations

from .protocol import Action, Observation, ProtocolError, normalize_actions
from .session import GameConfig, GameSession


class WC3Env:
    def __init__(self, config: GameConfig = GameConfig(), *, game_factory=None):
        if len(config.agent_slots) != 1:
            raise ValueError("WC3Env needs one agent; use GameSession for multiple agents")
        self.session = GameSession(config, game_factory=game_factory)
        self.player = config.agent_slots[0]

    @property
    def config(self) -> GameConfig:
        return self.session.config

    @property
    def observation(self) -> dict | None:
        return self.session.observations.get(self.player)

    @property
    def done(self) -> bool:
        return self.session.done

    @property
    def steps(self) -> int:
        return self.session.steps

    def reset(self) -> dict:
        return self.session.reset()[self.player]

    def step(self, actions: list[Action] | list[dict], seconds: float | None = None) -> tuple[dict, bool, dict]:
        """Send `actions`, then advance the game by `seconds` (default `config.step_ms`; rounded to 25 ms)."""
        ms = None if seconds is None else max(25, round(seconds * 1000 / 25) * 25)
        observations, done, info = self.session.step({self.player: actions}, ms)
        obs = observations[self.player]
        return obs, done, {**info, "rejected": info["rejected"][self.player], "ticks_skipped": obs["ticks_skipped"]}

    def debug(self, op: str, **args) -> dict:
        return self.session.debug(op, **args)

    def save_replay(self, path):
        return self.session.save_replay(path)

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def run_episode(env: WC3Env, agent, max_steps: int) -> list[dict]:
    """Drive `agent` (anything with act(Observation) -> list[Action]) for one episode."""
    if type(max_steps) is not int or max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")
    records = []
    obs = env.reset()
    for _ in range(max_steps):
        try:
            actions = normalize_actions(agent.act(Observation.from_dict(obs)))
            obs, done, info = env.step(actions)
            records.append({"observation": obs, "actions": [a.to_dict() for a in actions], "info": info})
        except ProtocolError as exc:
            records.append({"observation": obs, "actions": [], "error": str(exc)})
            obs, done, _ = env.step([])
        if done or getattr(agent, "finished", False):
            break
    return records
