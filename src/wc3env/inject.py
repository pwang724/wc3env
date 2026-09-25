"""Launch a process suspended, inject a DLL, resume — in pure Python via ctypes.

A freshly built helper .exe can trip Windows Application Control, but python.exe is already
trusted, so the CreateProcess/VirtualAllocEx/CreateRemoteThread dance runs here. The child
is 32-bit but this Python is 64-bit, so our own LoadLibraryW address is the wrong bitness. We
instead compute the 32-bit LoadLibraryW: the child's 32-bit kernel32 base (via
EnumProcessModulesEx with LIST_MODULES_32BIT) plus LoadLibraryW's RVA read from the export
table of the on-disk SysWOW64\\kernel32.dll.
"""

from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from .compatibility import verify_executable

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

# Default ctypes return type is c_int (32-bit); pointer/handle returns must be widened to avoid
# truncation in this 64-bit process.
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
k32.CreateRemoteThread.restype = wintypes.HANDLE
k32.OpenProcess.restype = wintypes.HANDLE
k32.CreateEventW.restype = wintypes.HANDLE
k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CreateRemoteThread.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
]
k32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
k32.WriteProcessMemory.restype = wintypes.BOOL
k32.GetExitCodeThread.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
k32.GetExitCodeThread.restype = wintypes.BOOL
k32.ResumeThread.argtypes = [wintypes.HANDLE]
k32.ResumeThread.restype = wintypes.DWORD
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.VirtualFreeEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD]
psapi.EnumProcessModulesEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPDWORD,
    wintypes.DWORD,
]
psapi.EnumProcessModulesEx.restype = wintypes.BOOL
psapi.GetModuleBaseNameW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
psapi.GetModuleBaseNameW.restype = wintypes.DWORD


def _export_rva(dll_path: str, name: bytes) -> int:
    """RVA of an exported symbol, parsed straight from a PE file on disk."""
    d = Path(dll_path).read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    # export directory RVA lives at a magic-dependent offset in the data directories
    dd = opt + (96 if magic == 0x10B else 112)
    exp_rva, exp_size = struct.unpack_from("<II", d, dd)
    # section table -> map RVA to file offset
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    secs = []
    for i in range(nsec):
        s = pe + 24 + optsz + 40 * i
        va, rs, ro = struct.unpack_from("<III", d, s + 12)[0], *struct.unpack_from("<II", d, s + 16)
        secs.append((va, rs, ro))

    def off(rva):
        for va, rs, ro in secs:
            if va <= rva < va + rs:
                return rva - va + ro
        raise ValueError("rva not in any section")

    e = off(exp_rva)
    nnames, addr_rva, names_rva, ord_rva = (struct.unpack_from("<I", d, e + o)[0] for o in (24, 28, 32, 36))
    for i in range(nnames):
        nptr = struct.unpack_from("<I", d, off(names_rva) + 4 * i)[0]
        s = off(nptr)
        end = d.index(b"\0", s)
        if d[s:end] == name:
            oi = struct.unpack_from("<H", d, off(ord_rva) + 2 * i)[0]
            address = struct.unpack_from("<I", d, off(addr_rva) + 4 * oi)[0]
            if exp_rva <= address < exp_rva + exp_size:
                raise ValueError(f"Forwarded export {name!r} is unsupported in {dll_path}")
            return address
    raise KeyError(name)


