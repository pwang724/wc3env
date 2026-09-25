"""Machine-specific settings, read from environment variables (optionally via a `.env` file).

    WC3_GAME_DIR   Warcraft III install folder        default: C:\\Program Files (x86)\\Warcraft III (Legacy)
    WC3_USER_DIR   Warcraft III user data folder     default: %LOCALAPPDATA%/Warcraft III
                   (holds Maps/, CustomMapData/, Replay/)
    WC3_OUTPUT_DIR Optional parent for isolated process output directories.
                   Overrides the legacy sibling location derived from WC3_USER_DIR.
    WC3_MAP        the stock map to launch, relative to WC3_GAME_DIR
                                                     default: Maps/FrozenThrone/(2)EchoIsles.w3x
    WC3_SOUND      1 for sound (default), 0 for silent automation

Read `.env` in the source checkout, then in the current directory, then the real environment
(last wins). Files use KEY=VALUE lines and `#` comments. See `.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    game_dir: Path
    user_dir: Path
    map: Path
    hook_dll: Path | None = None
    output_dir: Path | None = None
    sound: bool = True

    @property
    def game_exe(self) -> Path:
        return self.game_dir / "Warcraft III.exe"


@lru_cache(maxsize=1)
def settings() -> Settings:
    source_env = _read_dotenv(ROOT / ".env") if (ROOT / "pyproject.toml").is_file() else {}
    env = {**source_env, **_read_dotenv(Path.cwd() / ".env"), **os.environ}
    game_dir = Path(env.get("WC3_GAME_DIR", r"C:\Program Files (x86)\Warcraft III (Legacy)"))
    local_data = Path(env.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    user_dir = Path(env.get("WC3_USER_DIR", str(local_data / "Warcraft III"))).expanduser().resolve()
    map = game_dir / env.get("WC3_MAP", "Maps/FrozenThrone/(2)EchoIsles.w3x")
    hook = env.get("WC3_HOOK_DLL")
    output = env.get("WC3_OUTPUT_DIR")
    sound = env.get("WC3_SOUND", "1")
    if sound not in ("0", "1"):
        raise ValueError("WC3_SOUND must be 0 or 1")
    return Settings(
        game_dir=game_dir,
        user_dir=user_dir,
        map=map,
        hook_dll=Path(hook).expanduser().resolve() if hook else None,
        output_dir=Path(output).expanduser().absolute() if output else None,
        sound=sound == "1",
    )
