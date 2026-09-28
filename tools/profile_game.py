"""Sample where a stepping game process spends CPU, from outside the process.

    python tools/profile_game.py --warmup-seconds 120 --seconds 15 [--observation binary] [--under 1aefd0]

Plays the benchmark workload (tools/bench.py) in one game, then samples every busy thread's
instruction pointer and stack about once a millisecond while stepping continues. Reports CPU per
thread and, for the busy threads, the modules the samples land in and the exe functions on the stack
(inclusive and exclusive); `--under RVA` also breaks down what that function spends its samples
calling. Function starts are found as tools/disasm.py finds them, so the RVAs it prints can be read
with `python tools/disasm.py <rva>`. Works on Windows and under Wine.
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import struct
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from bench import add_workload_arguments, tune, workload_config  # noqa: E402
from winproc import k32, psapi, thread_cpu_seconds  # noqa: E402

from wc3env.session import GameSession  # noqa: E402

H, D = wintypes.HANDLE, wintypes.DWORD
PROLOGUE = bytes((0x55, 0x8B, 0xEC))  # push ebp; mov ebp, esp
REGISTERS = (
    "gs",
    "fs",
    "es",
    "ds",
    "edi",
    "esi",
    "ebx",
    "edx",
    "ecx",
    "eax",
    "ebp",
    "eip",
    "cs",
    "eflags",
    "esp",
    "ss",
)


class THREADENTRY32(ctypes.Structure):
    _fields_ = [(n, D) for n in ("size", "usage", "tid", "pid", "base_priority", "delta_priority", "flags")]


class WOW64_CONTEXT(ctypes.Structure):
    _fields_ = (
        [("flags", D), ("dr", D * 6), ("float_save", ctypes.c_byte * 112)]
        + [(n, D) for n in REGISTERS]
        + [("ext", ctypes.c_byte * 512)]
    )


class MODULEINFO(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("size", D), ("entry", ctypes.c_void_p)]


k32.CreateToolhelp32Snapshot.restype = H
k32.CreateToolhelp32Snapshot.argtypes = [D, D]
k32.Thread32First.argtypes = k32.Thread32Next.argtypes = [H, ctypes.POINTER(THREADENTRY32)]
k32.OpenThread.restype = H
k32.OpenThread.argtypes = [D, wintypes.BOOL, D]
k32.ResumeThread.argtypes = k32.Wow64SuspendThread.argtypes = [H]
k32.Wow64GetThreadContext.argtypes = [H, ctypes.POINTER(WOW64_CONTEXT)]
k32.ReadProcessMemory.argtypes = [H, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.CloseHandle.argtypes = [H]
psapi.EnumProcessModulesEx.argtypes = [H, ctypes.POINTER(ctypes.c_void_p), D, ctypes.POINTER(D), D]
psapi.GetModuleBaseNameW.argtypes = [H, ctypes.c_void_p, wintypes.LPWSTR, D]
psapi.GetModuleInformation.argtypes = [H, ctypes.c_void_p, ctypes.POINTER(MODULEINFO), D]


def threads_of(pid: int) -> list[int]:
    snap = k32.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    entry = THREADENTRY32()
    entry.size = ctypes.sizeof(entry)
    tids = []
    ok = k32.Thread32First(snap, ctypes.byref(entry))
    while ok:
        if entry.pid == pid:
            tids.append(entry.tid)
        ok = k32.Thread32Next(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return tids


def modules_of(process) -> list[tuple[int, int, str]]:
    handles, needed = (ctypes.c_void_p * 1024)(), D()
    psapi.EnumProcessModulesEx(process, handles, ctypes.sizeof(handles), ctypes.byref(needed), 0x01)  # 32-bit
    found = []
    for h in handles[: needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        name, info = ctypes.create_unicode_buffer(260), MODULEINFO()
        psapi.GetModuleBaseNameW(process, h, name, 260)
        psapi.GetModuleInformation(process, h, ctypes.byref(info), ctypes.sizeof(info))
        found.append((info.base or 0, info.size, name.value))
    return found


def module_at(modules, address: int) -> str:
    return next((name for base, size, name in modules if base <= address < base + size), "?")


def exe_code() -> bytes:
    """The exe laid out at its RVAs (sections copied from the file), to recognise return addresses."""
    from wc3env.settings import settings

    d = settings().game_exe.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    count, optional = struct.unpack_from("<H", d, pe + 6)[0], struct.unpack_from("<H", d, pe + 20)[0]
    image = bytearray(struct.unpack_from("<I", d, pe + 24 + 56)[0])
    for i in range(count):
        vsize, va, rsize, raw = struct.unpack_from("<IIII", d, pe + 24 + optional + 40 * i + 8)
        n = min(vsize, rsize)
        image[va : va + n] = d[raw : raw + n]
    return bytes(image)


def returns_from_call(code: bytes, r: int) -> bool:
    """The bytes before r end in a call: E8 rel32, or FF /2 through a register or memory."""
    if r >= 5 and code[r - 5] == 0xE8:
        return True
    return any(r >= n and code[r - n] == 0xFF and (code[r - n + 1] >> 3) & 7 == 2 for n in (2, 3, 6, 7))


_starts: dict[int, int] = {}


def function_start(code: bytes, r: int) -> int:
    """disasm.Image.func_start's rule (the nearest prologue at most 0x4000 bytes back, else r itself),
    without its capstone dependency, which the Docker worker does not ship."""
    if r not in _starts:
        i = code.rfind(PROLOGUE, max(r - 0x4000, 0), r + 1)
        _starts[r] = i if i >= 0 else r
    return _starts[r]


def sample(process, pid: int, seconds: float, top: int, under: int | None = None) -> str:
    handles = {tid: k32.OpenThread(0x1FFFFF, False, tid) for tid in threads_of(pid)}
    modules = modules_of(process)
    exe_base = next((b for b, _, n in modules if n.lower() == "warcraft iii.exe"), 0)
    code = exe_code()
    cpu0 = {tid: thread_cpu_seconds(h) for tid, h in handles.items()}
    time.sleep(0.5)
    busy = {tid: (thread_cpu_seconds(h) - cpu0[tid]) / 0.5 for tid, h in handles.items()}
    hot = [tid for tid, c in sorted(busy.items(), key=lambda kv: -kv[1]) if c > 0.05][:4]
    counters = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    counts = collections.Counter()
    cpu_start = {tid: thread_cpu_seconds(h) for tid, h in handles.items()}
    stack = (D * 4096)()
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        for tid in hot:
            h = handles[tid]
            if k32.Wow64SuspendThread(h) == 0xFFFFFFFF:
                continue
            ctx = WOW64_CONTEXT()
            ctx.flags = 0x00010001  # WOW64_CONTEXT_CONTROL
            ok = k32.Wow64GetThreadContext(h, ctypes.byref(ctx))
            got = ctypes.c_size_t()
            if ok:
                k32.ReadProcessMemory(process, ctx.esp, stack, ctypes.sizeof(stack), ctypes.byref(got))
            k32.ResumeThread(h)
            if not ok:
                continue
            counts[tid] += 1
            c = counters[tid]
            module = module_at(modules, ctx.eip)
            c["modules"][module] += 1
            chain = []  # function starts, innermost first
            eip = ctx.eip - exe_base
            if 0 < eip < len(code):
                chain.append(function_start(code, eip))
                c["exclusive"][chain[0]] += 1
            else:
                chain.append(module)
            chain += [
                function_start(code, a - exe_base)
                for a in stack[: got.value // 4]
                if 0 < a - exe_base < len(code) and returns_from_call(code, a - exe_base)
            ]
            for f in set(chain):
                if not isinstance(f, str):
                    c["inclusive"][f] += 1
            if under in chain:
                i = chain.index(under)
                c["under"][chain[i - 1] if i else "self"] += 1
        time.sleep(0.001)
    wall = time.perf_counter() - started
    used = {tid: (thread_cpu_seconds(h) - cpu_start[tid]) / wall for tid, h in handles.items()}
    for h in handles.values():
        k32.CloseHandle(h)

    def pct(counter, n, k):
        return ", ".join(
            f"{x if isinstance(x, str) else f'{x:06x}'} {100 * v / n:.0f}%" for x, v in counter.most_common(k)
        )

    lines = [f"sampled {wall:.1f} s; CPU {sum(used.values()):.2f} cores in total, by thread:"]
    lines += [
        f"  thread {tid}: {cores:.2f}" for tid, cores in sorted(used.items(), key=lambda kv: -kv[1]) if cores >= 0.01
    ]
    for tid in hot:
        n, c = max(1, counts[tid]), counters[tid]
        lines.append(f"thread {tid}: {counts[tid]} samples")
        lines.append("  modules: " + pct(c["modules"], n, top))
        lines.append("  inclusive exe functions: " + pct(c["inclusive"], n, 3 * top))
        lines.append("  exclusive exe functions: " + pct(c["exclusive"], n, top))
        if under is not None:
            lines.append(f"  under {under:06x}, calling: " + pct(c["under"], n, 2 * top))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warmup-seconds", type=int, default=120, help="game seconds to play before sampling")
    ap.add_argument("--seconds", type=float, default=10.0, help="wall seconds to sample")
    ap.add_argument("--no-observe", action="store_true", help="step only, to separate stepping from observing")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--under", help="hex RVA: also break down what this function spends its samples calling")
    add_workload_arguments(ap)
    a = ap.parse_args(argv)
    with GameSession(workload_config(a)) as session:
        session.reset()
        tune(session, a)
        for _ in range(a.warmup_seconds * 1000 // a.step_ms):
            session.step({0: []})
        game, stop, steps = session.game, threading.Event(), [0]

        def play():
            while not stop.is_set():
                if a.no_observe:
                    game.rpc.step(a.step_ms)
                else:
                    session.step({0: []})
                steps[0] += 1

        player = threading.Thread(target=play, daemon=True)
        player.start()
        report = sample(game._process_handle, game.pid, a.seconds, a.top, int(a.under, 16) if a.under else None)
        stop.set()
        player.join()
    print(f"{steps[0]} steps of {a.step_ms} ms during sampling ({steps[0] * a.step_ms / 1000 / a.seconds:.1f}x)")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
