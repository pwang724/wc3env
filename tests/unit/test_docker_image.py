"""Docker contexts come from the builder's installation; activation stays separate."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prepare = module("docker_prepare", "docker/prepare.py")
licenses = module("docker_license", "docker/license.py")


class DockerContextTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.install = self.root / "installation"
        for name in (
            prepare.HOOK,
            *prepare.PREPARED,
            "pyproject.toml",
            "setup.py",
            "README.md",
            "LICENSE",
            "docker/Dockerfile",
            "docker/entrypoint.sh",
            "wc3hook/main.c",
            "wc3hook/yyjson/LICENSE",
            "src/wc3env/__init__.py",
            "wc3agent/pyproject.toml",
            "wc3agent/LICENSE",
            "wc3agent/README.md",
            ".env",
            "roc.w3k",
            "build/old.py",
            "wc3hook/out/old.c",
            "installation/Warcraft III.exe",
            "installation/Mss32.dll",
            "installation/War3.mpq",
            "installation/War3x.mpq",
            "installation/Maps/FrozenThrone/test.w3x",
            "installation/Maps/notes.txt",
            "installation/roc.w3k",
            "installation/tft.w3k",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture " + name.encode())
        exe_hash = hashlib.sha256((self.install / "Warcraft III.exe").read_bytes()).hexdigest()
        patcher = patch.object(prepare, "EXE_SHA256", exe_hash)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.output = self.root / "context"

    def test_context_holds_game_and_sources_but_no_credentials(self):
        prepare.prepare(self.output, self.install, self.root)
        for name in (
            "assets/game/Warcraft III.exe",
            "assets/game/Maps/FrozenThrone/test.w3x",
            prepare.HOOK,
            *prepare.PREPARED,
            "Dockerfile",
            "wc3hook/main.c",
        ):
            self.assertTrue((self.output / name).is_file(), name)
        for name in (".env", "roc.w3k", "build/old.py", "wc3hook/out/old.c", "assets/game/Maps/notes.txt"):
            self.assertFalse((self.output / name).exists(), name)
        self.assertFalse(list(self.output.rglob("*.w3k")))

    def test_unsupported_or_incomplete_inputs_are_rejected_before_writing(self):
        cases = (
            ("installation/War3.mpq", FileNotFoundError, "Missing Warcraft installation files"),
            (prepare.HOOK, FileNotFoundError, "build.bat"),
            (prepare.PREPARED[0], FileNotFoundError, "tools.prepare"),
        )
        for name, error, message in cases:
            with self.subTest(name):
                content = (self.root / name).read_bytes()
                (self.root / name).unlink()
                with self.assertRaisesRegex(error, message):
                    prepare.prepare(self.output, self.install, self.root)
                (self.root / name).write_bytes(content)
        (self.install / "Warcraft III.exe").write_bytes(b"different patch")
        with self.assertRaisesRegex(ValueError, "Unsupported Warcraft installation"):
            prepare.prepare(self.output, self.install, self.root)
        self.assertFalse(self.output.exists())


class RuntimeLicenseTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.game, self.keys = self.root / "game", self.root / "keys"
        self.game.mkdir()
        self.keys.mkdir()

    def test_missing_or_baked_in_activation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Mount a directory"):
            licenses.attach(self.game, self.keys)
        self.assertEqual(list(self.game.iterdir()), [])
        for name in ("roc.w3k", "tft.w3k"):
            (self.keys / name).write_bytes(b"test activation fixture")
        (self.game / "roc.w3k").write_bytes(b"must not be in image")
        with self.assertRaisesRegex(ValueError, "baked into"):
            licenses.attach(self.game, self.keys)


if __name__ == "__main__":
    unittest.main()
