# Writeup plan

Audience: a technical AI crowd that already knows AlphaStar and OpenAI Five. Tell the project as a set of design decisions and what each one cost. What's new is an LLM agent with no game-specific training, a real-time game with hidden information, and a clear list of where it breaks.

## Outline

1. **Why Warcraft III.** It has what StarCraft lacks for this question: heroes with levels, items, neutral creep camps, and a small army where each unit matters. Also say what we are not doing: no reinforcement learning and no self-play. The game knowledge comes from guides written into prompts.
2. **The environment.** A C hook inside the real game, a stepping mode and a real-time mode, and ground-truth state rather than pixels. Keep this short and link the code.
3. **The agent.** Two models on two timescales, joined by groups and plain-language objectives.
4. **The key decisions** (below). This is the core of the post.
5. **Results.** Mirror duels won 8/8. Then the full game against the Insane AI, which lost, with the root-cause table from [full-game-root-causes.md](full-game-root-causes.md).
6. **Where it breaks.** The table, grouped into: fixable in code; needs a fast reflex; needs a spatial picture of the map.
7. **Cost and latency.** Last game: 115 macro turns for about $4, answering in a median 6.6s. 2,491 micro decisions answered in a median 0.3s.
8. **What's next.**

## The decisions worth writing about

1. **Text observations, not pixels.** It's cheap, exact, and works with any LLM. The cost: no terrain, no pathing, no sense of space. Almost every hard failure comes back to this, which gives the post a clear storyline.
2. **Split by timescale, not by topic.** The macro thinks every ~7s, the micro every ~0.3s. The interesting part is the boundary between them: the macro talks to the micro in plain language ("creep camp 7; Grunts tank; kill the Ogre Magi first"). That's flexible but lossy. Compare with passing a structured goal instead.
3. **The micro picks from a menu instead of writing actions.** `candidates.py` lists the legal, sensible options, and Jev picks one, with a probability for each.
   - Pros: it can't hallucinate an action, you can inspect and debug every choice, and the replay panel comes almost for free.
   - Con: it can't do anything that isn't on the menu, such as "form a line here." This is the basic trade-off between a constrained and an open action space.
4. **Deciding what goes in code, what goes in prompts, and what the model decides.** Loot pickup, heal queues and escape thresholds live in `policies.py`. Prompts state general roles, not tactics fitted to one game.
   - The retreat fight shows the rule: a decision that must be made faster than the model can answer has to be a code reflex, and the model sets its limits.
   - This is the bitter-lesson tension in a concrete form: which of these rules would you want to delete as models get better?
5. **Real-time vs stepping.** Stepping mode hides the model's latency, which makes it reproducible and good for evaluation. Real-time exposes the latency. Present the 13:17 to 13:30 fight as the example: it was decided between two macro turns.
6. **Designing the observation text.**
   - A "what you can do now" list with "NOT YET: 70 more gold".
   - Order feedback that separates "submitted" from "confirmed started".
   - A precomputed strength number.
   - These affordances cut down invalid actions more than any prompt rule. Also admit their weak spot: when the strength number counted Mirror Images as real Blademasters, it fooled the model.
7. **Memory and belief under fog of war.** It keeps a window of 5-15 recent turns, cut in steps so prompt caching keeps working. It doesn't track the enemy's hidden state, which is why it attacked after 101s without seeing the enemy. It's a clean example of an LLM lacking a model of what it can't currently see.
8. **Evaluation.** Mirror duels act as unit tests for the micro; full games against the built-in AI test everything together. The replays with the decision panel serve as a way to see why each choice was made. The frank point: one full game is an anecdote, so say how many games we'd need.
9. **A human in the loop (optional, but it's a good point).** The watcher's orders caused two of the worst decisions, because the model obeys a commander even with too little information. That's a small, concrete case of the tension between following instructions and using its own judgment.

## Fundamental vs specific to this project

- **Fundamental:**
  - where to cut the hierarchy;
  - an open vs constrained action space;
  - code reflexes vs model judgment;
  - latency vs how fast the game changes;
  - the missing spatial representation;
  - beliefs about what's hidden.

  These carry over to robotics and computer-use agents, and that audience will care about the carry-over.
- **Specific to this project:** the illusion flag, the revive bug, the lumber cap. Put them in one table as evidence and move on.

## Framing

Lead with the picture of the two models, then the 13:17 to 13:30 fight as the moment it all becomes concrete.
