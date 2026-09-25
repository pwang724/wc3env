"""Line-oriented client for the named pipe served by wc3hook.dll inside the game process.

One UTF-8 line per message in each direction: the RPC's JSON (rpc.py) and the DLL's
diagnostic lines (`native ...`, `scan ...`), which the RPC client skips.

A reader thread pulls bytes off the pipe and queues whole lines, so every `readline` has a
timeout: a game that stops talking raises `TimeoutError` instead of hanging the host forever,
and a game that exits raises `ConnectionError` once the queued lines are drained.
"""

from __future__ import annotations

import collections
import ctypes
import math
import threading
import time
from ctypes import wintypes
from typing import BinaryIO

_CLOSED = object()


class OverlappedPipeFile:
    """A named-pipe client handle opened for overlapped I/O, with blocking read/write on top.

    A synchronous pipe handle admits one operation at a time, so a reader thread parked in
    ReadFile would block every WriteFile from another thread. Overlapped I/O lets the read
    stay pending while writes go through."""

    _GENERIC_RW = 0x80000000 | 0x40000000
    _OPEN_EXISTING = 3
    _FLAG_OVERLAPPED = 0x40000000
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_IO_PENDING = 997
    _DISCONNECTED = (
        6,
        109,
        233,
        995,
    )  # ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_OPERATION_ABORTED (our close)
    _INVALID = ctypes.c_void_p(-1).value  # INVALID_HANDLE_VALUE as an unsigned pointer

    def __init__(self, path: str):
        k = _kernel32()
        h = k.CreateFileW(path, self._GENERIC_RW, 0, None, self._OPEN_EXISTING, self._FLAG_OVERLAPPED, None)
        if h == self._INVALID:
            err = ctypes.get_last_error()
            if err == self._ERROR_FILE_NOT_FOUND:
                raise FileNotFoundError(path)
            raise OSError(err, f"CreateFile({path}) failed: {err}")
        self._io_lock = threading.Lock()
        self._h = h
        self._k = k

    def _io(self, fn, buf, n: int, timeout: float | None = None) -> int | None:
        """Bytes transferred, or None once the pipe is disconnected."""
        ov = _OVERLAPPED()
        ov.hEvent = self._k.CreateEventW(None, True, False, None)
        if not ov.hEvent:
            raise ctypes.WinError(ctypes.get_last_error())
        done = wintypes.DWORD(0)
        try:
            with self._io_lock:
                handle = self._h
                if handle is None:
                    return None
                ok = fn(handle, buf, n, ctypes.byref(done), ctypes.byref(ov))
                err = ctypes.get_last_error()
            if not ok:
                if err != self._ERROR_IO_PENDING:
                    if err in self._DISCONNECTED:
                        return None
                    raise OSError(err, f"pipe I/O failed: {err}")
                if timeout is not None:
                    waited = self._k.WaitForSingleObject(ov.hEvent, min(0xFFFFFFFE, max(0, math.ceil(timeout * 1000))))
                    if waited != 0:
                        self._k.CancelIoEx(handle, ctypes.byref(ov))
                        # OVERLAPPED and its buffer must stay alive until cancellation completes.
                        self._k.WaitForSingleObject(ov.hEvent, 0xFFFFFFFF)
                        raise TimeoutError("pipe write deadline expired")
                self._k.WaitForSingleObject(ov.hEvent, 0xFFFFFFFF)
                if not self._k.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(done), True):
                    err = ctypes.get_last_error()
                    if err in self._DISCONNECTED:
                        return None
                    raise OSError(err, f"pipe I/O failed: {err}")
            return done.value
        finally:
            self._k.CloseHandle(ov.hEvent)

    def read(self, n: int) -> bytes:
        """Bytes available, at most n; b"" only once the server has disconnected. A zero-length
        WriteFile on the server side surfaces as a successful zero-byte read and is not EOF."""
        buf = ctypes.create_string_buffer(n)
        while True:
            got = self._io(self._k.ReadFile, buf, n)
            if got is None:
                return b""
            if got:
                return buf.raw[:got]

    def write(self, data: bytes, timeout: float = 120.0) -> int:
        n = self._io(self._k.WriteFile, data, len(data), timeout)
        if n is None:
            raise BrokenPipeError("pipe disconnected")
        return n

    def close(self) -> None:
        with self._io_lock:
            if self._h is not None:
                handle, self._h = self._h, None
                self._k.CancelIoEx(handle, None)
                self._k.CloseHandle(handle)


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


