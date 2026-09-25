"""Shared locations and the .env reader. Outputs and .env use the working directory."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = Path.cwd()


def _read_dotenv(path):
    """Read optional KEY=VALUE settings without importing an environment backend."""
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def environment():
    return {**_read_dotenv(ROOT / ".env"), **os.environ}
