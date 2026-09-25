"""Real-game tests; skipped when no supported Warcraft installation is configured.

python -m unittest discover -s tests/e2e -t .    engine, RPC, reset, pool and replay tests
python -m tests.e2e                              scripted scenarios from configs/ (reports in out/)
"""

import os
import unittest

from wc3env.settings import settings

# All real-game tests, scenarios and their subprocesses start silently.
os.environ["WC3_SOUND"] = "0"
settings.cache_clear()

if not settings().game_exe.is_file():
    raise unittest.SkipTest(f"No Warcraft III.exe in {settings().game_dir}; set WC3_GAME_DIR (see .env.example)")
