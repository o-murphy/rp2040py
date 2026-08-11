"""Shared FIFO-backpressure plumbing for driving a firmware's USB-CDC console, factored out from
what used to be three near-identical implementations (raw-REPL upload, the CLI's interactive
MicroPython/CircuitPython mode, and demo/kaluma_run.py's generic interactive mode).

`BaseReplRunner` owns the `USBCDC` reference and the one backpressure strategy every caller needs:
`_queue()`/`pump()` enqueues data and drains as much as currently fits. Scheduling *when* to call
`pump()` again is left to the caller - it must never be a real `threading.Timer` against a live
`Simulator`, only something that runs on the simulator's own thread (e.g. a clock alarm, or an
`asyncio.get_event_loop().add_reader()` callback already running there); see
`device/mp_device.py`'s `_exec_blocking` and `cli/stdio_repl.py` for why.

`InteractiveRepl` is a generic bidirectional bridge (device output -> callback, input bytes ->
device) with no tty/stdio knowledge of its own - usable against any CDC-console firmware.
"""

import threading
from collections.abc import Callable

from rp2040py.usb.cdc import USBCDC

__all__ = ("BaseReplRunner", "InteractiveRepl")


class BaseReplRunner:
    """Base class for REPL runners driving firmware over `USBCDC`. `start()` wires
    `cdc.on_serial_data` to this runner and calls `_on_start()`; `stop()` unwires it and calls
    `_on_stop()`. Subclasses implement `feed()` (and typically override `_on_start()`/`_on_stop()`
    for protocol-specific setup/teardown).
    """

    def __init__(self, cdc: USBCDC, on_error: "Callable[[Exception], None] | None" = None) -> None:
        self._cdc = cdc
        self._on_error = on_error
        self._pending = b""
        self._pending_lock = threading.Lock()

    async def start(self) -> None:
        self._cdc.on_serial_data = self._feed_safe
        await self._on_start()

    async def stop(self) -> None:
        if self._cdc.on_serial_data == self._feed_safe:
            self._cdc.on_serial_data = None
        await self._on_stop()

    async def _on_start(self) -> None:
        """Hook for subclasses: send whatever bytes/setup kick off this REPL mode. `async def`
        (docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape") because at least one subclass
        (`SocketInteractiveRepl`) needs to `await asyncio.start_server()` here - most others have
        nothing to `await` and just run synchronous setup, which is fine inside an `async def`
        with no `await` in it."""

    async def _on_stop(self) -> None:
        """Hook for subclasses: teardown. See `_on_start()` for why this is `async def`."""

    def feed(self, data: "bytes | bytearray") -> None:
        raise NotImplementedError

    def _feed_safe(self, data: "bytes | bytearray") -> None:
        # Registered directly as cdc.on_serial_data, i.e. called from inside the emulated CPU's
        # own call chain (the device writing to its USB TX register) - an uncaught exception here
        # would propagate into the simulator instead of just this runner. Catches and forwards to
        # on_error instead, so a subclass whose feed() can raise (e.g. a malformed protocol reply)
        # doesn't need every caller to wrap on_serial_data itself.
        try:
            self.feed(data)
        except Exception as exc:
            if self._on_error is not None:
                self._on_error(exc)
            else:
                raise

    def _queue(self, data: bytes) -> None:
        with self._pending_lock:
            self._pending += data

    def pump(self) -> bool:
        """Sends as much of the queued data as the FIFO currently has room for, and returns
        whether it's all been sent. Call again while this returns False - see the module
        docstring for why the *scheduling* of those repeat calls matters.
        """
        with self._pending_lock:
            if self._pending:
                free = max(self._cdc.tx_fifo.size - self._cdc.tx_fifo.item_count, 0)
                if free:
                    chunk, self._pending = self._pending[:free], self._pending[free:]
                    for byte in chunk:
                        self._cdc.send_serial_byte(byte)
            return not self._pending


class InteractiveRepl(BaseReplRunner):
    """Generic bidirectional bridge: forwards device output bytes to `on_data`, and lets callers
    push input bytes to the device via `send()`.
    """

    def __init__(self, cdc: USBCDC, on_data: "Callable[[bytes | bytearray], None]") -> None:
        super().__init__(cdc)
        self._on_data = on_data

    def feed(self, data: "bytes | bytearray") -> None:
        self._on_data(data)

    def send(self, data: bytes) -> bool:
        """Queues `data` and attempts one immediate `pump()`. Returns whether it all went out -
        call `pump()` again (e.g. from a clock alarm) while this returns `False`, same as `pump()`
        itself; see the module docstring for why the *scheduling* of those repeat calls matters."""
        self._queue(data)
        return self.pump()
