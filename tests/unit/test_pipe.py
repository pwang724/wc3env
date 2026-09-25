"""HookPipe line framing, timeouts and close detection over an os.pipe (no game)."""

import os
import threading
import time
import unittest

from wc3env.pipe import HookPipe


class Duplex:
    """Reads from one os.pipe end, writes to another: what the game's pipe looks like to us."""

    def __init__(self, r, w):
        self.r, self.w = os.fdopen(r, "rb", 0), os.fdopen(w, "wb", 0)

    def read(self, n):
        return self.r.read(n)

    def write(self, b):
        return self.w.write(b)

    def close(self):
        self.r.close()
        self.w.close()


class PipeTest(unittest.TestCase):
    def setUp(self):
        game_r, host_w = os.pipe()  # host -> game
        host_r, game_w = os.pipe()  # game -> host
        self.game_in = os.fdopen(game_r, "rb", 0)
        self.game_out = os.fdopen(game_w, "wb", 0)
        self.pipe = HookPipe(0).attach(Duplex(host_r, host_w))

    def tearDown(self):
        # The reader thread sits in a blocking read; closing its end from another thread would
        # block on Windows, so end the stream from the writing side first.
        for f in (self.game_out, self.game_in):
            try:
                f.close()
            except OSError:
                pass
        self.pipe.close()

    def test_lines_split_across_chunks_and_crlf(self):
        self.game_out.write(b"pong\r\nin")
        self.game_out.write(b"fo pid=1\nsync 3\n")
        self.assertEqual([self.pipe.readline(1) for _ in range(3)], ["pong", "info pid=1", "sync 3"])

    def test_silence_is_a_timeout_not_a_hang(self):
        t0 = time.time()
        with self.assertRaises(TimeoutError):
            self.pipe.readline(timeout=0.2)
        self.assertLess(time.time() - t0, 1.0)

    def test_closed_pipe_drains_then_raises(self):
        self.game_out.write(b"last line\n")
        self.game_out.close()
        self.assertEqual(self.pipe.readline(1), "last line")
        with self.assertRaises(ConnectionError):
            self.pipe.readline(1)
        with self.assertRaises(ConnectionError):  # stays closed
            self.pipe.readline(1)


class OverlappedPipeTest(unittest.TestCase):
    """OverlappedPipeFile against a real named pipe served the way wc3hook.dll serves it:
    byte mode, one instance, a pending read must not block a concurrent write."""

    PATH = r"\\.\pipe\wc3hook-test-%d" % os.getpid()

    def setUp(self):
        import ctypes
        from ctypes import wintypes

        from wc3env.pipe import OverlappedPipeFile

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateNamedPipeW.restype = wintypes.HANDLE
        k.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        for fn in (k.ConnectNamedPipe, k.DisconnectNamedPipe, k.CloseHandle):
            fn.argtypes = [wintypes.HANDLE] + ([wintypes.LPVOID] if fn is k.ConnectNamedPipe else [])
            fn.restype = wintypes.BOOL
        k.ReadFile.argtypes = k.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPVOID,
        ]
        PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE_WAIT = 3, 0
        self.k = k
        self.server = k.CreateNamedPipeW(
            self.PATH, PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE_WAIT, 1, 1 << 16, 1 << 16, 0, None
        )
        self.assertNotEqual(self.server, ctypes.c_void_p(-1).value)
        self.client = OverlappedPipeFile(self.PATH)
        k.ConnectNamedPipe(self.server, None)

    def tearDown(self):
        self.client.close()
        self.k.DisconnectNamedPipe(self.server)
        self.k.CloseHandle(self.server)

    def _server_write(self, data: bytes):
        import ctypes
        from ctypes import wintypes

        n = wintypes.DWORD(0)
        self.assertTrue(self.k.WriteFile(self.server, data, len(data), ctypes.byref(n), None))

    def _server_read(self, n: int) -> bytes:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_string_buffer(n)
        got = wintypes.DWORD(0)
        self.assertTrue(self.k.ReadFile(self.server, buf, n, ctypes.byref(got), None))
        return buf.raw[: got.value]

    def test_write_while_a_read_is_pending(self):
        got = []
        t = threading.Thread(target=lambda: got.append(self.client.read(64)), daemon=True)
        t.start()
        time.sleep(0.1)  # the read is now pending on the handle
        self.client.write(b"step 250\n")  # must not block behind it
        self.assertEqual(self._server_read(64), b"step 250\n")
        self._server_write(b"ok\n")
        t.join(2)
        self.assertEqual(got, [b"ok\n"])

    def test_zero_length_server_write_is_not_eof(self):
        self._server_write(b"")
        self._server_write(b"sync 1\n")
        self.assertEqual(self.client.read(64), b"sync 1\n")

    def test_disconnect_reads_as_eof(self):
        self.k.DisconnectNamedPipe(self.server)
        self.assertEqual(self.client.read(64), b"")

    def test_write_deadline_when_server_does_not_read(self):
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.client.write(b"x" * (1 << 20), timeout=0.05)
        self.assertLess(time.monotonic() - start, 1)

    def test_close_cancels_pending_read(self):
        got = []
        thread = threading.Thread(target=lambda: got.append(self.client.read(64)))
        thread.start()
        self.client.close()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(got, [b""])


if __name__ == "__main__":
    unittest.main()
