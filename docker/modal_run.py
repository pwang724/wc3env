"""Run the Docker worker on Modal VM sandboxes (Wine needs the VM runtime's 32-bit syscall entry).

    python docker/prepare.py --game-dir "C:\\...\\Warcraft III (Legacy)" --output build/docker-context
    python docker/modal_run.py image                       # build, initialize Wine once, snapshot
    python docker/modal_run.py run smoke                   # any entrypoint.sh mode and its arguments
    python docker/modal_run.py run bench --prefixes 4 --instances 2 --observation binary --cpu 8 --memory 16384

Modal's image builder cannot run Wine, so the image is built with INIT_WINE=0 and a VM sandbox runs
the entrypoint's `init`; its filesystem snapshot is the worker image (id in build/modal-image.txt).
The activation files are streamed into each sandbox at /run/wc3-license, never into an image, as the
Docker README requires. Results in /sessions are copied to runs/modal/<sandbox id>/.
"""

from __future__ import annotations

import argparse
import io
import shlex
import sys
import tarfile
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "build" / "docker-context"
IMAGE_ID = ROOT / "build" / "modal-image.txt"
APP = "wc3-agent-lab-wine"
ENTRYPOINT = "cd /work && bash /opt/worker/with-display.sh bash /opt/worker/entrypoint.sh"


def sandbox(image: modal.Image, cpu: float, memory: int, timeout: int) -> modal.Sandbox:
    """A VM sandbox that idles; commands run through exec."""
    app = modal.App.lookup(APP, create_if_missing=True)
    box = modal.Sandbox.create(
        "sleep",
        "infinity",
        app=app,
        image=image,
        cpu=cpu,
        memory=memory,
        timeout=timeout,
        experimental_options={"vm_runtime": True},
    )
    print(f"sandbox {box.object_id}", flush=True)
    return box


def stream(box: modal.Sandbox, command: str, root: bool = False) -> int:
    """Run a shell command, as the image's worker user (whose Wine prefix it is) unless root."""
    if not root:
        command = "exec su worker -s /bin/bash -p -c " + shlex.quote("export HOME=/home/worker; " + command)
    process = box.exec("bash", "-c", command)
    for line in process.stdout:
        print(line, end="", flush=True)
    code = process.wait()
    error = process.stderr.read()
    if error:
        print(error, file=sys.stderr, flush=True)
    return code


def build_image(cpu: float) -> None:
    if not (CONTEXT / "Dockerfile").is_file():
        sys.exit(f"missing {CONTEXT}; run docker/prepare.py first")
    image = modal.Image.from_dockerfile(CONTEXT / "Dockerfile", context_dir=CONTEXT, build_args={"INIT_WINE": "0"})
    box = sandbox(image.entrypoint([]), cpu, 8192, 1800)
    try:
        if stream(box, f"{ENTRYPOINT} init") != 0:
            sys.exit("Wine initialization failed")
        snapshot = box.snapshot_filesystem()
        IMAGE_ID.write_text(snapshot.object_id)
        print(f"worker image {snapshot.object_id} saved to {IMAGE_ID}")
    finally:
        box.terminate()


def run(args: list[str], cpu: float, memory: int, timeout: int) -> int:
    from wc3env.settings import settings

    if not IMAGE_ID.is_file():
        sys.exit("no worker image; run `python docker/modal_run.py image` first")
    box = sandbox(modal.Image.from_id(IMAGE_ID.read_text().strip()), cpu, memory, timeout)
    try:
        stream(box, "mkdir -p /run/wc3-license /sessions && chown 10001:10001 /sessions", root=True)
        for name in ("roc.w3k", "tft.w3k"):
            copy = box.exec("bash", "-c", f"cat > /run/wc3-license/{name}", text=False)  # contents stay off argv
            copy.stdin.write((settings().game_dir / name).read_bytes())
            copy.stdin.write_eof()
            copy.stdin.drain()
            if copy.wait() != 0:
                sys.exit(f"could not upload {name}")
        stream(box, "chmod 444 /run/wc3-license/*", root=True)
        code = stream(box, f"{ENTRYPOINT} {shlex.join(args)}")
        out = ROOT / "runs" / "modal" / box.object_id
        out.mkdir(parents=True, exist_ok=True)
        archive = box.exec("tar", "-C", "/sessions", "-cf", "-", ".", text=False)
        data = archive.stdout.read()
        archive.wait()
        tarfile.open(fileobj=io.BytesIO(data)).extractall(out, filter="data")
        print(f"sessions copied to {out}; exit {code}")
        return code
    finally:
        box.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("image", "run"))
    parser.add_argument("--cpu", type=float, default=4)
    parser.add_argument("--memory", type=int, default=8192, help="MiB")
    parser.add_argument("--timeout", type=int, default=3600, help="sandbox lifetime, seconds")
    args, rest = parser.parse_known_args()
    sys.path.insert(0, str(ROOT / "src"))
    if args.command == "image":
        build_image(args.cpu)
        return 0
    return run(rest, args.cpu, args.memory, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
