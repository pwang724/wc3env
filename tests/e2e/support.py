"""Shared native-game trace normalization."""


def state(observations):
    # Object IDs are process-local. Compare gameplay values and every native score counter.
    return tuple(
        (
            o["game_time_seconds"],
            o["player"],
            o["score"],
            o["result"],
            sorted(tuple(u[k] for k in ("type_id", "owner", "x", "y", "hp", "mana", "level")) for u in o["units"]),
        )
        for o in observations.values()
    )
