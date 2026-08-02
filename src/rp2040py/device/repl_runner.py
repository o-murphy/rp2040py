"""Shared FIFO-backpressure/threading plumbing for driving a firmware's USB-CDC console, factored
out from what used to be three near-identical implementations (raw-REPL upload, the CLI's
interactive MicroPython/CircuitPython mode, and demo/kaluma_run.py's generic interactive mode).

`BaseReplRunner` owns the `USBCDC` reference and both backpressure strategies real callers need:

- `_send_byte_blocking()`: spin-waits for FIFO room, then sends. Safe from a dedicated real thread
  that has nothing else to do while blocked (e.g. a stdin reader thread) - NOT safe to call from
  `feed()`/`_feed_safe()` itself, since that runs on whatever thread is driving the simulator and
  must not block it.
- `_queue()`/`pump()`: enqueues data and drains as much as currently fits, for callers that can't
  block (`feed()`, or anything driven by the simulated clock). Scheduling *when* to call `pump()`
  again is left to the caller - it must never be a real `threading.Timer` against a live
  `Simulator`, only something that runs on the simulator's own thread (e.g. a clock alarm); see
  `device/mp_device.py`'s `_exec_blocking` for why.

`InteractiveRepl` is a generic bidirectional bridge (device output -> callback, input bytes ->
device) with no tty/stdio knowledge of its own - usable against any CDC-console firmware.
"""

import threading
import time
from collections.abc import Callable

from rp2040py.usb.cdc import USBCDC

__all__ = ("BaseReplRunner", "InteractiveRepl")

_FIFO_TIMEOUT = 0.001


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

    def start(self) -> None:
        self._cdc.on_serial_data = self._feed_safe
        self._on_start()

    def stop(self) -> None:
        if self._cdc.on_serial_data == self._feed_safe:
            self._cdc.on_serial_data = None
        self._on_stop()

    def _on_start(self) -> None:
        """Hook for subclasses: send whatever bytes/setup kick off this REPL mode."""

    def _on_stop(self) -> None:
        """Hook for subclasses: teardown."""

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

    def _send_byte_blocking(self, byte: int) -> None:
        while self._cdc.tx_fifo.item_count >= self._cdc.tx_fifo.size:
            time.sleep(_FIFO_TIMEOUT)
        self._cdc.send_serial_byte(byte)

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
    push input bytes to the device via `send()` (blocking backpressure - callers must do so from a
    dedicated thread that has nothing else to keep running while blocked).
    """

    def __init__(self, cdc: USBCDC, on_data: "Callable[[bytes | bytearray], None]") -> None:
        super().__init__(cdc)
        self._on_data = on_data

    def feed(self, data: "bytes | bytearray") -> None:
        self._on_data(data)

    def send(self, data: bytes) -> None:
        for byte in data:
            self._send_byte_blocking(byte)
