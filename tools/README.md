# Tools

| Location | Responsibility |
|---|---|
| `prepare/` | Extract the reference JSON and map descriptions from your installation |
| `scripts/` | Extract the game's AI and JASS scripts for research; measure in the game how each item is aimed |
| `disasm.py`, `imports.py`, `natives.py` | Executable inspection for hook development |
| `check_wheel.py`, `check_agent_wheel.py` | Installed-wheel validation used by CI |
| `check_compatibility.py` | Local compatibility and scale matrix behind [docs/compatibility.md](../docs/compatibility.md) |
| `example_observation.py` | Regenerate `docs/examples/observation.json` from a real game |

## Prepare agent inputs

The agent reads game facts from `wc3agent/src/wc3agent/game/data/`, which is tracked: rerun these
after a game update. Preparation needs the supported installation (`WC3_GAME_DIR` or `--game-dir`)
and StormLib:

```powershell
git clone --depth 1 https://github.com/ladislav-zezula/StormLib.git tools/StormLib
cmake -S tools/StormLib -B tools/StormLib/build -G "Visual Studio 17 2022" -A x64 -DBUILD_SHARED_LIBS=ON -DSTORM_UNICODE=OFF -DSTORM_USE_BUNDLED_LIBRARIES=ON
cmake --build tools/StormLib/build --config Release

python -m tools.prepare reference                 # game/data/reference.json
python -m tools.prepare map                       # game/data/maps/<map>.json: starts, mines, creep camps, shops
python -m tools.scripts.measure_item_targets      # game/data/item_targets.json, measured in a running game
```

`STORMLIB_DLL` selects a StormLib built elsewhere. `reference.json` holds units (cost, food, build
time, requirements, what each trains, builds and researches), pathing footprints, collision radii,
per-rank abilities, items, research, command ids and damage multipliers for the stock installation (about 11 MB). It is a lookup: select
what a prompt needs rather than sending it whole. Live facts such as mana, cooldowns and research
levels come from the environment. Custom map object-data overrides are not extracted.
