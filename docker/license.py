"""Attach user-supplied activation files at runtime, without logging their contents."""

import os
from pathlib import Path


def attach(game_dir, license_dir):
    game_dir, license_dir = Path(game_dir), Path(license_dir)
    names = ("roc.w3k", "tft.w3k")
    missing = [name for name in names if not (license_dir / name).is_file() or (license_dir / name).stat().st_size == 0]
    if missing:
        raise ValueError(
            "Missing Warcraft license files: "
            + ", ".join(missing)
            + ". Mount a directory containing your own roc.w3k and tft.w3k at /run/wc3-license (read-only)."
        )
    for name in names:
        source, target = (license_dir / name).resolve(), game_dir / name
        if target.is_symlink():
            if target.resolve() == source:
                continue
            raise ValueError("Unexpected existing license link: " + name)
        if target.exists():
            raise ValueError("License file was baked into the game image: " + name)
        target.symlink_to(source)


if __name__ == "__main__":
    try:
        attach("/opt/game", os.environ.get("WC3_LICENSE_DIR", "/run/wc3-license"))
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
