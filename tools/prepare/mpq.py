"""Minimal ctypes binding to StormLib, for reading the game's MPQ archives (tools/prepare, tools/scripts).

StormLib is built from source into tools/StormLib/build (README, tools)."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from wc3env.settings import _read_dotenv

_DLL_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "StormLib" / "build" / "Release" / "StormLib.dll",
    Path(__file__).resolve().parents[1] / "StormLib.dll",
]

MPQ_OPEN_READ_ONLY = 0x00000100
MPQ_FILE_COMPRESS = 0x00000200
MPQ_FILE_REPLACEEXISTING = 0x80000000
MPQ_COMPRESSION_ZLIB = 0x02
MPQ_CREATE_ARCHIVE_V1 = 0x00000000
MPQ_CREATE_LISTFILE = 0x00100000
MPQ_CREATE_ATTRIBUTES = 0x00200000
SFILE_INVALID_SIZE = 0xFFFFFFFF


class _FindData(ctypes.Structure):
    _fields_ = [
        ("cFileName", ctypes.c_char * 1024),
        ("szPlainName", ctypes.c_char_p),
        ("dwHashIndex", wintypes.DWORD),
        ("dwBlockIndex", wintypes.DWORD),
        ("dwFileSize", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwCompSize", wintypes.DWORD),
        ("dwFileTimeLo", wintypes.DWORD),
        ("dwFileTimeHi", wintypes.DWORD),
        ("lcLocale", wintypes.DWORD),
    ]


def _load():
    configured = os.environ.get("STORMLIB_DLL") or _read_dotenv(Path.cwd() / ".env").get("STORMLIB_DLL")
    candidates = [Path(configured)] if configured else _DLL_CANDIDATES
    for p in candidates:
        if p.exists():
            lib = ctypes.WinDLL(str(p))
            break
    else:
        raise FileNotFoundError(
            f"StormLib.dll not found; set STORMLIB_DLL or build it in tools/StormLib; looked in {candidates}"
        )
    H = ctypes.c_void_p
    lib.SFileOpenArchive.argtypes = [ctypes.c_char_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(H)]
    lib.SFileOpenArchive.restype = wintypes.BOOL
    lib.SFileCreateArchive.argtypes = [ctypes.c_char_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(H)]
    lib.SFileCreateArchive.restype = wintypes.BOOL
    lib.SFileCloseArchive.argtypes = [H]
    lib.SFileCloseArchive.restype = wintypes.BOOL
    lib.SFileOpenFileEx.argtypes = [H, ctypes.c_char_p, wintypes.DWORD, ctypes.POINTER(H)]
    lib.SFileOpenFileEx.restype = wintypes.BOOL
    lib.SFileGetFileSize.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
    lib.SFileGetFileSize.restype = wintypes.DWORD
    lib.SFileReadFile.argtypes = [H, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    lib.SFileReadFile.restype = wintypes.BOOL
    lib.SFileCloseFile.argtypes = [H]
    lib.SFileCloseFile.restype = wintypes.BOOL
    lib.SFileHasFile.argtypes = [H, ctypes.c_char_p]
    lib.SFileHasFile.restype = wintypes.BOOL
    lib.SFileAddFileEx.argtypes = [H, ctypes.c_char_p, ctypes.c_char_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    lib.SFileAddFileEx.restype = wintypes.BOOL
    lib.SFileFindFirstFile.argtypes = [H, ctypes.c_char_p, ctypes.POINTER(_FindData), ctypes.c_char_p]
    lib.SFileFindFirstFile.restype = H
    lib.SFileFindNextFile.argtypes = [H, ctypes.POINTER(_FindData)]
    lib.SFileFindNextFile.restype = wintypes.BOOL
    lib.SFileFindClose.argtypes = [H]
    lib.SFileFindClose.restype = wintypes.BOOL
    lib.GetLastError.restype = wintypes.DWORD
    return lib


_lib = None


def lib():
    global _lib
    if _lib is None:
        _lib = _load()
    return _lib


def _check(ok, what):
    if not ok:
        raise OSError(f"{what} failed (StormLib error {lib().GetLastError()})")


class Archive:
    def __init__(self, path: str | os.PathLike, readonly: bool = True):
        self._h = ctypes.c_void_p()
        flags = MPQ_OPEN_READ_ONLY if readonly else 0
        _check(lib().SFileOpenArchive(str(path).encode(), 0, flags, ctypes.byref(self._h)), f"open {path}")

    @classmethod
    def create(cls, path: str | os.PathLike, max_files: int = 64) -> Archive:
        self = cls.__new__(cls)
        self._h = ctypes.c_void_p()
        flags = MPQ_CREATE_ARCHIVE_V1 | MPQ_CREATE_LISTFILE | MPQ_CREATE_ATTRIBUTES
        _check(lib().SFileCreateArchive(str(path).encode(), flags, max_files, ctypes.byref(self._h)), f"create {path}")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._h:
            lib().SFileCloseArchive(self._h)
            self._h = ctypes.c_void_p()

    def has(self, name: str) -> bool:
        return bool(lib().SFileHasFile(self._h, name.encode()))

    def list(self) -> list[str]:
        if not self.has("(listfile)"):
            return []
        return [n for n in self.read("(listfile)").decode(errors="replace").splitlines() if n]

    def read(self, name: str) -> bytes:
        fh = ctypes.c_void_p()
        _check(lib().SFileOpenFileEx(self._h, name.encode(), 0, ctypes.byref(fh)), f"open file {name}")
        try:
            hi = wintypes.DWORD(0)
            size = lib().SFileGetFileSize(fh, ctypes.byref(hi))
            if size == SFILE_INVALID_SIZE:
                raise OSError(f"size of {name} unavailable")
            buf = ctypes.create_string_buffer(size)
            got = wintypes.DWORD(0)
            _check(lib().SFileReadFile(fh, buf, size, ctypes.byref(got), None), f"read {name}")
            return buf.raw[: got.value]
        finally:
            lib().SFileCloseFile(fh)

    def add(self, local_path: str | os.PathLike, name: str, compress: bool = True):
        flags = MPQ_FILE_REPLACEEXISTING | (MPQ_FILE_COMPRESS if compress else 0)
        comp = MPQ_COMPRESSION_ZLIB if compress else 0
        _check(lib().SFileAddFileEx(self._h, str(local_path).encode(), name.encode(), flags, comp, comp), f"add {name}")
