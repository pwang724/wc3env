"""Disassemble Warcraft III.exe around RVAs (capstone, no other deps).

    python tools/disasm.py 4d7bb4 3fc4d0            # function containing each RVA
    python tools/disasm.py --at 3fc4d0 --n 40       # 40 instructions starting at RVA
    python tools/disasm.py --calls 3f4944           # only the call instructions in that function

Function start is found by scanning backwards for `push ebp; mov ebp, esp` (the exe keeps
frame pointers). Addresses print as RVAs so they match the hook log.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import capstone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wc3env.settings import settings

EXE = settings().game_exe


class Image:
    def __init__(self, path: Path):
        d = path.read_bytes()
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        nsec = struct.unpack_from("<H", d, pe + 6)[0]
        opt_size = struct.unpack_from("<H", d, pe + 20)[0]
        self.image_base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
        sec = pe + 24 + opt_size
        self.sections = []
        for i in range(nsec):
            o = sec + i * 40
            name = d[o : o + 8].rstrip(b"\0").decode(errors="replace")
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append((name, va, vsize, rptr, rsize))
        self.d = d

    def read(self, rva: int, n: int) -> bytes:
        for _, va, vsize, rptr, rsize in self.sections:
            if va <= rva < va + max(vsize, rsize):
                off = rptr + (rva - va)
                return self.d[off : off + n]
        raise ValueError(f"rva {rva:x} not in any section")

    def func_start(self, rva: int, limit: int = 0x4000) -> int:
        """Scan back for the standard prologue 55 8B EC (push ebp; mov ebp, esp)."""
        lo = max(rva - limit, 0)
        blob = self.read(lo, rva - lo + 1)
        i = len(blob) - 1
        while i >= 2:
            if blob[i - 2 : i + 1] == b"\x55\x8b\xec":
                return lo + i - 2
            i -= 1
        return rva


def disasm(img: Image, rva: int, n_bytes: int, md: capstone.Cs):
    code = img.read(rva, n_bytes)
    yield from md.disasm(code, img.image_base + rva)


def print_function(img: Image, rva: int, md: capstone.Cs, only_calls: bool, max_bytes: int = 0x3000):
    start = img.func_start(rva)
    print(f"; function at {start:x} (contains {rva:x})")
    for ins in disasm(img, start, max_bytes, md):
        r = ins.address - img.image_base
        mark = "=>" if r == rva else "  "
        line = f"{mark} {r:08x}  {ins.mnemonic:7s} {ins.op_str}"
        if only_calls:
            if ins.mnemonic == "call" or r == rva:
                print(line)
        else:
            print(line)
        if ins.mnemonic == "ret" and r >= rva:
            break
        # a second prologue means we ran into the next function
        if r > start and ins.bytes[:3] == b"\x55\x8b\xec":
            break


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("rvas", nargs="*", help="hex RVAs; prints the containing function")
    ap.add_argument("--at", help="hex RVA to start at")
    ap.add_argument("--n", type=int, default=40, help="instructions for --at")
    ap.add_argument("--calls", action="store_true", help="print only call instructions")
    ap.add_argument("--exe", default=str(EXE))
    a = ap.parse_args()
    img = Image(Path(a.exe))
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    if a.at:
        rva = int(a.at, 16)
        for i, ins in enumerate(disasm(img, rva, 0x400, md)):
            if i >= a.n:
                break
            print(f"   {ins.address - img.image_base:08x}  {ins.mnemonic:7s} {ins.op_str}")
        return
    for h in a.rvas:
        print_function(img, int(h, 16), md, a.calls)
        print()


if __name__ == "__main__":
    sys.exit(main())
