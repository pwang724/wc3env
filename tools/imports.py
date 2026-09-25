"""Resolve IAT slots of Warcraft III.exe to dll!name, so `call dword ptr [0xe661a8]` in a
disassembly can be read.

    python tools/imports.py e661a8 e6625c        # VAs or RVAs (hex); VAs have the 0x400000 base
    python tools/imports.py --all | grep -i wait
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wc3env.settings import settings

EXE = settings().game_exe


def iat_map(path: Path) -> tuple[dict[int, str], int]:
    d = path.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    opt_size = struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
    secs = []
    for i in range(nsec):
        o = pe + 24 + opt_size + i * 40
        vs, va, rs, rp = struct.unpack_from("<IIII", d, o + 8)
        secs.append((va, max(vs, rs), rp))

    def off(rva: int) -> int:
        for va, size, rp in secs:
            if va <= rva < va + size:
                return rp + rva - va
        raise ValueError(hex(rva))

    def cstr(rva: int) -> str:
        o = off(rva)
        return d[o : d.index(b"\0", o)].decode(errors="replace")

    imp_rva = struct.unpack_from("<I", d, pe + 24 + 104)[0]  # data directory 1: import table
    out: dict[int, str] = {}
    o = off(imp_rva)
    while True:
        ilt, _, _, name_rva, iat = struct.unpack_from("<IIIII", d, o)
        if not ilt and not iat:
            break
        dll = cstr(name_rva)
        thunk = ilt or iat
        i = 0
        while True:
            e = struct.unpack_from("<I", d, off(thunk) + i * 4)[0]
            if not e:
                break
            name = f"#{e & 0xFFFF}" if e & 0x80000000 else cstr(e + 2)
            out[iat + i * 4] = f"{dll}!{name}"
            i += 1
        o += 20
    return out, base


def main() -> None:
    m, base = iat_map(EXE)
    args = sys.argv[1:]
    if args == ["--all"]:
        for rva in sorted(m):
            print(f"{rva:08x}  {m[rva]}")
        return
    for a in args:
        v = int(a, 16)
        rva = v - base if v >= base else v
        print(f"{a}: {m.get(rva, '?')}")


if __name__ == "__main__":
    main()
