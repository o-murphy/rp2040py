"""A programmatic API for driving a MicroPython/CircuitPython firmware image in the emulator -
the library equivalent of the ``rp2040py micropython`` CLI subcommand, for embedding in other
Python programs rather than a terminal (e.g. a test runner, or a Thonny-style tool that wants to
evaluate code against a device and read back the result).

``start()``/``exec()``/``exec_file()`` block the calling thread. Each has an ``_async`` twin
(``start_async()``/``exec_async()``/``exec_file_async()``) returning a `concurrent.futures.Future`
instead - the blocking methods are one-line wrappers around them. A Future already supports
callback style for free (``future.add_done_callback(...)``), and
``astart()``/``aexec()``/``aexec_file()`` are thin ``async def`` wrappers for asyncio callers.

All of these run as coroutines on the ``Simulator``'s own engine-room loop (`simulator.submit()`),
serialized by one `asyncio.Lock`: the device only has one REPL channel, so it can't run two
``exec()``s at once anyway - the lock gets queueing (extra calls simply wait their turn behind
whichever coroutine holds it), while `simulator.submit()` gets Future/callback/async support for
free the same way `concurrent.futures.Executor.submit()` would. Running on the engine room
(instead of a separate worker thread) also means the raw-REPL protocol's own byte sends
(`RawReplRunner.start()`'s Ctrl-C/Ctrl-A, `pump()`'s FIFO-paced uploads) happen on the same thread
that drives `execute_instruction()`'s own USBCDC FIFO access - by construction, not by convention.

.. code-block:: python

    from rp2040py.device import MicroPythonDevice

    with MicroPythonDevice("RPI_PICO-20231005-v1.21.0.uf2") as device:
        stdout, stderr = device.exec("print(1 + 1)")
        assert stdout == b"2\\r\\n"
"""

import asyncio
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from os import PathLike
from typing import TypeVar

from rp2040py.device.base_device import DEFAULT_TIMEOUT, BaseDevice
from rp2040py.device.load_flash import (
    dump_circuitpython_flash_image,
    dump_micropython_flash_image,
    load_circuitpython_flash_image,
    load_micropython_flash_image,
)
from rp2040py.device.raw_repl import RawReplError, RawReplRunner
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.utils.logging import LogLevel

_T = TypeVar("_T")

__all__ = ("MicroPythonDevice",)


