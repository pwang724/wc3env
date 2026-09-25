"""Install a built wheel into a fresh environment and smoke-test outside the checkout."""

import argparse
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="wheel file, or directory containing exactly one project wheel")
    args = parser.parse_args()
    candidates = list(args.wheel.glob("wc3env-*.whl")) if args.wheel.is_dir() else [args.wheel]
    if len(candidates) != 1 or not candidates[0].is_file():
        parser.error("supply one existing wc3env wheel")
    wheel = candidates[0].resolve()
    smoke = Path(__file__).resolve().parents[1] / "tests" / "installed_smoke.py"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "WC3_")) and key != "VIRTUAL_ENV"
    }
    with tempfile.TemporaryDirectory(prefix="wc3-installed-") as temp:
        root = Path(temp)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv" / "Scripts" / "python.exe"
        shutil.copyfile(smoke, root / "smoke.py")
        subprocess.run(
            [str(python), "-I", "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run([str(python), "-I", str(root / "smoke.py")], cwd=root, env=env, check=True)
        subprocess.run(
            [str(python), "-I", "-m", "wc3env", "--help"], cwd=root, env=env, check=True, stdout=subprocess.DEVNULL
        )


if __name__ == "__main__":
    main()
