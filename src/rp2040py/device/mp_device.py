"""A programmatic API for driving a MicroPython/CircuitPython firmware image in the emulator -
the library equivalent of the ``rp2040py micropython`` CLI subcommand, for embedding in other
Python programs rather than a terminal (e.g. a test runner, or a Thonny-style tool that wants to
evaluate code against a device and read back the result).

``start()``/``exec()``/``exec_file()`` block the calling thread. Each has an ``_async`` twin
(``start_async()``/``exec_async()``/``exec_file_async()``) returning a `concurrent.futures.Future`
instead - the blocking methods are one-line wrappers around them (``future.result()``). A Future
already supports callback style for free (``future.add_done_callback(...)``), and
``astart()``/``aexec()``/``aexec_file()`` are thin ``async def`` wrappers
(``await asyncio.wrap_future(future)``) for asyncio callers. All of these ultimately share one
implementation - there's exactly one place that knows when a boot/exec has actually finished.

The device only has one REPL channel, so it can't run two ``exec()``s at once: calling
``exec_async()``/``aexec()`` again before a previous one finishes doesn't error, it queues - each
call always gets its own Future, resolved once its turn comes.

.. code-block:: python

    from rp2040py.device import MicroPythonDevice

    with MicroPythonDevice("RPI_PICO-20231005-v1.21.0.uf2") as device:
        stdout, stderr = device.exec("print(1 + 1)")
        assert stdout == b"2\\r\\n"
"""

import asyncio
import threading
from collections import deque
from concurrent.futures import Future, InvalidStateError

from rp2040py.device.bootrom import BOOTROM_B1
from rp2040py.device.load_flash import load_circuitpython_flash_image, load_micropython_flash_image, load_uf2
from rp2040py.device.raw_repl import RawReplError, RawReplRunner
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = (
    "DEFAULT_TIMEOUT",
    "MicroPythonDevice",
)

DEFAULT_TIMEOUT = 30.0


def _resolve_once(future: "Future", *, result: object = None, exception: "BaseException | None" = None) -> None:
    # Tolerates the future already being resolved - e.g. a timeout watchdog racing the real
    # completion callback. Whichever gets there first wins; the other is a harmless no-op, rather
    # than the InvalidStateError Future.set_result()/set_exception() would otherwise raise.
    if future.done():
        return
    try:
        if exception is not None:
            future.set_exception(exception)
        else:
            future.set_result(result)
    except InvalidStateError:
        pass  # the same done()-raced-us case, just not caught by the check above


def _arm_timeout(future: "Future", timeout: "float | None", message: str) -> "threading.Timer | None":
    """Guarantees `future` resolves within `timeout` seconds even if nothing else ever does -
    with the builtin TimeoutError, not concurrent.futures.TimeoutError (a distinct class on
    Python <3.11), so `except TimeoutError` works the same for the blocking and async callers."""
    if timeout is None:
        return None
    timer = threading.Timer(timeout, _resolve_once, kwargs={"future": future, "exception": TimeoutError(message)})
    timer.daemon = True
    timer.start()
    return timer


def _disarm(timer: "threading.Timer | None") -> None:
    if timer is not None:
        timer.cancel()


def _exec_via_raw_repl(cdc: USBCDC, source: bytes, timeout: "float | None") -> "Future[tuple[bytes, bytes]]":
    future: Future[tuple[bytes, bytes]] = Future()
    runner = RawReplRunner(source, cdc.send_serial_byte)
    timer = _arm_timeout(future, timeout, f"raw-REPL exec did not complete within {timeout}s")

    def _on_serial_data(data: bytes | bytearray) -> None:
        try:
            runner.feed(data)
        except RawReplError as exc:
            _disarm(timer)
            _resolve_once(future, exception=exc)
            return
        if runner.result is not None:
            _disarm(timer)
            _resolve_once(future, result=runner.result)

    # Runs on the Simulator's worker thread, not the caller's - the Future is what turns this into
    # a blocking call (.result()), callback style (.add_done_callback()), or an awaitable
    # (asyncio.wrap_future()), without this function needing to know or care which.
    cdc.on_serial_data = _on_serial_data
    runner.start()
    return future


