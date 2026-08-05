import asyncio
import socket

from rp2040py.gdb.gdb_connection import GDBConnection
from rp2040py.gdb.gdb_server import GDBServer
from rp2040py.gdb.gdb_target import IGDBTarget
from rp2040py.utils.asyncio_loop_thread import start_loop_thread

__all__ = ("GDBTCPServer",)


class GDBTCPServer(GDBServer):
    def __init__(self, target: IGDBTarget, port: int = 3333):
        super().__init__(target)
        # Own dedicated loop/thread, not shared with `target`'s (Simulator's own engine room, see
        # simulator.py): connection I/O (accepting a socket, reading bytes) has nothing to do with
        # CPU/peripheral state, so there's no shared-state reason to put it on the same loop.
        # Message *processing* (process_gdb_message(), touching core/rp2040 state) does need
        # Simulator's engine room though - bridged per-message via target.acall() in
        # _handle_connection() below instead, rather than moving this whole loop onto
        # Simulator's - see docs/ASYNCIO_MIGRATION_BACKLOG.md's "Resolved during PR 5". NOT a
        # daemon thread on purpose - a listening GDB server should keep the process alive by itself,
        # matching Node's `net.Server.listen()` semantics in upstream rp2040js. That means a plain
        # `sys.exit()`/return from main() hangs interpreter shutdown forever joining this thread
        # unless something first tells it to stop - see close().
        self._loop, self._loop_thread = start_loop_thread(daemon=False)
        self._server: asyncio.Server = asyncio.run_coroutine_threadsafe(self._start_server(port), self._loop).result()
        # port=0 asks the OS to pick a free one - reflect what was actually bound, not the
        # constructor argument verbatim (a stale echo of 0 was a latent bug; callers used to work
        # around it by reaching into the underlying socket directly instead of `self.port`).
        self.port: int = self._server.sockets[0].getsockname()[1]
        # Tracked so close() can cancel still-open connections before stopping the loop out from
        # under them - otherwise a client connected at shutdown time leaves its _handle_connection
        # task blocked in reader.read() forever, which asyncio reports as "Task was destroyed but
        # it is pending!" once the loop stops (harmless in effect - matches the old per-connection
        # daemon thread just being abandoned - but a needless, confusing warning on every shutdown
        # with an open connection).
        self._connection_tasks: set[asyncio.Task[None]] = set()

    async def _start_server(self, port: int) -> asyncio.Server:
        # family=AF_INET (not host="" alone): an empty/unset host lets asyncio's own getaddrinfo()
        # resolve to *multiple* addresses (e.g. both an IPv6 "::" and an IPv4 "0.0.0.0" socket),
        # each bound independently - with port=0 (OS-assigned), each of those separate bind()
        # calls can get a *different* port number. `self.port` below only reads sockets[0], so an
        # IPv4 client connecting to that port would intermittently hit the wrong (IPv6) socket's
        # port instead - confirmed flaky (~50% of runs) before pinning the family. The original
        # socket-based implementation was always a single explicit AF_INET socket; this preserves
        # that instead of accidentally dual-stack.
        return await asyncio.start_server(self._handle_connection, host="0.0.0.0", port=port, family=socket.AF_INET)

    def close(self, timeout: "float | None" = 5.0) -> None:
        """Stops the listening server and joins its loop thread, so the process can exit normally
        afterward (a plain `sys.exit()`/return would otherwise hang forever joining that
        non-daemon thread - see its own NOTE). Idempotent - safe to call more than once (e.g. once
        from the CLI's normal shutdown path, once from a `finally`)."""
        if not self._loop_thread.is_alive():
            return
        asyncio.run_coroutine_threadsafe(self._aclose(), self._loop).result(timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=timeout)

    async def _aclose(self) -> None:
        self._server.close()
        await self._server.wait_closed()
        for task in self._connection_tasks:
            task.cancel()
        if self._connection_tasks:
            await asyncio.gather(*self._connection_tasks, return_exceptions=True)

    async def _handle_connection(self, reader: "asyncio.StreamReader", writer: "asyncio.StreamWriter") -> None:
        self.info("GDB connected")
        client_socket = writer.get_extra_info("socket")
        if client_socket is not None:
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        loop = self._loop

        # call_soon_threadsafe() unconditionally (not just a direct writer.write() when already on
        # this loop's own thread, which feed_data() below is): this same callback is also handed to
        # GDBConnection for on_breakpoint() (gdb_connection.py), invoked synchronously from
        # GDBServer.add_connection()'s _on_break - which runs on the *target's* engine-room thread
        # (Simulator.execute()'s own call chain on a real breakpoint hit), not this one.
        # call_soon_threadsafe() is safe and correct from any thread, including this loop's own.
        def on_response(data: str) -> None:
            loop.call_soon_threadsafe(writer.write, data.encode("utf-8"))

        connection = GDBConnection(self, on_response)

        task = asyncio.current_task()
        assert task is not None  # always run as a Task, by start_server()'s own contract
        self._connection_tasks.add(task)

        async def _feed(chunk: str) -> None:
            # Bridges onto the target's own engine room (Simulator.acall(), not call() - this
            # coroutine already runs on GDBTCPServer's own loop, and call() would block it,
            # stalling every other connection on this server while this one waits).
            # process_gdb_message() (invoked synchronously from inside feed_data()) reads/writes
            # core.registers/memory directly - the same class of race PR 3/PR 4 already found and
            # fixed for stdin and raw-REPL exec, just not bridged here until now. on_response()
            # (above) already does call_soon_threadsafe() unconditionally, treating being called
            # from a foreign thread as the normal case - built for _on_break in PR 2 - so nothing
            # there needs to change just because feed_data() itself now runs on a different thread.
            connection.feed_data(chunk)

        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                await self.target.acall(_feed(data.decode("utf-8")))
        except OSError as err:
            self.remove_connection(connection)
            self.error(f"GDB socket error {err}")
            return
        finally:
            self._connection_tasks.discard(task)

        self.remove_connection(connection)
        self.info("GDB disconnected")
