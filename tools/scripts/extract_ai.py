"""Extract the built-in melee AI scripts (common.ai, human.ai, orc.ai) from the legacy War3x.mpq."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.prepare.mpq import Archive  # noqa: E402
from wc3env.settings import settings  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "jass"
OUT.mkdir(exist_ok=True)
with Archive(settings().game_dir / "War3x.mpq") as a:
    for name in ["common.ai", "human.ai", "orc.ai", "elf.ai", "undead.ai"]:
        key = "Scripts\\" + name
        if a.has(key):
            data = a.read(key)
            (OUT / name).write_bytes(data)
            print(name, len(data))
