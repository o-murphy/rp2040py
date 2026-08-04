import socket
import threading

from rp2040py.gdb.gdb_connection import GDBConnection
from rp2040py.gdb.gdb_server import GDBServer
from rp2040py.gdb.gdb_target import IGDBTarget

__all__ = ("GDBTCPServer",)


_ACCEPT_POLL_SECONDS = 0.2


class GDBTCPServer(GDBServer):
    def __init__(self, target: IGDBTarget, port: int = 3333):
        super().__init__(target)
        self.port = port
        self._socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket_server.bind(("", port))
        self._socket_server.listen()
        # A short accept() timeout (instead of blocking forever) is what makes _closing
        # checkable from _accept_loop at all - closing _socket_server from another thread while
        # accept() is blocked on it does NOT reliably unblock that call on Linux (a well-known
        # glibc/kernel gotcha for blocking accept() specifically - confirmed the hard way: an
        # earlier close()-only version of this hung indefinitely instead of returning). Polling
        # avoids that race entirely instead of trying to interrupt a blocking syscall from
        # outside it.
        self._socket_server.settimeout(_ACCEPT_POLL_SECONDS)
        self._closing = threading.Event()
        # NOTE: not a daemon thread on purpose - a listening GDB server should keep the process
        # alive by itself, matching Node's `net.Server.listen()` semantics in upstream rp2040js.
        # That means a plain `sys.exit()`/return from main() hangs interpreter shutdown forever
        # joining this thread unless something first tells it to stop - see close().
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=False)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while not self._closing.is_set():
            try:
                client_socket, _addr = self._socket_server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self.handle_connection, args=(client_socket,), daemon=True).start()

    def close(self, timeout: "float | None" = 5.0) -> None:
        """Stops the accept thread and joins it, so the process can exit normally afterward (a
        plain `sys.exit()`/return would otherwise hang forever joining that non-daemon thread -
        see its own NOTE). Joining (rather than just setting the flag and trusting it) is what
        actually makes this safe to rely on elsewhere instead of falling back to `os._exit()`
        again. Idempotent - safe to call more than once (e.g. once from the CLI's normal shutdown
        path, once from a `finally`)."""
        self._closing.set()
        self._accept_thread.join(timeout=timeout)
        self._socket_server.close()

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
