# Docker worker

Runs the environment under Wine on x86-64 Linux. No image is distributed: build one from
your own supported installation. The repository contains no game files, and the image
you build contains yours, so keep it private.

## Build

On the Windows machine with the game, generate the inputs that are not in Git, then
assemble a build context:

```powershell
wc3hook/build.bat
python -m tools.prepare reference
python docker/prepare.py --game-dir "C:\Program Files (x86)\Warcraft III (Legacy)" --output build/docker-context
```

`prepare.py` verifies the executable hash, copies only the allowed game files and stock
maps, adds the hook, prepared inputs and Python sources, and excludes activation files and
`.env`. It refuses to merge into an existing directory. [Preparation](../tools/README.md)
needs StormLib. Copy the context to any Linux builder; it needs no game installation:

```sh
docker build --platform linux/amd64 --target agent -t wc3-worker:local build/docker-context
```

Use `--target environment` to leave out the agent and runner. The builder needs internet
access for base images and packages, and working x86 compatibility because Wine is
initialized during the build. Start with 4 vCPUs / 8 GiB RAM and software graphics.
Tested on Ubuntu 24.04 (kernel 6.1), Wine 11, Xvfb and Mesa llvmpipe; a Modal VM kernel
without the x86 `int 0x80` syscall entry could not run Wine, which
`docker/platform_probe.py` reports before launch.

## Supply your license at runtime

The image and repository contain no `roc.w3k` or `tft.w3k`. This exact client
opens a CD-key dialog without those files. Copy both from your own working
Warcraft III Legacy installation into a directory outside the repository, such
as `$HOME/.wc3-license`. Do not upload that directory or add keys to a Dockerfile.

Mount it read-only. The entrypoint checks that both files exist and makes links
inside the running container; no key contents enter the image or command line.
Missing files produce a clear error before launching the game.

```sh
sudo install -d -o 10001 -g 10001 /mnt/wc3-sessions
docker run --rm --init --network none --cpus 4 --memory 7g --shm-size 256m \
  --mount type=bind,src="$HOME/.wc3-license",dst=/run/wc3-license,readonly \
  --mount type=bind,src=/mnt/wc3-sessions,dst=/sessions \
  wc3-worker:local smoke
```

UID 10001 must be able to read the license files and write the sessions directory.
The smoke checks observations, movement, four exact one-second steps, reset in
the same process and close. Results go under `/mnt/wc3-sessions/env-<id>/env/`.
The files survive container removal while the host disk is retained.

For environment runs, select another bundled map with
`-e WC3_MAP='Maps/FrozenThrone/(4)TurtleRock.w3x'` before the image name, or pass
`map=` to `GameConfig` in your own Python runner.

The agent runs with `run`, for example `run scenarios --jobs 1` or `run melee --hidden`. Pass the model keys
(`-e OPENAI_API_KEY -e TYPESAFE_API_KEY -e MACRO_PROVIDER -e MACRO_MODEL`) from the host environment, not the
repository's whole `.env`, and drop `--network none`. Without the `/sessions` mount, output is lost when the container is removed.
