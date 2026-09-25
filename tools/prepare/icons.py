"""Write every unit's command-card icon from the installed game to build/icons/<type id>.png.

The replay panel (wc3agent.replay) shows these beside each unit group. Game art stays local (build/ is
not tracked). Run once per installation:

    python -m tools.prepare.icons
"""

import io
from pathlib import Path

from PIL import Image

from .gamedata import read_all
from .source_catalog import SourceCatalog

OUT = Path(__file__).resolve().parents[2] / "build" / "icons"


def main():
    catalog = SourceCatalog.load()
    art = {raw: prof.get("Art", "").split(",")[0] for raw, prof in catalog.unit_functions.items() if prof.get("Art")}
    files = read_all(sorted(set(art.values())))
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for raw, path in art.items():
        data = files.get(path)
        if not data:
            continue
        try:
            Image.open(io.BytesIO(data.encode("latin-1"))).convert("RGB").save(OUT / f"{raw}.png")
            written += 1
        except OSError:  # an image format Pillow cannot read
            continue
    print(f"{written} unit icons in {OUT}")


if __name__ == "__main__":
    main()
