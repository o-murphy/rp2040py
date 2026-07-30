import socket
import threading

from rp2040py.gdb.gdb_connection import GDBConnection
from rp2040py.gdb.gdb_server import GDBServer
from rp2040py.gdb.gdb_target import IGDBTarget

__all__ = ("GDBTCPServer",)


class GDBTCPServer(GDBServer):
    def __init__(self, target: IGDBTarget, port: int = 3333):
        super().__init__(target)
        self.port = port
        self._socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket_server.bind(("", port))
        self._socket_server.listen()
        # NOTE: not a daemon thread on purpose - a listening GDB server should keep the process
        # alive by itself, matching Node's `net.Server.listen()` semantics in upstream rp2040js.
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=False)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while True:
            try:
                client_socket, _addr = self._socket_server.accept()
            except OSError:
                return
            threading.Thread(target=self.handle_connection, args=(client_socket,), daemon=True).start()

    def handle_connection(self, client_socket: socket.socket) -> None:
        self.info("GDB connected")
        client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        connection = GDBConnection(self, lambda data: client_socket.sendall(data.encode("utf-8")))

        try:
            while True:
                data = client_socket.recv(4096)
                if not data:
                    break
                connection.feed_data(data.decode("utf-8"))
        except OSError as err:
            self.remove_connection(connection)
            self.error(f"GDB socket error {err}")
            return

        self.remove_connection(connection)
        self.info("GDB disconnected")
