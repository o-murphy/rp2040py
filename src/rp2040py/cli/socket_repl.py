"""Bridges a device's USB-CDC console to a plain TCP socket instead of this process's own
stdio - the headless counterpart to `StdioInteractiveRepl` (`cli/stdio_repl.py`), for driving the
emulator from tools that expect a serial port but can't open a real (or virtual pty-backed) one -
notably `mpremote` in a sandboxed/containerized environment with no serial support at all (see
README.md's "mpremote" section).

No client-side patching needed on the `mpremote` side: `mpremote`'s `SerialTransport` opens its
connection via `serial.serial_for_url()`, and pySerial ships built-in support for
`socket://host:port` URLs - a raw byte pipe over TCP with no protocol of its own layered on top
(unlike `rfc2217://`, which does Telnet option negotiation). `mpremote connect
socket://127.0.0.1:<port>` talks directly to `SocketInteractiveRepl` below with nothing in between.
"""

import asyncio
import contextlib
import socket
from collections.abc import Callable
from typing import Any

from rp2040py.device.repl_runner import InteractiveRepl
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC

__all__ = ("SocketInteractiveRepl",)

# Matches stdio_repl.py's own _PUMP_PERIOD_NANOS - 1ms of simulated time between repeat pump()
# attempts while the tx_fifo is still full.
_PUMP_PERIOD_NANOS = 1_000_000

# Upper bound on how long _handle_connection() waits for a still-occupied slot's previous
# connection to actually finish tearing down before concluding it's genuinely still active - see
# its own comment. A dead connection's teardown needs no real I/O wait (just event-loop scheduling
# turns), so this is already generous for that case; kept well under typical client-side socket
# read timeouts (e.g. this project's own tests default to 2s) so a client legitimately waiting to
# be rejected sees the connection close before its own timeout fires instead of racing it.
_STALE_CONNECTION_GRACE_SECONDS = 1.0


