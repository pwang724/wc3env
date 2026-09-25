"""The executable supported by the native adapter. Verify before loading any hooks."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

GAME_VERSION = "1.29.2.9231"
EXE_SHA256 = "3f2ed0120d80578bf07e4423296dade1adfb959d59a2d20a7584224559570eed"


def verify_executable(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"No Warcraft III executable at {path}; set WC3_GAME_DIR (see .env.example)")
    data = path.read_bytes()
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        valid = (
            data[:2] == b"MZ"
            and data[pe : pe + 4] == b"PE\0\0"
            and struct.unpack_from("<H", data, pe + 4)[0] == 0x14C
            and struct.unpack_from("<H", data, pe + 24)[0] == 0x10B
        )
    except struct.error:
        valid = False
    digest = hashlib.sha256(data).hexdigest()
    if not valid or digest != EXE_SHA256:
        raise ValueError(
            f"Unsupported Warcraft executable: {path}. This adapter requires the verified "
            f"32-bit {GAME_VERSION} binary (SHA-256 {EXE_SHA256}); found {digest}. "
            "No process was started. Other binaries need a verified adapter."
        )
    return digest
