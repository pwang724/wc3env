from pathlib import Path

from tests.e2e.run import main

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    main(
        configs=HERE / "configs",
        output=HERE / "out",
        modules=[
            "tests.e2e.extended.seeds_determinism",
            "tests.e2e.extended.determinism",
            "tests.e2e.extended.combat_replay",
            "tests.e2e.extended.allocator",
        ],
    )
