# Full game root causes: Orc vs Insane AI

Game: `sessions/melee-orc-opus2`, Orc mirror against the Insane computer on the same map. Macro was Opus 5.5 and micro was Jev. We lost at about 14:09.

The question: which failures are hard to fix without vision, a vision-language model or a faster macro? Most are fixable in code or prompts. Only two really need a spatial picture of the map or a faster decision loop.

## Fixable without vision

| Problem | Root cause (from the log) | Fix |
|---|---|---|
| Economy starved, lumber piled up to 1,905 | Exactly 5 Peons on gold all game (one mine is full at 5), and lumber Peons grew from 4 to 10. It never expanded, so gold income stayed flat. From 11:49 it was also paying upkeep (70% gold income). | Code: cap lumber workers and send extra workers to an expansion. The prompt now says to expand; a hard rule in code would be more reliable. |
| Mirror Images | The hook doesn't tell illusions apart. Jev's picks named Blademasters 114 times, and only 22 were blademaster1, probably the real one; about 80% went to illusions. The macro also read "Blademaster x4" and counted enemy strength as 2,594. | Hook: the game has an `IsUnitIllusion` check. Mark illusions and leave them out of strength. |
| Heroes running ahead | At 12:57 both heroes were at (-4192,1698) and the army's center was at (-1878,1581), about 2,300 apart. Heroes are faster, and after picking up items they sat idle near the enemy base. The macro noticed this 3 turns in a row and only told them to wait. | Code: keep heroes within a set distance of the army's center when moving. Don't make the macro manage it. |
| Far Seer revive never started | A revive order went in at 13:40 and again at 13:52. The Altar stayed idle and there was no feedback. | A bug to trace: cost, the order format, or the Altar being busy. |
| XP plateau at level 3 (6:53 to about 13:00) | 7 camps cleared in 14 minutes. The army spent 10:21-11:41 parked at base and the Voodoo Lounge healing with salves (the human watching said "regroup and salve everybody"). The second hero also halved each hero's XP. | Prompt and policy; the guide already says never park to heal. |

## Needs a faster decision loop, but a code rule solves it

**When to fight and when to retreat.** Macro turns take a median of 6.6s to answer, and 11s at p90. The fatal fight:

- **13:09:** attack the expansion.
- **13:17:** enemy strength 1,434 vs our 1,200. Macro: heroes fall back.
- **13:30:** 2,594 vs 1,136, and the Far Seer is at 24 HP. Only now does the macro say retreat, and the Far Seer dies.

The whole fight was decided between two macro turns. A faster model won't fix that, and vision isn't needed. What's needed is a group-level reflex in `policies.py`: retreat when the enemy in view is clearly stronger (after removing illusions) or a hero drops low. The macro then decides what to do after the retreat.

## Hard without vision or a spatial map

1. **Terrain and pathing.**
   - The macro only sees coordinates. It guessed the enemy expansion "by mirroring ours" and walked across the map.
   - Its retreat plan ("east toward home ... so we don't run across their path") is guesswork about paths it can't see.
   - It can't see choke points, cliffs, walking distance, or tower range.
   - A text version is possible without a vision model: the pathing grid, turned into named areas and walking distances. That's real work, not a prompt tweak.
2. **Judging a fight in the fog.**
   - It attacked after nobody had seen the enemy for 101s, not knowing the enemy army's size.
   - "Strength" is a crude sum. It ignores Bloodlust, upgrades, towers, and whether the army is spread out.
   - Keeping a memory of the last enemy army seen is easy.
   - Truly predicting who wins a fight is hard: it needs a simple simulator or learned values, not better prompts.
3. **Shape of the army in big fights.** Surrounding, body-blocking, spreading against area damage, and keeping melee units from stringing out on a retreat all need space. Jev picks from a list of targets and actions per unit, so it can't express "form a line here." In the retreat, the Shadow Hunter reached home while the back of the army was still being chased 4,000 units away. That's partly the missing rule above, but good formation play needs a spatial picture.

The watcher's orders ("attack their expansion", "regroup and salve") also drove two of the worst decisions. The macro treats them as orders from its commander, so it followed them even without enough information.

## Suggested order of work

1. Illusion flag.
2. Hero leash.
3. Group retreat reflex.
4. Lumber cap and expansion rule.
5. Trace the revive bug.

Those are all code. Turning the pathing grid into text is the one real step toward "vision" and can wait.