class SocketInteractiveRepl(InteractiveRepl):
    """Headless bridge between a device's USB-CDC console and one TCP client at a time - a raw
    byte pipe in both directions, with no framing or escape sequences of its own. Unlike
    `StdioInteractiveRepl`'s Ctrl+X, no in-band byte here can be reserved to mean "quit": a real
    client (e.g. `mpremote`) owns its own protocol on top of this stream (raw-REPL's own
    Ctrl-A/Ctrl-B/Ctrl-C/Ctrl-D among them), and stealing a byte from it would corrupt that
    protocol. Quit via Ctrl+C/SIGTERM/`--expect-text` at the process level instead - see
    `cli/__init__.py`; `Simulator.wait_for_shutdown()` already handles a plain `KeyboardInterrupt`
    since nothing here puts the real terminal in raw mode the way `StdioInteractiveRepl` does.

    Listens for this instance's whole `start()`-to-`stop()` lifetime, accepting one client at a
    time; a second connection while one is already active is closed immediately rather than
    queued or multiplexed - matching a real serial port's exclusive-access semantics, and simpler
    than trying to fan one device console out to several independent clients. `on_data`, if given,
    sees every chunk of device output regardless of whether a client is currently connected (e.g.
    for `--expect-text`) - bytes are otherwise simply dropped when nothing is listening, the same
    as a real UART TX pin with nothing wired to RX.

    Runs its `asyncio.start_server()` on `simulator`'s own engine-room loop (started there via
    `simulator.call()`, mirroring `StdioInteractiveRepl`'s `add_reader()`) rather than an
    independent one - contrast `GDBTCPServer`, which doesn't need this: forwarding bytes to the
    device touches `cdc.tx_fifo` directly (via `send()`/`pump()`, see `device/repl_runner.py`),
    which is only ever safe from that one thread. Because the connection handler therefore already
    runs on the right thread, both directions are plain synchronous calls with no per-message
    cross-thread bridging.
    """

    def __init__(
        self,
        cdc: USBCDC,
        simulator: Simulator,
        host: str = "127.0.0.1",
        port: int = 0,
        on_data: "Callable[[bytes | bytearray], None] | None" = None,
    ) -> None:
        super().__init__(cdc, on_data=self._dispatch)
        self._simulator = simulator
        self._host = host
        self._requested_port = port
        self._extra_on_data = on_data
        self._server: asyncio.Server | None = None
        self._client_writer: asyncio.StreamWriter | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._pump_alarm: Any = None
        # Resolved once the server is actually listening (self._requested_port may be 0, meaning
        # "OS picks a free port") - None until then.
        self.port: int | None = None

    def _dispatch(self, data: "bytes | bytearray") -> None:
        # Always called from cdc.on_serial_data, itself only ever invoked from execute()'s own
        # call chain on the engine-room thread - the same thread self._server was started on, so
        # a plain write() is already safe here with no cross-thread bridge needed.
        if self._client_writer is not None:
            self._client_writer.write(bytes(data))
        if self._extra_on_data is not None:
            self._extra_on_data(data)

    def _on_start(self) -> None:
        self._server = self._simulator.call(self._start_server())
        assert self._server.sockets
        self.port = self._server.sockets[0].getsockname()[1]

    def _on_stop(self) -> None:
        if self._server is not None:
            self._simulator.call(self._stop_server())
            self._server = None
            self.port = None

    async def _start_server(self) -> asyncio.Server:
        # family=AF_INET, matching GDBTCPServer's own reasoning: an unset host can otherwise
        # resolve to multiple independently-bound addresses (e.g. IPv6 "::" and IPv4 "0.0.0.0"),
        # each getting a different OS-assigned port when port=0 - self.port above only reads
        # sockets[0], so a plain IPv4 client could intermittently hit the wrong socket's port.
        return await asyncio.start_server(
            self._handle_connection, host=self._host, port=self._requested_port, family=socket.AF_INET
        )

    async def _stop_server(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
        if self._connection_task is not None:
            self._connection_task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(self._connection_task, return_exceptions=True), timeout=2.0)
            except TimeoutError:
                pass
            self._connection_task = None
        if self._client_writer is not None:
            self._client_writer.close()
            self._client_writer = None

    async def _handle_connection(self, reader: "asyncio.StreamReader", writer: "asyncio.StreamWriter") -> None:
        # A connection that's already dead on arrival (its peer closed before, or while, this
        # accept()ed) still occupies self._client_writer until its own _handle_connection() task
        # actually finishes (its reader.read() resolving to EOF, then its own finally clearing
        # self._client_writer below) - not something a single synchronous check up front can see,
        # and not bounded by a fixed number of scheduling yields either (confirmed: a bounded
        # `await asyncio.sleep(0)` retry loop here was still occasionally too few iterations on a
        # slower CI runner). Waiting on the *actual* previous task instead - shielded, so timing
        # out here doesn't cancel a connection that turns out to still be genuinely running -
        # resolves the instant that task truly finishes, however many loop iterations that takes,
        # rather than guessing a count. Confirmed via a real repro: two connections opened back-to-
        # back (the second right after the first's peer already closed) could otherwise have the
        # second wrongly rejected purely because of task-scheduling order, even though the first
        # was already dead by the time the second arrived.
        if self._client_writer is not None:
            stale_task = self._connection_task
            if stale_task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(stale_task), timeout=_STALE_CONNECTION_GRACE_SECONDS)
            if self._client_writer is not None:
                # Still occupied after waiting - a genuinely live connection. One client at a
                # time - see class docstring.
                writer.close()
                return

        client_socket = writer.get_extra_info("socket")
        if client_socket is not None:
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        self._client_writer = writer
        self._connection_task = asyncio.current_task()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                self._send_and_pace(data)
        except OSError:
            pass
        finally:
            # Identity-checked, not unconditional: guards against this (by-then-superseded)
            # connection's cleanup clobbering a *different*, newer connection's state - not
            # currently reachable given the retry-then-reject logic above never lets two
            # connections hold the slot at once, but cheap insurance against that invariant ever
            # changing out from under this method.
            if self._client_writer is writer:
                self._client_writer = None
                self._connection_task = None
            writer.close()

    def _send_and_pace(self, data: bytes) -> None:
        """Mirrors `StdioInteractiveRepl`'s own helper: `send()`s `data`, scheduling a repeat pump
        if the tx_fifo didn't have room for all of it yet."""
        if not self.send(data):
            self._schedule_pump()

    def _schedule_pump(self) -> None:
        if self._pump_alarm is None:
            self._pump_alarm = self._simulator.clock.create_alarm(self._pump_tick)
        self._pump_alarm.schedule(_PUMP_PERIOD_NANOS)

    def _pump_tick(self) -> None:
        # A clock alarm callback always fires from Clock.tick()'s own call chain, i.e. already on
        # the engine-room thread - no bridging needed here either.
        if not self.pump():
            self._schedule_pump()
