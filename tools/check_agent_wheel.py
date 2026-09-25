"""Verify the agent policy without the environment, then both installed wheels, outside the checkout."""

import argparse
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", type=Path, help="directory containing the wc3agent and wc3env wheels")
    parser.add_argument("--reference", type=Path, help="Also load this prepared JSON without archive access")
    args = parser.parse_args()
    found = [list(args.wheels.glob(pattern)) for pattern in ("wc3agent-*.whl", "wc3env-*.whl")]
    if any(len(paths) != 1 for paths in found):
        parser.error("supply exactly one wheel for each project")
    agent, environment = [paths[0].resolve() for paths in found]
    if not agent.name.endswith("-py3-none-any.whl"):
        parser.error("the agent wheel must be platform-neutral")
    with zipfile.ZipFile(agent) as archive:
        assert not any(name.endswith(".dll") for name in archive.namelist())
        assert not any(name.startswith(("wc3agent/system1/", "wc3agent/system2/")) for name in archive.namelist())
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "WC3_", "TYPESAFE_")) and key != "VIRTUAL_ENV"
    }
    with tempfile.TemporaryDirectory(prefix="wc3agent-installed-") as temp:
        root = Path(temp)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv/Scripts/python.exe"

        def run(*arguments):
            subprocess.run([str(python), "-I", *arguments], cwd=root, env=clean_env, check=True)

        run("-m", "pip", "install", "--no-index", "--no-deps", str(agent))
        run(
            "-c",
            """
import importlib.util
import sys
from wc3agent.models.jev import choice_question
from wc3agent.macro.prompts import GENERAL_GUIDE, MACRO_SYSTEM, RACE_GUIDES
from wc3agent.game.catalog import Catalog
from wc3agent.agent import Agent
from wc3agent.macro.agent import MacroAgent
from wc3agent.macro.memory import MacroMemory
from wc3agent.micro.agent import MicroAgent
from wc3agent.micro.memory import MicroMemory
from wc3agent.scenarios.scenario import names
assert importlib.util.find_spec('wc3env') is None
assert 'wc3env' not in sys.modules
assert choice_question('Choose', {'keep': 'Continue'})['criteria'] == {'keep': 'Continue'}
assert MACRO_SYSTEM and GENERAL_GUIDE and RACE_GUIDES and names()
print('Agent client, packaged prompt and scenarios work without Warcraft installed.')
""",
        )
        if args.reference:
            run(
                "-c",
                "from wc3agent.game.catalog import Catalog; import sys; c=Catalog.load(sys.argv[1]); assert c.units and c.abilities; print('Prepared JSON loaded without the environment package.')",
                str(args.reference.resolve()),
            )
        run("-m", "pip", "install", "--no-index", "--no-deps", str(environment))
        # The CLI uses the environment declared in the agent package dependencies.
        run("-m", "wc3agent", "--help")
        run(
            "-c",
            """
import importlib
import json
import pkgutil
from pathlib import Path
import wc3agent
for module in pkgutil.walk_packages(wc3agent.__path__, wc3agent.__name__ + '.'):
    if not module.name.endswith('.__main__'):
        importlib.import_module(module.name)
print('All agent and runner imports work outside the checkout.')
""",
        )


if __name__ == "__main__":
    main()
