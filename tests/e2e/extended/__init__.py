"""Extended real-game fixtures: broader than the everyday e2e tier, run on request.

    python -m tests.e2e.extended                          all extended modules and scenarios
    python -m tests.e2e.extended opening_undead           one scenario
    python -m unittest tests.e2e.extended.seeds_determinism   long seeded-match checks
    python -m tests.e2e.extended.record_combat_replay         re-record tests/fixtures/combat.w3g

Run it after hook or protocol changes and before tagging. It covers Undead and Night Elf
economy and openings, hero casts at a unit, a point and with no target (a summon), an
8-player map, orders the engine refuses, rarely seen events, allocator stress, full-match
determinism and recorded combat replay. Module filenames
omit the `test_` prefix so ordinary unittest discovery leaves this tier to the explicit runner.
"""
