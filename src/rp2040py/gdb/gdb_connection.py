from collections.abc import Callable

from rp2040py.gdb.gdb_server import STOP_REPLY_SIGINT, STOP_REPLY_TRAP, GDBServer
from rp2040py.gdb.gdb_utils import gdb_checksum, gdb_message

__all__ = (
    "GDBConnection",
    "GDBResponseHandler",
)

GDBResponseHandler = Callable[[str], None]


class GDBConnection:
    def __init__(self, server: GDBServer, on_response: GDBResponseHandler):
        self.server = server
        self.on_response = on_response
        self.target = server.target
        self._buf = ""

        server.add_connection(self)
        on_response("+")

    def feed_data(self, data: str) -> None:
        on_response = self.on_response
        if data and ord(data[0]) == 3:
            self.server.info("BREAK")
            self.target.stop()
            on_response(gdb_message(STOP_REPLY_SIGINT))
            data = data[1:]

        self._buf += data
        while True:
            dolla = self._buf.find("$")
            hash_index = self._buf.find("#", dolla + 1)
            if dolla < 0 or hash_index < 0 or hash_index + 2 > len(self._buf):
                return
            cmd = self._buf[dolla + 1 : hash_index]
            cksum = self._buf[hash_index + 1 : hash_index + 3]
            self._buf = self._buf[hash_index + 2 :]
            if gdb_checksum(cmd) != cksum:
                self.server.warn(f"GDB checksum error in message: {cmd}")
                on_response("-")
            else:
                on_response("+")
                self.server.debug(f">{cmd}")
                response = self.server.process_gdb_message(cmd)
                if response:
                    self.server.debug(f"<{response}")
                    on_response(response)

    def on_breakpoint(self) -> None:
        try:
            self.on_response(gdb_message(STOP_REPLY_TRAP))
        except Exception:  # noqa: BLE001 - matches upstream's untyped `catch (e)`
            self.server.remove_connection(self)
