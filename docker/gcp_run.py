"""Run the Docker worker on Google Cloud: the image built by Cloud Build, workers on spot VMs.

    python docker/prepare.py --game-dir "C:\\...\\Warcraft III (Legacy)" --output build/docker-context
    python docker/gcp_run.py setup --project P            # registry, bucket, license secrets (once)
    python docker/gcp_run.py image --project P            # Cloud Build: build and push the image
    python docker/gcp_run.py run --project P smoke        # a spot VM runs one entrypoint.sh mode
    python docker/gcp_run.py run --project P bench --prefixes 8 --instances 2 --observation binary

Cloud Build machines run 32-bit code, so the image initializes Wine as it builds (INIT_WINE=1). The
activation files live in Secret Manager: a worker reads them at boot into /run/wc3-license, mounted
read-only, never into the image. A worker runs its mode in the container, copies /sessions to
gs://<project>-sessions/<vm>/ and deletes itself; `run` waits for that and downloads it to
runs/gcp/<vm>/. Private: the image holds your game files, so the registry stays in your project.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "build" / "docker-context"
GCLOUD = shutil.which("gcloud") or "gcloud"

STARTUP = """#!/bin/bash
# The worker: docker, the license from Secret Manager, one entrypoint mode, results to the bucket.
set -u
meta() { curl -sf -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/$1"; }
IMAGE=$(meta instance/attributes/wc3-image); ARGS=$(meta instance/attributes/wc3-args)
NAME=$(meta instance/name); ZONE=$(meta instance/zone | cut -d/ -f4); BUCKET=$(meta instance/attributes/wc3-bucket)
apt-get update -q && apt-get install -yq docker.io
gcloud auth configure-docker "${IMAGE%%/*}" -q
mkdir -p /run/wc3-license /sessions && chown 10001:10001 /sessions
for key in roc tft; do gcloud secrets versions access latest --secret "wc3-$key" > "/run/wc3-license/$key.w3k"; done
chmod 444 /run/wc3-license/*
# ntsync (kernel 6.14+): Wine then synchronizes in the kernel instead of through its wineserver.
NTSYNC=""
if [ "$(meta instance/attributes/wc3-ntsync)" = 1 ] && modprobe ntsync && [ -e /dev/ntsync ]; then
    chmod 666 /dev/ntsync
    NTSYNC="--device /dev/ntsync"
fi
echo "ntsync: ${NTSYNC:-off}" > /sessions/ntsync
docker run --rm --init --network none --shm-size 256m $NTSYNC \\
    --mount type=bind,src=/run/wc3-license,dst=/run/wc3-license,readonly \\
    --mount type=bind,src=/sessions,dst=/sessions "$IMAGE" $ARGS > /sessions/worker.log 2>&1
echo "exit $?" > /sessions/done
gcloud storage rsync -r /sessions "gs://$BUCKET/$NAME"
gcloud compute instances delete "$NAME" --zone "$ZONE" -q
"""


def gcloud(*args: str, check: bool = True, capture: bool = False) -> str:
    result = subprocess.run([GCLOUD, *args], check=check, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def names(args) -> tuple[str, str, str]:
    """The registry host, the image and the results bucket for a project."""
    region = args.zone.rsplit("-", 1)[0]
    return (
        f"{region}-docker.pkg.dev",
        f"{region}-docker.pkg.dev/{args.project}/wc3/worker:latest",
        f"{args.project}-sessions",
    )


def setup(args) -> None:
    from wc3env.settings import settings

    region = args.zone.rsplit("-", 1)[0]
    _, _, bucket = names(args)
    p = ("--project", args.project)
    gcloud(
        "artifacts",
        "repositories",
        "create",
        "wc3",
        "--repository-format=docker",
        f"--location={region}",
        *p,
        check=False,
    )
    gcloud("storage", "buckets", "create", f"gs://{bucket}", f"--location={region}", *p, check=False)
    account = gcloud("compute", "project-info", "describe", *p, "--format=value(defaultServiceAccount)", capture=True)
    for key in ("roc", "tft"):
        secret = f"wc3-{key}"
        gcloud("secrets", "create", secret, "--replication-policy=automatic", *p, check=False)
        gcloud("secrets", "versions", "add", secret, f"--data-file={settings().game_dir / (key + '.w3k')}", *p)
        gcloud("secrets", "add-iam-policy-binding", secret, f"--member=serviceAccount:{account}",
               "--role=roles/secretmanager.secretAccessor", *p, "--quiet", capture=True)  # fmt: skip
    print(f"registry wc3 and bucket gs://{bucket} in {region}; license secrets for {account}")


def image(args) -> None:
    if not (CONTEXT / "Dockerfile").is_file():
        sys.exit(f"missing {CONTEXT}; run docker/prepare.py first")
    _, tag, _ = names(args)
    gcloud("builds", "submit", str(CONTEXT), f"--tag={tag}", "--machine-type=e2-highcpu-8", "--timeout=3600s",
           f"--project={args.project}", f"--region={args.zone.rsplit('-', 1)[0]}")  # fmt: skip
    print(f"worker image {tag}")


def run(args, mode: list[str]) -> int:
    _, tag, bucket = names(args)
    name = f"wc3-{time.strftime('%Y%m%d-%H%M%S')}"
    startup = ROOT / "build" / "gcp-startup.sh"
    startup.parent.mkdir(exist_ok=True)
    startup.write_text(STARTUP, newline="\n")
    gcloud("compute", "instances", "create", name, f"--project={args.project}", f"--zone={args.zone}",
           f"--machine-type={args.machine}", "--provisioning-model=SPOT", "--instance-termination-action=DELETE",
           "--image-family=ubuntu-2404-lts-amd64", "--image-project=ubuntu-os-cloud", "--boot-disk-size=40GB",
           "--scopes=cloud-platform", f"--metadata-from-file=startup-script={startup}",
           f"--metadata=wc3-image={tag},wc3-bucket={bucket},wc3-ntsync={int(args.ntsync)},wc3-args={shlex.join(mode)}")  # fmt: skip
    print(f"worker {name} started; waiting for gs://{bucket}/{name}/done", flush=True)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(30)
        done = subprocess.run([GCLOUD, "storage", "cat", f"gs://{bucket}/{name}/done"],
                              capture_output=True, text=True)  # fmt: skip
        if done.returncode == 0:
            out = ROOT / "runs" / "gcp" / name
            out.mkdir(parents=True, exist_ok=True)
            # Per-game user folders (logs, settings) nest too deep for Windows paths; they stay in the bucket.
            gcloud("storage", "rsync", "-r", "--exclude=.*/Warcraft III/.*", f"gs://{bucket}/{name}", str(out))
            print((out / "worker.log").read_text(errors="replace")[-3000:])
            print(f"results in {out}; {done.stdout.strip()}")
            return 0 if done.stdout.strip() == "exit 0" else 1
    print(
        f"no result after {args.timeout} s; the VM may still be running: gcloud compute instances list --project {args.project}"
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("setup", "image", "run"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--zone", default="us-central1-a")
    parser.add_argument("--machine", default="n2d-standard-16")
    parser.add_argument("--timeout", type=int, default=3600, help="seconds to wait for a run")
    parser.add_argument("--no-ntsync", dest="ntsync", action="store_false", help="leave Wine on its wineserver")
    args, rest = parser.parse_known_args()
    sys.path.insert(0, str(ROOT / "src"))
    if args.command == "setup":
        setup(args)
    elif args.command == "image":
        image(args)
    else:
        return run(args, rest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