class MicroPythonDevice:
    """Boots a MicroPython/CircuitPython UF2 image on a daemon thread and lets you run code on
    it programmatically via the raw-REPL protocol (the same one `mpremote run`/`pyboard.py` and
    Thonny's "Run" use over a real serial port).

    Use as a context manager (or `async with`), or call `start()`/`stop()` directly for more
    control over the lifecycle. See the module docstring for the blocking/callback/async story.
    """

    def __init__(
        self,
        image: str,
        *,
        littlefs: "str | None" = None,
        fat12: "str | None" = None,
        circuitpython: bool = False,
    ) -> None:
        self.simulator = Simulator()
        self.mcu: RP2040 = self.simulator.rp2040
        self.mcu.load_bootrom(BOOTROM_B1)
        self.mcu.logger = ConsoleLogger(LogLevel.ERROR)
        load_uf2(image, self.mcu)
        if littlefs is not None:
            load_micropython_flash_image(littlefs, self.mcu)
        if fat12 is not None:
            load_circuitpython_flash_image(fat12, self.mcu)
        self.circuitpython = circuitpython
        self.cdc = USBCDC(self.mcu.usb_ctrl)
        self._thread: threading.Thread | None = None
        # The raw-REPL protocol is a single request/response channel - the device can't run two
        # exec()s at once - so overlapping exec_async()/aexec() calls queue instead of erroring.
        self._exec_lock = threading.Lock()
        self._exec_queue: deque[tuple[bytes, float | None, Future]] = deque()
        self._exec_running = False

    # -- start -----------------------------------------------------------------------------

    def start_async(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[None]":
        """Boot the device on a daemon thread. Returns a Future that resolves once it enumerates
        over USB, or fails with TimeoutError after `timeout` (if given)."""
        if self._thread is not None:
            raise RuntimeError("start()/start_async() already called")

        future: Future[None] = Future()
        timer = _arm_timeout(future, timeout, f"device did not enumerate over USB within {timeout}s")

        def _on_connected() -> None:
            _disarm(timer)
            _resolve_once(future, result=None)

        self.cdc.on_device_connected = _on_connected
        self.mcu.core.pc = FLASH_START_ADDRESS
        self._thread = threading.Thread(target=self.simulator.execute, daemon=True)
        self._thread.start()
        return future

    def start(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> None:
        """Blocking version of `start_async()`."""
        self.start_async(timeout).result()

    async def astart(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> None:
        """asyncio version of `start_async()`."""
        await asyncio.wrap_future(self.start_async(timeout))

    def stop(self) -> None:
        """Halt the emulator. Safe to call even if `start()` was never called.

        Note: this does not resolve any Futures from an `exec_async()`/`start_async()` call still
        in flight - if you called those with `timeout=None`, stopping mid-exec leaves that Future
        pending forever, since the device will never send the response it was waiting for.
        """
        self.simulator.stop()

    # -- exec ------------------------------------------------------------------------------

    def exec_async(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[tuple[bytes, bytes]]":
        """Run `code` on the device via the raw-REPL protocol. Returns a Future resolving to its
        `(stdout, stderr)`, or failing with TimeoutError after `timeout` (if given) or
        RawReplError on a protocol error.

        Interrupts anything already running on the device first (e.g. an auto-run `main.py` from
        a littlefs image), same as `mpremote run`/`pyboard.py` do.

        Never blocks and never raises for "another exec is running": if one is, this call queues
        behind it and runs once its turn comes (the device only has one REPL channel, it can't
        run two exec()s at once). Callers that don't want to wait in line should wait on the
        previous Future themselves before issuing the next call.
        """
        if self._thread is None:
            raise RuntimeError("call start()/start_async() (or enter as a context manager) before exec_async()")

        future: Future[tuple[bytes, bytes]] = Future()
        with self._exec_lock:
            if self._exec_running:
                self._exec_queue.append((code.encode(), timeout, future))
            else:
                self._exec_running = True
                self._start_exec(code.encode(), timeout, future)
        return future

    def _start_exec(self, source: bytes, timeout: "float | None", future: "Future[tuple[bytes, bytes]]") -> None:
        inner = _exec_via_raw_repl(self.cdc, source, timeout)

        def _relay(done_inner: "Future[tuple[bytes, bytes]]") -> None:
            exc = done_inner.exception()
            if exc is not None:
                _resolve_once(future, exception=exc)
            else:
                _resolve_once(future, result=done_inner.result())
            self._advance_exec_queue()

        inner.add_done_callback(_relay)

    def _advance_exec_queue(self) -> None:
        with self._exec_lock:
            if self._exec_queue:
                source, timeout, next_future = self._exec_queue.popleft()
                self._start_exec(source, timeout, next_future)
            else:
                self._exec_running = False

    def exec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """Blocking version of `exec_async()`."""
        return self.exec_async(code, timeout).result()

    async def aexec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """asyncio version of `exec_async()`."""
        return await asyncio.wrap_future(self.exec_async(code, timeout))

    def exec_file_async(self, path: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[tuple[bytes, bytes]]":
        with open(path) as f:
            return self.exec_async(f.read(), timeout=timeout)

    def exec_file(self, path: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """Blocking version of `exec_file_async()`."""
        return self.exec_file_async(path, timeout=timeout).result()

    async def aexec_file(self, path: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """asyncio version of `exec_file_async()`."""
        return await asyncio.wrap_future(self.exec_file_async(path, timeout=timeout))

    # -- context managers --------------------------------------------------------------------

    def __enter__(self) -> "MicroPythonDevice":  # noqa: PYI034 (Self needs Python 3.11+)
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    async def __aenter__(self) -> "MicroPythonDevice":  # noqa: PYI034 (Self needs Python 3.11+)
        await self.astart()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.stop()  # synchronous and quick - no need for an async variant
