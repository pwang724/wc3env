# Compatibility evidence

The launcher supports the executable hash in [design](design.md#compatibility).
These measurements describe tested fixtures on one machine, not universal engine limits.
Measured 2026-09-17/18: Windows 11 build 26200, x64 Python 3.14.6, Intel Graphics driver
32.0.101.8724, desktop 1920x1200, system DPI query 96. Other physical GPUs, drivers,
desktop scaling settings and Windows versions remain untested.

| Case | Result |
|---|---|
| Echo Isles and Secret Valley, two agents | Passed observation, movement and exact stepping |
| Twisted Meadows, four agents; Twilight Ruins, eight agents | Passed independent observations, movement and exact stepping |
| Human, Orc, Undead, Night Elf | Correct starting workers/buildings; seeded random selection and built-in computer AI also tested |
| 640x480, 1280x720, 1920x1080, presentation on/off | Passed gameplay at every requested size |
| Request 320x240, presentation on/off | Engine applied 640x360; gameplay passed at that actual size |
| 512, 1,024 and 1,536 added flying units | Full own-unit observation and movement of the last spawned unit passed |
| 2,048 added flying units, presentation on/off | Passed observation, last-unit movement and exact advancement after allocator fix |
| Pools of one, two and four processes | Passed 40 rounds of exact advancement and cleanup |
| 360 raw resets before GPU completion fix | After reset 10: used address space grew 123.6 MiB; 17 mostly empty 5.5 MiB graphics ranges remained |
| 360 raw resets with GPU completion | After reset 10: used address space grew 10.7 MiB; no accumulated 5.5 MiB ranges; native arenas stayed at 83.4 MiB |
| 120 session resets, recycling every 32 episodes | Passed across four processes; final-window median was about 10 MiB above the warm baseline |
| Complete seeded Echo Isles match, idle Human versus Orc AI | All 458 sampled states and the 458.4-second outcome matched across reload and a fresh process |
| Two-agent movement replay | All 51 sampled states matched the recording; EOF preserves observations and ends the session |

The original 2,048-unit failure exhausted the process's 2 GiB address space: moved reallocations
kept reserving arenas from an old full arena. Selecting the current matching arena fixes that
case. These are short movement workloads, not a portable unit limit or combat benchmark.

The old free-space search skipped usable bins; merging holes alone did not fix it. Over resets
10–120, native reservations previously grew 104.3 → 147.4 MiB; the corrected search stayed at
83.7 MiB. Tiny allocations pinning old arenas were live UI text caches, not abandoned data.
Remaining growth traced to Direct3D heap capacity and Intel graphics-buffer mappings.
Suppressing `Present` left GPU work pending and accumulated mostly empty 5.5 MiB ranges.
Render-off now waits for GPU completion through an event query. Heap caches still retain
capacity; the 32-episode recycling default remains.

## Reproduce

```powershell
wc3hook/build.bat
python tools/check_compatibility.py --output runs/compatibility.json
python tools/check_compatibility.py --only window --soak-resets 0 --output runs/windows.json
python tools/check_compatibility.py --only reset-soak --soak-resets 120 --output runs/reset-soak.json
python -m unittest tests.e2e.extended.allocator tests.e2e.extended.determinism tests.e2e.test_replay
```

The harness records binary hashes, revision, hardware, actual sizes, simulation timing and
memory after every case. Failures exit nonzero; clamped sizes are `limited`, skipped cases
remain untested. `--army-sizes` selects larger stress cases; no Warcraft assets enter reports.

The tracked replay fixture contains recorded commands, not map assets. Arbitrary custom maps,
other graphics backends and LAN require separate validation. LAN create/join and network
command delivery are not implemented by the current offline API.

## Linux / Wine

The [Docker worker](../docker/README.md) passed observations, worker movement, four exact
1,000-ms steps, same-process reset, close and an agent probe on Ubuntu 24.04 (kernel 6.1),
Wine 11, Windows Python 3.11.9, Xvfb and Mesa llvmpipe with 4 vCPUs / 8 GiB RAM, without
privileged mode. Files under a mounted `/sessions` survived container removal. These short
checks do not establish throughput; model-driven runs, render-off, repeated resets and long
workloads are untested there. Windows results do not establish Linux compatibility.
