"""A programmatic API for driving a MicroPython/CircuitPython firmware image in the emulator -
the library equivalent of the ``rp2040py micropython`` CLI subcommand, for embedding in other
Python programs rather than a terminal (e.g. a test runner, or a Thonny-style tool that wants to
evaluate code against a device and read back the result).

``start()``/``exec()``/``exec_file()`` block the calling thread. Each has an ``_async`` twin
(``start_async()``/``exec_async()``/``exec_file_async()``) returning a `concurrent.futures.Future`
instead - the blocking methods are one-line wrappers around them. A Future already supports
callback style for free (``future.add_done_callback(...)``), and
``astart()``/``aexec()``/``aexec_file()`` are thin ``async def`` wrappers for asyncio callers.

All of these share one `concurrent.futures.ThreadPoolExecutor` with a single worker: the device
only has one REPL channel, so it can't run two ``exec()``s at once anyway - a single-worker
executor gets queueing (extra calls simply wait their turn), Future/callback/async support, and
cancellation of not-yet-started calls all from the standard library, instead of a hand-rolled
alternative. Both boot and exec submit *plain blocking* work to it (`threading.Event.wait`) - the
executor is what turns "blocking" into "queued, cancellable, awaitable" for callers, without the
underlying implementation needing to know or care which style is being used.

.. code-block:: python

    from rp2040py.device import MicroPythonDevice

    with MicroPythonDevice("RPI_PICO-20231005-v1.21.0.uf2") as device:
        stdout, stderr = device.exec("print(1 + 1)")
        assert stdout == b"2\\r\\n"
"""

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TypeVar

from rp2040py.clock.clock import IClock
from rp2040py.device.base_device import DEFAULT_TIMEOUT, BaseDevice, connect_blocking
from rp2040py.device.load_flash import load_circuitpython_flash_image, load_micropython_flash_image
from rp2040py.device.raw_repl import RawReplError, RawReplRunner
from rp2040py.usb.cdc import USBCDC

_T = TypeVar("_T")

__all__ = (
    "DEFAULT_TIMEOUT",
    "MicroPythonDevice",
)


def _result(future: "Future[_T]", timeout: "float | None") -> _T:
    """future.result(timeout), raising the builtin TimeoutError uniformly - concurrent.futures'
    own TimeoutError is a distinct, unrelated class on Python <3.11, so plain `except TimeoutError`
    would otherwise silently miss "still queued behind other work" timeouts on those versions."""
    try:
        return future.result(timeout)
    except FutureTimeoutError as exc:
        raise TimeoutError(f"did not complete within {timeout}s") from exc


async def _await(future: "Future[_T]", timeout: "float | None") -> _T:
    """asyncio.wrap_future(future), with the same TimeoutError normalization as _result()."""
    try:
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
    except (FutureTimeoutError, asyncio.TimeoutError) as exc:
        raise TimeoutError(f"did not complete within {timeout}s") from exc


def _exec_blocking(cdc: USBCDC, clock: "IClock", source: bytes, timeout: "float | None") -> "tuple[bytes, bytes]":
    done = threading.Event()
    errors: list[RawReplError] = []

    def _on_result(_result: "tuple[bytes, bytes]") -> None:
        done.set()

    def _on_error(exc: Exception) -> None:
        assert isinstance(exc, RawReplError)
        errors.append(exc)
        done.set()

    runner = RawReplRunner(cdc, source, on_result=_on_result, on_error=_on_error)

    def _pump_until_sent() -> None:
        # feed() only pushes as much of the source as fits in cdc's FIFO right away (see
        # RawReplRunner.pump()'s docstring for why) - this drip-feeds the rest, giving the
        # simulator loop room to actually drain the FIFO between attempts (pump() itself can't do
        # that: like feed(), it'd be running synchronously inside the emulated CPU's own call
        # chain). Scheduled via the simulated clock, not threading.Timer: a real-time timer fires
        # on its own OS thread, which then races USBCDC.tx_fifo.push()/pull() against whichever
        # thread is driving the simulator (pull() happens deep in the emulated USB peripheral's own
        # read path, mid-instruction-execution) - USBCDC/FIFO were never meant to be thread-safe
        # (it's a hot path used everywhere in peripheral emulation, not worth locking globally for
        # this one caller), and that race really did corrupt uploads intermittently (confirmed:
        # different corruption each run - an IndentationError one run, a SyntaxError the next, same
        # input). An alarm callback instead runs synchronously inside Clock.tick(), on whatever
        # thread is already driving the simulator - same thread as feed()/pull(), no race. Keeps
        # rescheduling until `done` regardless of pump()'s own return value - there's nothing
        # queued to send yet the first few times this fires (feed() only populates it once the
        # raw-REPL banner arrives), so an empty-queue "nothing to send" from pump() isn't a
        # reliable "fully sent, stop" signal on its own; `done` (set once the exec has actually
        # finished or errored) is.
        if done.is_set():
            return
        runner.pump()
        pump_alarm.schedule(1_000_000)  # 1ms of emulated time, in nanoseconds

    pump_alarm = clock.create_alarm(_pump_until_sent)

    runner.start()  # wires cdc.on_serial_data = runner.feed, then sends CTRL_C, CTRL_C, CTRL_A
    pump_alarm.schedule(1_000_000)

    if not done.wait(timeout):
        raise TimeoutError(f"raw-REPL exec did not complete within {timeout}s")
    runner.stop()
    if errors:
        raise errors[0]
    assert runner.result is not None
    return runner.result