def _result(future: "Future[_T]", timeout: "float | None") -> _T:
    """future.result(timeout), raising the builtin TimeoutError uniformly - concurrent.futures'
    own TimeoutError is a distinct, unrelated class on Python <3.10, so plain `except TimeoutError`
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


class MicroPythonDevice(BaseDevice):
    """Boots a MicroPython/CircuitPython UF2 image and lets you run code on it programmatically
    via the raw-REPL protocol (the same one `mpremote run`/`pyboard.py` and Thonny's "Run" use
    over a real serial port).

    Use as a context manager (or `async with`), or call `start()`/`stop()` directly for more
    control over the lifecycle. See the module docstring for the blocking/callback/async story.
    """

    def __init__(
        self,
        image: PathLike,
        *,
        littlefs: "PathLike | None" = None,
        fat12: "PathLike | None" = None,
        circuitpython: bool = False,
        bootrom_words: "list[int] | None" = None,
        log_level: LogLevel = LogLevel.ERROR,
    ) -> None:
        super().__init__(image, bootrom_words=bootrom_words, log_level=log_level)
        if littlefs is not None:
            load_micropython_flash_image(littlefs, self.mcu)
        if fat12 is not None:
            load_circuitpython_flash_image(fat12, self.mcu)
        self.circuitpython = circuitpython
        # Serializes start_async()/exec_async() coroutines running on the engine room (see
        # simulator.submit() below) the same way ThreadPoolExecutor(max_workers=1) used to -
        # extra calls simply wait their turn behind whichever one holds the lock. Safe to
        # construct here even though __init__ doesn't run on the engine-room thread: modern
        # asyncio.Lock (3.10+, this project's floor) binds to a loop lazily on first acquire(),
        # not at construction - every acquire() below always happens on the same loop (the engine
        # room, via simulator.submit()).
        self._repl_lock = asyncio.Lock()

    # -- start -----------------------------------------------------------------------------
    # Overridden (rather than inheriting BaseDevice.start()/stop() as-is) to route through
    # self._repl_lock - the same lock exec_async() uses, so start_async()/exec_async() calls
    # made back-to-back queue behind each other instead of racing.

    async def _aconnect(self, timeout: "float | None") -> None:
        async with self._repl_lock:
            connected = asyncio.Event()
            self.cdc.on_device_connected = connected.set
            self.mcu.core.pc = FLASH_START_ADDRESS
            self.simulator.start_execution()
            try:
                await asyncio.wait_for(connected.wait(), timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"device did not enumerate over USB within {timeout}s") from exc

    def start_async(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[None]":
        """Boot the device. Returns a Future that resolves once it enumerates over USB, or fails
        with TimeoutError after `timeout` (if given)."""
        if self._started:
            raise RuntimeError("start()/start_async() already called")
        self._started = True
        return self.simulator.submit(self._aconnect(timeout))

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

    async def _aexec(self, source: bytes, timeout: "float | None") -> "tuple[bytes, bytes]":
        async with self._repl_lock:
            done = asyncio.Event()
            errors: list[RawReplError] = []

            def _on_result(_result: "tuple[bytes, bytes]") -> None:
                done.set()

            def _on_error(exc: Exception) -> None:
                assert isinstance(exc, RawReplError)
                errors.append(exc)
                done.set()

            runner = RawReplRunner(self.cdc, source, on_result=_on_result, on_error=_on_error)

            def _pump_until_sent() -> None:
                # feed() only pushes as much of the source as fits in cdc's FIFO right away (see
                # RawReplRunner.pump()'s docstring for why) - this drip-feeds the rest, giving the
                # simulator loop room to actually drain the FIFO between attempts (pump() itself
                # can't do that: like feed(), it'd be running synchronously inside the emulated
                # CPU's own call chain). Scheduled via the simulated clock, not a real-time
                # asyncio.sleep(): pacing must stay tied to simulated time, not wall-clock time, or
                # it drifts against however fast/slow the emulator itself is actually running. The
                # alarm callback runs synchronously inside Clock.tick(), on this Simulator's own
                # engine-room thread - the same thread this coroutine itself runs on (both this
                # method and every USBCDC/RawReplRunner touch it makes), so there's no cross-thread
                # race here at all, unlike the old ThreadPoolExecutor-worker-thread design this
                # replaces. Keeps rescheduling until `done` regardless of pump()'s own return
                # value - there's nothing queued to send yet the first few times this fires (feed()
                # only populates it once the raw-REPL banner arrives), so an empty-queue "nothing to
                # send" from pump() isn't a reliable "fully sent, stop" signal on its own; `done`
                # (set once the exec has actually finished or errored) is.
                if done.is_set():
                    return
                runner.pump()
                pump_alarm.schedule(1_000_000)  # 1ms of emulated time, in nanoseconds

            pump_alarm = self.simulator.clock.create_alarm(_pump_until_sent)

            # wires cdc.on_serial_data = runner.feed, then sends CTRL_C, CTRL_C, CTRL_A - now
            # genuinely running on the engine room, closing the same USBCDC.tx_fifo race PR 3
            # closed for stdin (see docs/ASYNCIO_MIGRATION_BACKLOG.md's "Resolved during PR 3").
            runner.start()
            pump_alarm.schedule(1_000_000)

            try:
                await asyncio.wait_for(done.wait(), timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"raw-REPL exec did not complete within {timeout}s") from exc
            runner.stop()
            if errors:
                raise errors[0]
            assert runner.result is not None
            return runner.result

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
        return self.simulator.submit(self._aexec(code.encode(), timeout))

    def exec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """Blocking version of `exec_async()` - `timeout` bounds the *entire* wait, including any
        time spent queued behind another exec()."""
        return _result(self.exec_async(code, timeout), timeout)

    async def aexec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """asyncio version of `exec_async()` (see `exec()` re: `timeout` covering queue time too)."""
        return await _await(self.exec_async(code, timeout), timeout)

    def exec_file_async(
        self, path: PathLike, timeout: "float | None" = DEFAULT_TIMEOUT
    ) -> "Future[tuple[bytes, bytes]]":
        with open(path) as f:
            return self.exec_async(f.read(), timeout=timeout)

    def exec_file(self, path: PathLike, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """Blocking version of `exec_file_async()`."""
        return _result(self.exec_file_async(path, timeout=timeout), timeout)

    async def aexec_file(self, path: PathLike, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
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

    def dump_flash_image(self, filename: PathLike) -> None:
        """Dump the device's flash filesystem image (LittleFS for MicroPython, FAT12 for CircuitPython)
        to a local file."""
        if self.circuitpython:
            dump_circuitpython_flash_image(filename, self.mcu)
        else:
            dump_micropython_flash_image(filename, self.mcu)
