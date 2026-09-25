"""The bundled x86 game hook needs a Windows wheel; Python itself must be 64-bit."""

import struct
from pathlib import Path

from setuptools import setup
from wheel.bdist_wheel import bdist_wheel


class WindowsWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        return "py3", "none", "win_amd64"

    def run(self):
        path = Path("src/wc3env/native/wc3hook.dll")
        if not path.is_file():
            raise RuntimeError("Supply the prebuilt hook DLL or build wc3hook/build.bat on Windows")
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
        if not valid:
            raise RuntimeError("The bundled hook must be a Windows x86 PE DLL")
        super().run()


setup(cmdclass={"bdist_wheel": WindowsWheel})
