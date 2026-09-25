"""List the JASS natives the exe registers: name, prototype, implementation RVA.

    python tools/natives.py                 # every native
    python tools/natives.py GetPlayerState Player   # just these

Registration sites push the prototype string, the name string and the function address
before calling the binder, so for each name string in .rdata we find the `push <name va>`
and read the neighbouring pushes. Checked against Preload (0x0a4410) and S2I (0x0a5840).
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from disasm import EXE, Image  # noqa: E402


def natives(img: Image) -> dict[str, tuple[str, int]]:
    d = img.d
    base = img.image_base

    # rva <-> file offset helpers over the sections
    def rva_of(off: int) -> int | None:
        for _, va, vsize, rptr, rsize in img.sections:
            if rptr <= off < rptr + rsize:
                return va + (off - rptr)
        return None

    def off_of(rva: int) -> int | None:
        for _, va, vsize, rptr, rsize in img.sections:
            if va <= rva < va + max(vsize, rsize):
                return rptr + (rva - va)
        return None

    # every `push imm32` whose target is a string, indexed by target
    pushes: dict[int, list[int]] = {}
    for m in re.finditer(rb"\x68(....)", d, re.S):
        (va,) = struct.unpack("<I", m.group(1))
        pushes.setdefault(va, []).append(m.start())

    out: dict[str, tuple[str, int]] = {}
    for m in re.finditer(rb"(?<=\x00)([A-Z][A-Za-z0-9]{2,40})(?=\x00)", d):
        name = m.group(1).decode()
        rva = rva_of(m.start())
        if rva is None or (base + rva) not in pushes:
            continue
        for site in pushes[base + rva]:
            # expect: push proto; push name; push fn   (5 bytes each)
            if d[site - 5] != 0x68 or d[site + 5] != 0x68:
                continue
            (proto_va,) = struct.unpack_from("<I", d, site - 4)
            (fn_va,) = struct.unpack_from("<I", d, site + 6)
            po = off_of(proto_va - base) if proto_va >= base else None
            if po is None or d[po : po + 1] != b"(":
                continue
            proto = d[po : d.index(b"\0", po)].decode(errors="replace")
            fo = off_of(fn_va - base) if fn_va >= base else None
            if fo is None:
                continue
            out[name] = (proto, fn_va - base)
            break
    return out


def main() -> None:
    img = Image(EXE)
    table = natives(img)
    want = sys.argv[1:]
    for name in sorted(table):
        if want and name not in want:
            continue
        proto, rva = table[name]
        print(f"{rva:08x}  {name:40s} {proto}")
    if not want:
        print(f"; {len(table)} natives")


if __name__ == "__main__":
    main()
