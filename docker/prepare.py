"""Create a Docker context from this checkout and your own Warcraft installation."""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Keep in sync with wc3env.compatibility; packaging runs without the package installed.
EXE_SHA256 = "3f2ed0120d80578bf07e4423296dade1adfb959d59a2d20a7584224559570eed"
GAME_FILES = (
    "Warcraft III.exe",
    "Mss32.dll",
    "blizzard.ax",
    "War3.mpq",
    "War3x.mpq",
    "War3Patch.mpq",
    "War3Local.mpq",
    "War3xLocal.mpq",
    "Deprecated.mpq",
)
REQUIRED_GAME_FILES = ("Warcraft III.exe", "Mss32.dll", "War3.mpq", "War3x.mpq")
# Generated on the preparation machine; see the error messages below.
HOOK = "src/wc3env/native/wc3hook.dll"
PREPARED = ("wc3agent/src/wc3agent/game/data/reference.json",)
SOURCE_DIRS = ("src/wc3env", "wc3agent/src/wc3agent", "docker", "wc3hook")
SOURCE_SUFFIXES = (".py", ".json", ".md", ".sh", ".c", ".h", ".bat", ".txt")
TEXT_SUFFIXES = (".sh", ".py", ".json", ".bat", ".c", ".h")


def game_files(game_dir: Path) -> dict[str, Path]:
    """Allowed installation files and stock maps; activation files never qualify."""
    exe = game_dir / GAME_FILES[0]
    if not exe.is_file():
        raise FileNotFoundError(f"No Warcraft III.exe in {game_dir}")
    if hashlib.sha256(exe.read_bytes()).hexdigest() != EXE_SHA256:
        raise ValueError(f"Unsupported Warcraft installation: {exe}; use the Legacy TFT build described in README.md")
    missing = [name for name in REQUIRED_GAME_FILES if not (game_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing Warcraft installation files: " + ", ".join(missing))
    paths = [game_dir / name for name in GAME_FILES if (game_dir / name).is_file()]
    paths += sorted(p for p in (game_dir / "Maps").rglob("*") if p.is_file() and p.suffix.lower() in (".w3m", ".w3x"))
    return {"assets/game/" + p.relative_to(game_dir).as_posix(): p for p in paths}


def prepare(output: Path, game_dir: Path, root: Path = ROOT) -> int:
    root, output = Path(root).resolve(), Path(output).resolve()
    sources = game_files(Path(game_dir).expanduser().resolve())
    if not (root / HOOK).is_file():
        raise FileNotFoundError(f"Missing {HOOK}; run wc3hook/build.bat")
    for name in PREPARED:
        if not (root / name).is_file():
            raise FileNotFoundError(f"Missing {name}; run python -m tools.prepare reference")
    names = ["pyproject.toml", "setup.py", "README.md", "LICENSE", HOOK, *PREPARED, "wc3hook/yyjson/LICENSE"]
    for package in ("wc3agent",):
        names += [f"{package}/{name}" for name in ("pyproject.toml", "README.md", "LICENSE")]
    for directory in SOURCE_DIRS:
        names += [
            p.relative_to(root).as_posix()
            for p in (root / directory).rglob("*")
            if p.is_file()
            and p.suffix in SOURCE_SUFFIXES
            and not set(p.relative_to(root).parts) & {"__pycache__", "out"}
        ]
    sources.update({name: root / name for name in names})
    sources["Dockerfile"] = root / "docker/Dockerfile"
    output.mkdir(parents=True, exist_ok=False)
    for name, source in sorted(sources.items()):
        dest = output / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in TEXT_SUFFIXES or source.name == "Dockerfile":
            dest.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
        else:
            shutil.copyfile(source, dest)
    return len(sources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game-dir", type=Path, required=True, help="Path to your supported Warcraft III Legacy installation"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "build/docker-context")
    args = parser.parse_args()
    print(f"Docker context: {args.output} ({prepare(args.output, args.game_dir)} files; no license files)")
