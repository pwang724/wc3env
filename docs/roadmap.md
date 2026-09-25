# Roadmap

Unfinished work only. [Design](design.md) describes implemented behavior;
[specs](specs/) define contracts. Evidence stays in tests, run artifacts and Git.

## Current work

| Work | State / next check |
|---|---|
| Jev live policy | Realtime brief controller lives in `wc3agent/`; compare executed decisions across repeated runs. Other architectures remain future experiments. |
| Pending construction placement | Sites of workers' current build orders and of earlier builds in the same act call count as occupied. A worker's Shift-queued builds after its current order are not read, so a later step can still pick their sites. No retry scheduler. |
| Team metadata for activated slots | `players[].team` is `-1` for slots that `MatchSetup` activates beyond the lobby's own (relations and alliances are correct). Assign a team at setup or document `-1` as unassigned. |
| Docker workers / Wine | Build from a user installation, mounted licenses, game smoke and agent probe passed. Next: longer runs, render-off and repeated resets. [Results](compatibility.md#linux--wine). |

## Next

1. **Compatibility and scale:** extend the [measured matrix](compatibility.md) to other
   GPUs, drivers, DPI settings and custom maps. Extend reset soaks before changing the
   32-episode recycling default; native allocation and GPU completion fixes are implemented.
2. **Repeatability:** extend replay and complete-match fixtures to more maps and active policies.
   `save_replay` exports native replays; debug mutations are not replay commands.
3. **LAN:** implement native create/join and network command delivery before validating a
   complete realtime match. Local process clock isolation passes; LAN desync remains untested.

## Deferred scope

Other executable builds, Battle.net play and windowless rendering need separate integration.
Other Linux hosts and larger worker pools need separate compatibility and
performance results.
