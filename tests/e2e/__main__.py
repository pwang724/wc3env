"""The everyday real-game tier: every test module and scenario config, four at a time.

python -m tests.e2e                    (from the repository root)
python -m tests.e2e worker_loop        one scenario
"""

from .run import main

if __name__ == "__main__":
    main()
