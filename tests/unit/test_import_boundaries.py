"""Package dependencies run one way: wc3agent may use wc3env, never the reverse."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "wc3agent/src/wc3agent"
GAME_FACING = ("play.py", "fusion.py", "duel.py")  # the runners: the only wc3agent code that may touch a live game


def imported_packages(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def importers(root, package, skip=()):
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in root.rglob("*.py")
        if path.relative_to(root).parts[0] not in skip and package in imported_packages(path)
    )


class ImportBoundaryTest(unittest.TestCase):
    def test_environment_never_imports_the_agent_or_tools(self):
        for package in ("wc3agent", "tools"):
            self.assertEqual(importers(ROOT / "src/wc3env", package), [])

    def test_policy_code_never_imports_the_environment_or_tools(self):
        self.assertEqual(importers(AGENT, "wc3env", skip=GAME_FACING), [])
        self.assertEqual(importers(AGENT, "tools"), [])


if __name__ == "__main__":
    unittest.main()
