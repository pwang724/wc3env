"""Check the Linux x86 compatibility entry used even by Wine's new WoW64 mode.

Wine 11.0 alloc_fs_sel uses int 0x80 for SYS_set_thread_area:
https://github.com/wine-mirror/wine/blob/wine-11.0/dlls/ntdll/unix/signal_x86_64.c#L104
Run each instruction test in a child so an unsupported entry cannot kill the probe.
"""

from __future__ import annotations

import json
import platform
import signal
import subprocess
import sys
from pathlib import Path


def check_getpid(machine_code: str) -> dict:
    code = f"""
import ctypes, mmap, os, resource
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
page = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
page.write(bytes.fromhex({machine_code!r}))
address = ctypes.addressof(ctypes.c_char.from_buffer(page))
actual = ctypes.CFUNCTYPE(ctypes.c_long)(address)()
print(actual, flush=True)
assert actual == os.getpid(), (actual, os.getpid())
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    report = {"passed": result.returncode == 0, "exit_code": result.returncode}
    if result.returncode < 0:
        report["signal"] = signal.Signals(-result.returncode).name
    if result.stderr:
        report["stderr"] = result.stderr[-2000:]
    return report


def main():
    report = {"kernel": platform.release(), "machine": platform.machine(), "status": "unsupported"}
    if sys.platform == "linux" and platform.machine() == "x86_64":
        # mov eax, SYS_getpid; syscall/int 0x80; ret. The syscall numbers differ.
        report["native_x64_getpid"] = check_getpid("b8 27 00 00 00 0f 05 c3")
        report["compat_int80_getpid"] = check_getpid("b8 14 00 00 00 cd 80 c3")
        if all(report[key]["passed"] for key in ("native_x64_getpid", "compat_int80_getpid")):
            report["status"] = "passed"
        else:
            report["reason"] = "The kernel cannot execute the x86 syscall entry required by Wine WoW64."
    else:
        report["reason"] = "This worker requires Linux x86_64."
    Path("/tmp/platform-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