_K = None


def _kernel32():
    """kernel32 with the signatures this module uses (64-bit handles need explicit types)."""
    global _K
    if _K is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        H, D, P, B = wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.BOOL
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, D, D, P, D, D, H]
        k.CreateFileW.restype = H
        k.CreateEventW.argtypes = [P, B, B, wintypes.LPCWSTR]
        k.CreateEventW.restype = H
        k.ReadFile.argtypes = [H, P, D, wintypes.LPDWORD, P]
        k.ReadFile.restype = B
        k.WriteFile.argtypes = [H, P, D, wintypes.LPDWORD, P]
        k.WriteFile.restype = B
        k.GetOverlappedResult.argtypes = [H, P, wintypes.LPDWORD, B]
        k.GetOverlappedResult.restype = B
        k.CancelIoEx.argtypes = [H, P]
        k.CancelIoEx.restype = B
        k.CloseHandle.argtypes = [H]
        k.CloseHandle.restype = B
        k.WaitForSingleObject.argtypes = [H, D]
        k.WaitForSingleObject.restype = D
        _K = k
    return _K


class HookPipe:
    def __init__(self, pid: int):
        self.path = r"\\.\pipe\wc3hook-%d" % pid
        self._f: BinaryIO | None = None
        self._lines: collections.deque = collections.deque()
        self._new = threading.Condition()
        self._reader: threading.Thread | None = None
        self._closed_reason = "end of stream"

    def connect(self, timeout: float = 30.0) -> HookPipe:
        deadline = time.monotonic() + timeout
        while True:
            try:
                f = OverlappedPipeFile(self.path)
                break
            except FileNotFoundError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"pipe {self.path} never appeared") from None
                time.sleep(0.2)
        return self.attach(f)

    def attach(self, f: BinaryIO) -> HookPipe:
        """Use an already-open duplex byte stream (tests hand in one end of an os.pipe)."""
        self._f = f
        self._reader = threading.Thread(target=self._read_loop, name="hookpipe-reader", daemon=True)
        self._reader.start()
        return self

    def _read_loop(self) -> None:
        f, buf = self._f, b""  # local: close() drops self._f while this read is pending
        try:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._put(line.decode("utf-8", errors="replace").rstrip("\r"))
        except (OSError, ValueError) as exc:  # pipe broken, or closed under us
            self._closed_reason = f"{type(exc).__name__}: {exc}"
        self._put(_CLOSED)

    def _put(self, item) -> None:
        with self._new:
            self._lines.append(item)
            self._new.notify_all()

    def send(self, line: str, timeout: float = 120.0) -> None:
        data = line.encode("utf-8") + b"\n"
        if len(data) > 65535:
            raise ValueError("RPC request exceeds the 65535-byte frame limit")
        if self._f is None:
            raise ConnectionError("pipe is closed")
        if isinstance(self._f, OverlappedPipeFile):
            written = self._f.write(data, timeout)
        else:
            written = self._f.write(data)
        if written != len(data):
            raise ConnectionError("incomplete pipe write; connection cannot be reused")

    def readline(self, timeout: float | None = 120.0) -> str:
        """Next line from the game, in arrival order. Raises TimeoutError after `timeout` seconds
        of silence (None waits forever), ConnectionError once the game has closed the pipe."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._new:
            while True:
                if self._lines:
                    item = self._lines[0]
                    if item is _CLOSED:
                        raise ConnectionError(f"pipe closed ({self._closed_reason})")  # left in place: closed for good
                    return self._lines.popleft()
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"no line from the game in {timeout} s")
                self._new.wait(remaining)

    def close(self) -> None:
        """Close the stream and wait briefly for the reader thread to notice (a pending read
        on the real pipe is cancelled by the close; on a test pipe the writer end must be
        closed first)."""
        self._put(_CLOSED)
        if self._f:
            self._f.close()
            self._f = None
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=2.0)


if __name__ == "__main__":
    import sys

    p = HookPipe(int(sys.argv[1])).connect(5)
    for cmd in ["ping", "info"]:
        p.send(cmd)
        print(cmd, "->", p.readline(5))
    p.close()