class MicroPythonDevice(BaseDevice):
    """Boots a MicroPython/CircuitPython UF2 image and lets you run code on it programmatically
    via the raw-REPL protocol (the same one `mpremote run`/`pyboard.py` and Thonny's "Run" use
    over a real serial port).

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
        super().__init__(image)
        if littlefs is not None:
            load_micropython_flash_image(littlefs, self.mcu)
        if fat12 is not None:
            load_circuitpython_flash_image(fat12, self.mcu)
        self.circuitpython = circuitpython
        self._executor = ThreadPoolExecutor(max_workers=1)

    # -- start -----------------------------------------------------------------------------
    # Overridden (rather than inheriting BaseDevice.start()/stop() as-is) to route through
    # self._executor - the same single-worker queue exec_async() uses, so start_async()/
    # exec_async() calls made back-to-back queue behind each other instead of racing.

    def start_async(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[None]":
        """Boot the device. Returns a Future that resolves once it enumerates over USB, or fails
        with TimeoutError after `timeout` (if given)."""
        if self._started:
            raise RuntimeError("start()/start_async() already called")
        self._started = True
        return self._executor.submit(connect_blocking, self.cdc, self.simulator, self.mcu, timeout)

    def start(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> None:
        """Blocking version of `start_async()`."""
        _result(self.start_async(timeout), timeout)

    async def astart(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> None:
        """asyncio version of `start_async()`."""
        await _await(self.start_async(timeout), timeout)

    # stop() inherited from BaseDevice unchanged. Note: it does not resolve any Futures from an
    # exec_async()/start_async() call still in flight - if you called those with timeout=None,
    # stopping mid-exec leaves that Future pending forever, since the device will never send the
    # response it was waiting for.

    # -- exec ------------------------------------------------------------------------------

    def exec_async(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[tuple[bytes, bytes]]":
        """Run `code` on the device via the raw-REPL protocol. Returns a Future resolving to its
        `(stdout, stderr)`, or failing with TimeoutError after `timeout` (if given) or
        RawReplError on a protocol error.

        Interrupts anything already running on the device first (e.g. an auto-run `main.py` from
        a littlefs image), same as `mpremote run`/`pyboard.py` do.

        Never raises for "another exec is running": if one is (or `start_async()` hasn't finished
        connecting yet), this call queues behind it and runs once its turn comes - the device only
        has one REPL channel, it can't run two exec()s at once. A queued call's own `timeout`
        starts counting from when it actually begins, not from when it was queued; use
        `future.result(timeout)`/`await asyncio.wait_for(...)` if you want to bound the wait
        including queue time too.
        """
        if not self._started:
            raise RuntimeError("call start()/start_async() (or enter as a context manager) before exec_async()")
        return self._executor.submit(_exec_blocking, self.cdc, self.simulator.clock, code.encode(), timeout)

    def exec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """Blocking version of `exec_async()` - `timeout` bounds the *entire* wait, including any
        time spent queued behind another exec()."""
        return _result(self.exec_async(code, timeout), timeout)

    async def aexec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """asyncio version of `exec_async()` (see `exec()` re: `timeout` covering queue time too)."""
        return await _await(self.exec_async(code, timeout), timeout)

    def exec_file_async(self, path: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[tuple[bytes, bytes]]":
        with open(path) as f:
            return self.exec_async(f.read(), timeout=timeout)

    def exec_file(self, path: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """Blocking version of `exec_file_async()`."""
        return _result(self.exec_file_async(path, timeout=timeout), timeout)

    async def aexec_file(self, path: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """asyncio version of `exec_file_async()`."""
        return await _await(self.exec_file_async(path, timeout=timeout), timeout)

    # -- context managers --------------------------------------------------------------------
    # sync __enter__/__exit__ inherited from BaseDevice - self.start()/self.stop() there resolve
    # to this class's own overrides via normal polymorphism.

    async def __aenter__(self) -> "MicroPythonDevice":  # noqa: PYI034 (Self needs Python 3.11+)
        await self.astart()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.stop()  # synchronous and quick - no need for an async variant
