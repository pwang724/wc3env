"""Run with an isolated Python from a clean wheel installation; no checkout imports."""

import importlib
import importlib.util
import pkgutil
import struct
import sys
from importlib.metadata import distribution
from pathlib import Path

import wc3env


def main():
    assert importlib.util.find_spec("wc3agent") is None, "environment installation pulled in agents"
    package = Path(wc3env.__file__).resolve().parent
    if not package.is_relative_to(Path(sys.prefix).resolve()):
        raise AssertionError(f"package loaded outside the test environment: {package}")
    for module in pkgutil.walk_packages(wc3env.__path__, wc3env.__name__ + "."):
        if not module.name.endswith(".__main__"):  # importing it runs the pool CLI
            importlib.import_module(module.name)

    from wc3env.fake_server import FakeServer
    from wc3env.game import hook_dll
    from wc3env.rpc import LocalTransport, RpcClient

    dll = hook_dll().resolve()
    assert dll == package / "native" / "wc3hook.dll", dll
    binary = dll.read_bytes()
    pe = struct.unpack_from("<I", binary, 0x3C)[0]
    assert binary[:2] == b"MZ" and binary[pe : pe + 4] == b"PE\0\0"
    assert struct.unpack_from("<H", binary, pe + 4)[0] == 0x14C, "hook must target x86"
    files = [str(p).replace("\\", "/") for p in distribution("wc3env").files]
    for license in ("LICENSE", "wc3hook/minhook/LICENSE.txt", "wc3hook/yyjson/LICENSE"):
        assert any(p.endswith("/licenses/" + license) for p in files), license

    rpc = RpcClient(LocalTransport(FakeServer()))
    rpc.create_game("m", [{"slot": 0, "control": "agent"}])
    before = rpc.observe(0)
    rpc.step(250)
    after = rpc.observe(0)
    assert after["game_time_seconds"] - before["game_time_seconds"] == 0.25
    rpc.quit()
    rpc.close()
    print(f"Installed-wheel imports, bundled x86 DLL, licenses and fake RPC passed: {package}")


if __name__ == "__main__":
    main()