def _kernel32_32_base(hproc) -> int:
    """Base address of the 32-bit kernel32 loaded in a running WOW64 process."""
    LIST_MODULES_32BIT = 0x01
    needed = wintypes.DWORD(0)
    arr = (ctypes.c_void_p * 1024)()
    for _ in range(200):
        if psapi.EnumProcessModulesEx(hproc, arr, ctypes.sizeof(arr), ctypes.byref(needed), LIST_MODULES_32BIT):
            count = min(len(arr), needed.value // ctypes.sizeof(ctypes.c_void_p))
            for i in range(count):
                name = ctypes.create_unicode_buffer(260)
                if not psapi.GetModuleBaseNameW(hproc, arr[i], name, 260):
                    continue
                if name.value.lower() == "kernel32.dll":
                    return arr[i]
        time.sleep(0.01)  # modules may not all be mapped yet in a just-created process
    raise OSError("kernel32.dll (32-bit) not found in process")


_k32_cache: int | None = None


def kernel32_32_base() -> int:
    """The 32-bit kernel32 base is the same in every WOW64 process for the life of a boot
    (ASLR is per boot, per image). A suspended child has only ntdll mapped, so we learn the
    value once from a throwaway 32-bit process (ping) and reuse it."""
    global _k32_cache
    if _k32_cache:
        return _k32_cache
    import subprocess

    ping = os.path.join(os.environ["WINDIR"], "SysWOW64", "ping.exe")
    p = subprocess.Popen(
        [ping, "-n", "3", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        h = k32.OpenProcess(0x0410, False, p.pid)  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _k32_cache = _kernel32_32_base(h)
        finally:
            k32.CloseHandle(h)
    finally:
        p.kill()
        p.wait()
    return _k32_cache


CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
MEM_COMMIT_RESERVE = 0x3000
PAGE_READWRITE = 0x04
INFINITE = 0xFFFFFFFF


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


k32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFO),
    ctypes.POINTER(PROCESS_INFORMATION),
]
k32.CreateProcessW.restype = wintypes.BOOL


def _env_block(extra: dict[str, str]) -> ctypes.Array:
    """Unicode environment block for CreateProcess: this process's environment plus `extra`."""
    merged = {**os.environ, **extra}
    return ctypes.create_unicode_buffer(
        "".join(f"{k}={v}\0" for k, v in sorted(merged.items(), key=lambda kv: kv[0].upper())) + "\0"
    )


def launch_with_dll(
    exe: Path, dll: Path, args: list[str], ready_timeout: float = 15.0, env: dict[str, str] | None = None
) -> tuple[int, int]:
    """Start `exe` suspended, inject `dll`, wait until the DLL signals that its hooks are installed
    (event `wc3hook-ready-<pid>`), then resume. Every instruction of the game therefore runs hooked,
    including the single-instance guards in WinMain. `env` adds variables for the child only, so
    concurrent launches do not race on os.environ. Returns (pid, process_handle); the caller must close the handle. Raises on failure."""
    exe, dll = exe.resolve(), dll.resolve()
    digest = verify_executable(exe)
    if not dll.is_file():
        raise FileNotFoundError(f"Build wc3hook/build.bat first, or set WC3_HOOK_DLL: {dll}")
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("The launcher requires 64-bit Python on 64-bit Windows")
    cmd = subprocess.list2cmdline([str(exe), *args])
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    load = kernel32_32_base() + _export_rva(
        os.path.join(os.environ["WINDIR"], "SysWOW64", "kernel32.dll"), b"LoadLibraryW"
    )
    block = _env_block({**(env or {}), "WC3HOOK_EXE_HASH": digest})
    if not k32.CreateProcessW(
        str(exe),
        ctypes.create_unicode_buffer(cmd),
        None,
        None,
        False,
        CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
        block,
        str(exe.parent),
        ctypes.byref(si),
        ctypes.byref(pi),
    ):
        raise OSError(f"CreateProcess failed {ctypes.get_last_error()}")
    ready = k32.CreateEventW(None, True, False, f"wc3hook-ready-{pi.dwProcessId}")
    thread = mem = None
    try:
        if not ready:
            raise ctypes.WinError(ctypes.get_last_error())
        raw = str(dll).encode("utf-16-le") + b"\0\0"
        mem = k32.VirtualAllocEx(pi.hProcess, None, len(raw), MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not mem:
            raise OSError(f"VirtualAllocEx failed {ctypes.get_last_error()}")
        written = ctypes.c_size_t(0)
        if not k32.WriteProcessMemory(pi.hProcess, mem, raw, len(raw), ctypes.byref(written)) or written.value != len(
            raw
        ):
            raise OSError(f"WriteProcessMemory failed or wrote an incomplete DLL path: {ctypes.get_last_error()}")
        thread = k32.CreateRemoteThread(pi.hProcess, None, 0, ctypes.c_void_p(load), ctypes.c_void_p(mem), 0, None)
        if not thread:
            raise OSError(f"CreateRemoteThread failed {ctypes.get_last_error()}")
        if k32.WaitForSingleObject(thread, 15000) != 0:
            raise TimeoutError("The DLL loader thread did not finish in 15 s")
        code = wintypes.DWORD(0)
        if not k32.GetExitCodeThread(thread, ctypes.byref(code)) or not code.value:
            raise OSError("LoadLibraryW in child failed to load the DLL")
        k32.VirtualFreeEx(pi.hProcess, mem, 0, 0x8000)
        mem = None
        if k32.WaitForSingleObject(ready, int(ready_timeout * 1000)) != 0:
            raise OSError("wc3hook.dll never signalled ready")
        if k32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        handle = pi.hProcess
        pi.hProcess = None  # transfer ownership to Game
        return pi.dwProcessId, handle
    except Exception:
        k32.TerminateProcess(pi.hProcess, 1)
        raise
    finally:
        if thread:
            k32.CloseHandle(thread)
        if ready:
            k32.CloseHandle(ready)
        k32.CloseHandle(pi.hThread)
        if pi.hProcess:
            k32.CloseHandle(pi.hProcess)
