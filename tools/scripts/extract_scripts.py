"""Extract common.j / Blizzard.j from the legacy MPQs shipped alongside WC3 1.29."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.prepare.mpq import Archive  # noqa: E402
from wc3env.settings import settings  # noqa: E402

BASE = str(settings().game_dir)
OUT = Path(__file__).resolve().parents[1] / "jass"
OUT.mkdir(exist_ok=True)

for m in ["War3Patch.mpq", "War3x.mpq", "War3.mpq", "Deprecated.mpq"]:
    p = os.path.join(BASE, m)
    if not os.path.exists(p):
        continue
    try:
        with Archive(p) as a:
            for f in ["Scripts\\common.j", "Scripts\\Blizzard.j"]:
                if a.has(f):
                    d = a.read(f)
                    out = OUT / f.split("\\")[-1]
                    if not out.exists():
                        out.write_bytes(d)
                    print(m, f, len(d))
    except Exception as e:  # noqa: BLE001
        print(m, "ERR", e)
