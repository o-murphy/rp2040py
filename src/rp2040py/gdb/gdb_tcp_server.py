import asyncio
import socket

from rp2040py.gdb.gdb_connection import GDBConnection
from rp2040py.gdb.gdb_server import GDBServer
from rp2040py.gdb.gdb_target import IGDBTarget

__all__ = ("GDBTCPServer",)


class GDBTCPServer(GDBServer):
    """Listens for GDB client connections on `target`'s own engine-room loop (docs/
    MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape") - not a separate dedicated thread the way
    this used to work (see git history/ASYNCIO_MIGRATION_BACKLOG.md for that older design):
    `process_gdb_message()` (invoked from `feed_data()`, called from `_handle_connection()` below)
    reads/writes `core.registers`/`rp2040` memory directly, the same state `execute()` mutates -
    since both now run as plain coroutines/tasks on one shared loop (cooperative scheduling, only
    one truly runs at a time), that's already safe with no cross-thread bridge needed, unlike when
    they lived on two independent loops/threads. Construct, then `await start()` from whatever
    loop `target.execute()` itself will run on (before or after - `start()` just needs to be
    awaited from that same loop eventually, not necessarily strictly before/after
    `target.bind_loop()`/`start_execution()`); `await close()` to stop listening and tear down.

    A caller with no async context of its own (embedding this in an otherwise-synchronous
    program) needs to host it on its own dedicated loop/thread - see
    docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "What still needs a real thread" - same as any other
    plain `async def start()`/`close()` component in this codebase now.
    """

    def __init__(self, target: IGDBTarget, port: int = 3333):
        super().__init__(target)
        self._requested_port = port
        self._server: asyncio.Server | None = None
        # Tracked so close() can cancel still-open connections before the server itself closes -
        # otherwise a client connected at shutdown time leaves its _handle_connection task pending
        # forever, which asyncio reports as "Task was destroyed but it is pending!" (harmless in
        # effect, but a needless, confusing warning on every shutdown with an open connection).
        self._connection_tasks: set[asyncio.Task[None]] = set()
        # Resolved once start() has actually bound the listening socket (self._requested_port may
        # be 0, meaning "OS picks a free one") - None until then.
        self.port: int | None = None

    async def start(self) -> None:
        # family=AF_INET (not host="" alone): an empty/unset host lets asyncio's own getaddrinfo()
        # resolve to *multiple* addresses (e.g. both an IPv6 "::" and an IPv4 "0.0.0.0" socket),
        # each bound independently - with port=0 (OS-assigned), each of those separate bind()
        # calls can get a *different* port number. `self.port` below only reads sockets[0], so an
        # IPv4 client connecting to that port would intermittently hit the wrong (IPv6) socket's
        # port instead - confirmed flaky (~50% of runs) before pinning the family. The original
        # socket-based implementation was always a single explicit AF_INET socket; this preserves
        # that instead of accidentally dual-stack.
        self._server = await asyncio.start_server(
            self._handle_connection, host="0.0.0.0", port=self._requested_port, family=socket.AF_INET
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self, timeout: "float | None" = 5.0) -> None:
        """Stops the listening server and any open connections. Idempotent - safe to call more
        than once (e.g. once from the CLI's normal shutdown path, once from a `finally`).

        Each stage gets its own bounded slice of `timeout` instead of one unbounded await each - a
        single stuck connection task (or a slow `wait_closed()` on an unusual platform) used to be
        able to consume the *entire* budget on its own, leaving nothing for the other stage."""
        if self._server is None:
            return
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=timeout)
        except TimeoutError:
            pass
        for task in self._connection_tasks:
            task.cancel()
        if self._connection_tasks:
            try:
                pending = asyncio.gather(*self._connection_tasks, return_exceptions=True)
                await asyncio.wait_for(pending, timeout=timeout)
            except TimeoutError:
                pass
        self._server = None
        self.port = None

    async def _handle_connection(self, reader: "asyncio.StreamReader", writer: "asyncio.StreamWriter") -> None:
        self.info("GDB connected")
        client_socket = writer.get_extra_info("socket")
        if client_socket is not None:
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # A plain, direct writer.write() - not bridged via call_soon_threadsafe() the way this
        # used to be: on_response() is also handed to GDBConnection for on_breakpoint()
        # (gdb_connection.py), invoked synchronously from GDBServer.add_connection()'s _on_break -
        # which runs on the *target's* engine-room call chain (a real breakpoint hit inside
        # execute()'s own call stack). That's now the same loop/thread this connection handler
        # itself runs on, so a direct call is already safe - StreamWriter.write() is a plain,
        # reentrant-safe synchronous buffer append, fine to call from any synchronous callback
        # already running on the loop's own thread, nested or not.
        def on_response(data: str) -> None:
            writer.write(data.encode("utf-8"))

        connection = GDBConnection(self, on_response)

        task = asyncio.current_task()
        assert task is not None  # always run as a Task, by start_server()'s own contract
        self._connection_tasks.add(task)

        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                # Direct, no cross-thread bridge needed: this coroutine already runs on the
                # target's own engine-room loop (see the class docstring), and process_gdb_message()
                # (invoked synchronously from within feed_data()) touching core.registers/memory
                # is already safe here.
                connection.feed_data(data.decode("utf-8"))
        except OSError as err:
            self.remove_connection(connection)
            self.error(f"GDB socket error {err}")
            return
        finally:
            self._connection_tasks.discard(task)

        self.remove_connection(connection)
        self.info("GDB disconnected")
