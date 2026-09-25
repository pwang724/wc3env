"""Fake-server conformance and RPC client concurrency and deadlines."""

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests.fakes import client
from tests.rpc_contract import RpcContract
from wc3env.fake_server import FakeServer
from wc3env.rpc import LocalTransport, RpcClient


class FakeServerContractTest(RpcContract, unittest.TestCase):
    def make_client(self):
        return client()

    def test_two_moves_execute_in_order_and_stop_clears_remaining_orders(self):
        c = client()
        c.create_game("m", [{"slot": 0, "control": "agent"}])
        u = c.observe(0)["units"][1]
        first = {"unit_id": u["unit_id"], "command": "move", "arguments": {"x": u["x"] + 100, "y": u["y"]}}
        second = {**first, "arguments": {"x": u["x"] + 100, "y": u["y"] + 100, "queued": True}}
        self.assertEqual(c.act(0, [first, second])["rejected"], [])
        c.step(375)
        halfway = c.observe(0)["units"][1]
        self.assertEqual((halfway["x"], halfway["y"]), (u["x"] + 100, u["y"]))
        c.step(375)
        final = c.observe(0)["units"][1]
        self.assertEqual((final["x"], final["y"]), (u["x"] + 100, u["y"] + 100))
        c.act(0, [first, second])
        c.act(0, [{"unit_id": u["unit_id"], "command": "stop"}])
        c.step(1000)
        self.assertEqual(c.observe(0)["units"][1], final)


class ClientTest(unittest.TestCase):
    def test_shared_client_serializes_complete_round_trips(self):
        reading = threading.Event()
        release = threading.Event()
        overlap = threading.Event()
        second_started = threading.Event()

        class DelayedTransport(LocalTransport):
            def send(self, line, timeout=None):
                if reading.is_set() and not release.is_set():
                    overlap.set()
                super().send(line, timeout=timeout)

            def readline(self, timeout=None):
                if not reading.is_set():
                    reading.set()
                    if not release.wait(2):
                        raise TimeoutError("test did not release the first reply")
                return super().readline(timeout=timeout)

        server = FakeServer()
        rpc = RpcClient(DelayedTransport(server))

        def second_call():
            second_started.set()
            return rpc.info()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(rpc.info)
            try:
                self.assertTrue(reading.wait(1))
                second = pool.submit(second_call)
                self.assertTrue(second_started.wait(1))
                self.assertFalse(overlap.wait(0.1), "the second request stole the first call's pipe")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=1), second.result(timeout=1))
        self.assertEqual([r["id"] for r in server.log], [1, 2])

    def test_rpc_noise_does_not_restart_deadline(self):
        class Noise:
            def send(self, line, timeout):
                pass

            def readline(self, timeout):
                time.sleep(min(timeout, 0.01))
                return "diagnostic"

        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            RpcClient(Noise(), timeout=0.05).info()
        self.assertLess(time.monotonic() - start, 0.5)


if __name__ == "__main__":
    unittest.main()
