"""Record tests/fixtures/combat.w3g: two agents build, train heroes, cast and fight, with no staging.

    python -m tests.e2e.extended.record_combat_replay

Debug mutations are not replay commands, so everything here is an ordinary order. Both players
mine, build an altar, train a summoning hero, learn its summon and attack-move at each other's
base; the heroes meet on the way. The map decides the races, so the script reads its race off
its town hall. The script prints the digest of the live state trace, which
combat_replay.py pins: playback must reproduce the recorded game second by second.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.e2e.support import state
from wc3env import GameConfig, GameSession, PlayerConfig

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "combat.w3g"
PLAYERS = (0, 1)
MAX_SECONDS = 600
# hall: worker, altar, hero, ability, its order, summoned type prefix (None: the spell targets an enemy unit)
RACES = {
    "htow": ("hpea", "halt", "Hamg", "AHwe", "waterelemental", "hwa"),
    "ogre": ("opeo", "oalt", "Ofar", "AOsf", "spiritwolf", "osw"),
    "unpl": ("uaco", "uaod", "Ulic", "AUfn", "frostnova", None),
}
MINES = ("ngol", "ugol", "egol")
ALTAR_SPOTS = ((0, -600), (0, 600), (-600, 0), (600, 0))  # tried in turn until a site is accepted


def digest(trace) -> str:
    return hashlib.sha256(json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Commander:
    """One player's script, driven only by its own observations."""

    def __init__(self):
        self.done = set()
        self.kinds = set()
        self.seconds = 0

    def once(self, key) -> bool:
        if key in self.done:
            return False
        self.done.add(key)
        return True

    def act(self, obs) -> list[dict]:
        self.kinds |= {e["kind"] for e in obs["events"]}
        self.seconds += 1
        units = obs["units"]
        hall = next((u for u in units if u["type_id"] in RACES), None)
        if hall is None:
            return []
        worker_type, altar_type, hero_type, ability, order, summon = RACES[hall["type_id"]]
        peasants = [u for u in units if u["type_id"] == worker_type]
        altar = next((u for u in units if u["type_id"] == altar_type), None)
        hero = next((u for u in units if u["hero"]), None)
        orders = []
        if hall and self.once("economy"):
            mine = min(
                (u for u in units + obs["visible_enemies"] if u["type_id"] in MINES),
                key=lambda u: (u["x"] - hall["x"]) ** 2 + (u["y"] - hall["y"]) ** 2,
            )
            orders += [
                {"unit_id": p["unit_id"], "command": "smart", "arguments": {"target_id": mine["unit_id"]}}
                for p in peasants[:-1]
            ]
            self.builder = peasants[-1]["unit_id"]
            self.enemy_base = (-hall["x"], hall["y"])  # Echo Isles mirrors the two bases left to right
        if hall and "construct_start" not in self.kinds and self.seconds % 8 == 1:
            dx, dy = ALTAR_SPOTS[(self.seconds // 8) % len(ALTAR_SPOTS)]
            spot = {"type_id": altar_type, "x": hall["x"] + dx, "y": hall["y"] + dy}
            orders.append({"unit_id": self.builder, "command": "build", "arguments": spot})
        if altar and "construct_finish" in self.kinds and obs["player"]["gold"] >= 425 and self.once("hero"):
            orders.append({"unit_id": altar["unit_id"], "command": "train", "arguments": {"type_id": hero_type}})
        if hero and self.once("learn"):
            orders.append({"unit_id": hero["unit_id"], "command": "learn", "arguments": {"ability_id": ability}})
        elif hero and "hero_learn" in self.kinds and self.once("march"):
            x, y = self.enemy_base
            orders.append({"unit_id": hero["unit_id"], "command": "attack", "arguments": {"x": x, "y": y}})
        elif (
            hero
            and obs["visible_enemies"]
            and hero["mana"] >= 125
            and "hero_learn" in self.kinds
            and "spell_effect" not in self.kinds
        ):
            foes = [e for e in obs["visible_enemies"] if not e["structure"] and e["owner"] in PLAYERS]
            if foes:
                arguments = {"order": order} if summon else {"order": order, "target_id": foes[0]["unit_id"]}
                orders.append({"unit_id": hero["unit_id"], "command": "cast", "arguments": arguments})
                self.done.discard("resume")
        elif hero and "spell_effect" in self.kinds and self.once("resume"):
            x, y = self.enemy_base
            army = [u for u in units if u["hero"] or (summon and u["type_id"].startswith(summon))]
            orders += [{"unit_id": u["unit_id"], "command": "attack", "arguments": {"x": x, "y": y}} for u in army]
        return orders


def record() -> tuple[str, int, dict]:
    commanders = {p: Commander() for p in PLAYERS}
    config = GameConfig(players=tuple(PlayerConfig(p) for p in PLAYERS), render=False)
    with GameSession(config) as session:
        observations = session.reset()
        session.game.rpc.debug("speed", factor=64)
        session.game.rpc.debug("waitfloor", ms=1)
        trace = [state(observations)]
        for _ in range(MAX_SECONDS):
            observations, done, info = session.step({p: commanders[p].act(observations[p]) for p in PLAYERS})
            assert not any(info["rejected"].values()), info["rejected"]
            trace.append(state(observations))
            fought = all({"spell_effect", "death"} <= c.kinds or done for c in commanders.values())
            if done or fought:
                break
        FIXTURE.unlink(missing_ok=True)
        session.game.rpc.save_replay(str(FIXTURE))
    return digest(trace), len(trace) - 1, {p: sorted(c.kinds) for p, c in commanders.items()}


if __name__ == "__main__":
    value, seconds, kinds = record()
    print(f"{FIXTURE} ({FIXTURE.stat().st_size} bytes), {seconds} seconds")
    print("events seen:", kinds)
    print("digest:", value)
