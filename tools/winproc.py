"""CPU and memory of a Windows process (or thread), for the measurement tools. Works under Wine too.

CPU comes from exact cycle counts: GetProcessTimes charges time on timer ticks and misses the
sub-millisecond bursts the game's threads run in. Where cycle counts are unavailable (Wine), process
and thread times are used instead; Wine takes them from Linux's own accounting, which is exact.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

MIB = 2**20
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
H, D, FT = wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.FILETIME)
k32.GetCurrentProcess.restype = H
k32.GetCurrentThread.restype = H
k32.QueryProcessCycleTime.argtypes = [H, ctypes.POINTER(ctypes.c_uint64)]
k32.QueryThreadCycleTime.argtypes = [H, ctypes.POINTER(ctypes.c_uint64)]
k32.GetProcessTimes.argtypes = [H, FT, FT, FT, FT]
k32.GetThreadTimes.argtypes = [H, FT, FT, FT, FT]


class ProcessMemory(ctypes.Structure):
    _fields_ = [("size", D), ("faults", D)] + [
        (name, ctypes.c_size_t)
        for name in (
            "peak_working",
            "working",
            "peak_paged",
            "paged",
            "peak_nonpaged",
            "nonpaged",
            "pagefile",
            "peak_pagefile",
            "private",
        )
    ]


class _Region(ctypes.Structure):
    _fields_ = [
        ("base", ctypes.c_void_p),
        ("allocation_base", ctypes.c_void_p),
        ("allocation_protect", D),
        ("partition", wintypes.WORD),
        ("size", ctypes.c_size_t),
        ("state", D),
        ("protect", D),
        ("type", D),
    ]


psapi.GetProcessMemoryInfo.argtypes = [H, ctypes.POINTER(ProcessMemory), D]
k32.VirtualQueryEx.argtypes = [H, ctypes.c_void_p, ctypes.POINTER(_Region), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


def memory(handle) -> ProcessMemory:
    m = ProcessMemory()
    m.size = ctypes.sizeof(m)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(m), m.size):
        raise ctypes.WinError(ctypes.get_last_error())
    return m


def memory_mib(handle) -> dict:
    """Private and resident memory, and the used part of the 32-bit address space (what finally runs out)."""
    m = memory(handle)
    used, address, region = 0, 0, _Region()
    while address < 2**32 and k32.VirtualQueryEx(handle, address, ctypes.byref(region), ctypes.sizeof(region)):
        if region.state != 0x10000:  # MEM_FREE
            used += region.size
        address = (region.base or 0) + region.size
    return {"private": m.private / MIB, "working": m.working / MIB, "address_space": used / MIB}


def _cycles(query, handle) -> int:
    cycles = ctypes.c_uint64()
    return cycles.value if query(handle, ctypes.byref(cycles)) else 0


def _times(query, handle) -> int:
    """User plus kernel time in 100 ns units."""
    t = [wintypes.FILETIME() for _ in range(4)]
    if not query(handle, *[ctypes.byref(x) for x in t]):
        raise ctypes.WinError(ctypes.get_last_error())
    return sum(x.dwHighDateTime << 32 | x.dwLowDateTime for x in t[2:])


class _Clock:
    """Raw CPU counts of processes or threads, and their rate per second of CPU."""

    def __init__(self, cycles, times, current):
        self.cycles, self.times = cycles, times
        # Calibrate on this process or thread spinning for 0.2 s; cycle counts may not exist (Wine).
        self.use_cycles = _cycles(cycles, current()) > 0
        c0, t0 = self.raw(current()), time.perf_counter()
        while time.perf_counter() - t0 < 0.2:
            pass
        self.rate = (self.raw(current()) - c0) / (time.perf_counter() - t0)

    def raw(self, handle) -> int:
        return _cycles(self.cycles, handle) if self.use_cycles else _times(self.times, handle)

    def seconds(self, handle) -> float:
        return self.raw(handle) / self.rate


_clocks: dict[str, _Clock] = {}


def cpu_seconds(handle) -> float:
    """CPU time a process has used so far."""
    if "process" not in _clocks:
        _clocks["process"] = _Clock(k32.QueryProcessCycleTime, k32.GetProcessTimes, k32.GetCurrentProcess)
    return _clocks["process"].seconds(handle)


def thread_cpu_seconds(handle) -> float:
    """CPU time a thread has used so far."""
    if "thread" not in _clocks:
        _clocks["thread"] = _Clock(k32.QueryThreadCycleTime, k32.GetThreadTimes, k32.GetCurrentThread)
    return _clocks["thread"].seconds(handle)
