"""In-process game and transport fixtures for environment unit tests."""

from wc3env.fake_server import FakeServer
from wc3env.rpc import LocalTransport, RpcClient


def client(server=None):
    return RpcClient(LocalTransport(server or FakeServer()))


class FakeGame:
    def __init__(self, config, server=None):
        self.map = config.map
        self.server = server or FakeServer()
        self.rpc = client(self.server)
        self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            self.rpc.quit()
            self.rpc.close()
