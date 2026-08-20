"""A programmatic API for driving a MicroPython/CircuitPython firmware image in the emulator -
the library equivalent of the ``rp2040py micropython`` CLI subcommand, for embedding in other
Python programs rather than a terminal (e.g. a test runner, or a Thonny-style tool that wants to
evaluate code against a device and read back the result).

Async-native only (docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape" - see `base_device.py`'s
own module docstring for why there is no blocking ``exec()``/``start()`` here anymore).
``start_async()``/``exec_async()``/``exec_file_async()`` return a `concurrent.futures.Future` -
useful for callback style for free (``future.add_done_callback(...)``) or a caller that wants a
Future without awaiting one itself; ``astart()``/``aexec()``/``aexec_file()`` are thin
``async def`` wrappers around those, for asyncio callers.

All of these run as coroutines on the ``Simulator``'s own engine-room loop (`simulator.submit()`)
- the *caller's own* currently-running loop, once `astart()` has bound it there (see its own
docstring), not a separate dedicated thread - serialized by one `asyncio.Lock`: the device only
has one REPL channel, so it can't run two ``exec()``s at once anyway - the lock gets queueing
(extra calls simply wait their turn behind whichever coroutine holds it), while
`simulator.submit()` gets Future/callback/async support for free the same way
`concurrent.futures.Executor.submit()` would. Running on the engine room (instead of a separate
worker thread) also means the raw-REPL protocol's own byte sends (`RawReplRunner.start()`'s
Ctrl-C/Ctrl-A, `pump()`'s FIFO-paced uploads) happen on the same thread that drives
`execute_instruction()`'s own USBCDC FIFO access - by construction, not by convention.

.. code-block:: python

    import asyncio
    from rp2040py.boards import resolve_board_spec
    from rp2040py.device import MicroPythonDevice
    from rp2040py.utils.firmware_retrieve import MICROPYTHON

    async def main():
        board = resolve_board_spec("pico", MICROPYTHON, "1.21.0")
        async with MicroPythonDevice(board=board) as device:
            stdout, stderr = await device.aexec("print(1 + 1)")
            assert stdout == b"2\\r\\n"

    asyncio.run(main())
"""

import asyncio
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from os import PathLike
from typing import TypeVar

from rp2040py.boards import BoardSpec
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


async def _await(future: "Future[_T]", timeout: "float | None") -> _T:
    """asyncio.wrap_future(future), raising the builtin TimeoutError uniformly - distinct from
    concurrent.futures' own TimeoutError, which `except TimeoutError` wouldn't otherwise catch."""
    try:
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
    except (FutureTimeoutError, asyncio.TimeoutError) as exc:
        raise TimeoutError(f"did not complete within {timeout}s") from exc


class MicroPythonDevice(BaseDevice):
    """Boots a MicroPython/CircuitPython UF2 image and lets you run code on it programmatically
    via the raw-REPL protocol (the same one `mpremote run`/`pyboard.py` and Thonny's "Run" use
    over a real serial port).

    Use as an async context manager (`async with`), or call `astart()`/`stop()` directly for more
    control over the lifecycle. See the module docstring for the Future/callback/async story.
    """

    def __init__(
        self,
        *,
        board: BoardSpec,
        littlefs: "PathLike | None" = None,
        fat12: "PathLike | None" = None,
        circuitpython: bool = False,
        bootrom_words: "list[int] | None" = None,
        log_level: LogLevel = LogLevel.ERROR,
    ) -> None:
        super().__init__(board=board, bootrom_words=bootrom_words, log_level=log_level)
        if littlefs is not None:
            assert board.layout is not None, "board.layout must be set to load a littlefs image"
            load_micropython_flash_image(littlefs, self.mcu, board.layout)
        if fat12 is not None:
            assert board.layout is not None, "board.layout must be set to load a fat12 image"
            load_circuitpython_flash_image(fat12, self.mcu, board.layout)
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
    # Overridden (rather than inheriting BaseDevice.astart()/start_async()/stop() as-is) to
    # route through self._repl_lock - the same lock exec_async() uses, so
    # start_async()/exec_async() calls made back-to-back queue behind each other instead of
    # racing.

    def _post_boot_handshake(self) -> None:
        """Nudge the just-enumerated firmware into a usable prompt, per family.

        MicroPython prints its prompt only in response to a newline, so a host that attaches after
        the banner has already gone by sees nothing until it sends one. CircuitPython instead
        auto-runs `code.py` on boot and prints its prompt only once that finishes (or is
        interrupted), so the equivalent nudge there is Ctrl-C.

        Lives here, on the device and keyed on the firmware family, rather than in
        `cli/__init__.py` after `astart()` (where it used to be): a device-level reset has to
        re-run it and cannot reach into the CLI for it - 0089's Phase 0.1, and [0087]'s item 4.
        """
        if self.circuitpython:
            self.cdc.send_serial_byte(3)  # Ctrl-C
        else:
            self.cdc.send_serial_byte(ord("\r"))
            self.cdc.send_serial_byte(ord("\n"))

    async def _aconnect(self, timeout: "float | None") -> None:
        async with self._repl_lock:
            connected = asyncio.Event()
            self.cdc.on_device_connected = connected.set
            self.mcu.core.pc = FLASH_START_ADDRESS
            self.simulator.start_execution()
            try:
                await self.simulator.wait_for(connected, timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"device did not enumerate over USB within {timeout}s") from exc
            self._post_boot_handshake()

    def start_async(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> "Future[None]":
        """Boot the device. Returns a Future that resolves once it enumerates over USB, or fails
        with TimeoutError after `timeout` (if given). Runs on whatever loop `bind_loop()` last
        registered on `self.simulator` (see `astart()`), or its own dedicated background thread
        if none was ever registered - see `Simulator.bind_loop()`/`_ensure_loop()`."""
        if self._started:
            raise RuntimeError("astart()/start_async() already called")
        self._started = True
        return self.simulator.submit(self._aconnect(timeout))

    async def astart(self, timeout: "float | None" = DEFAULT_TIMEOUT) -> None:
        """asyncio version of `start_async()`. Binds `self.simulator` to the *caller's own*
        currently-running loop first (docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape") -
        `execute()` then runs as a task there instead of on a separate, dedicated background
        thread."""
        self.simulator.bind_loop()
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
            await runner.start()
            pump_alarm.schedule(1_000_000)

            try:
                await self.simulator.wait_for(done, timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"raw-REPL exec did not complete within {timeout}s") from exc
            await runner.stop()
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

        Never raises for "another exec is running": if one is (or `start_async()`/`astart()`
        hasn't finished connecting yet), this call queues behind it and runs once its turn comes -
        the device only has one REPL channel, it can't run two exec()s at once. A queued call's
        own `timeout` starts counting from when it actually begins, not from when it was queued;
        use `future.result(timeout)`/`await asyncio.wait_for(...)` if you want to bound the wait
        including queue time too.
        """
        if not self._started:
            raise RuntimeError("call astart()/start_async() (or enter as an async context manager) before exec_async()")
        return self.simulator.submit(self._aexec(code.encode(), timeout))

    async def aexec(self, code: str, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """asyncio version of `exec_async()` (see its own docstring re: `timeout` covering queue
        time too)."""
        return await _await(self.exec_async(code, timeout), timeout)

    def exec_file_async(
        self, path: PathLike, timeout: "float | None" = DEFAULT_TIMEOUT
    ) -> "Future[tuple[bytes, bytes]]":
        with open(path) as f:
            return self.exec_async(f.read(), timeout=timeout)

    async def aexec_file(self, path: PathLike, timeout: "float | None" = DEFAULT_TIMEOUT) -> "tuple[bytes, bytes]":
        """asyncio version of `exec_file_async()`."""
        return await _await(self.exec_file_async(path, timeout=timeout), timeout)

    # -- context managers --------------------------------------------------------------------
    # async __aenter__/__aexit__ only - see base_device.py's module docstring for why there is no
    # blocking sync context-manager form anymore.

    async def __aenter__(self) -> "MicroPythonDevice":  # noqa: PYI034 (Self needs Python 3.11+)
        await self.astart()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.stop()  # synchronous and quick - no need for an async variant

    def dump_flash_image(self, filename: PathLike) -> None:
        """Dump the device's flash filesystem image (LittleFS for MicroPython, FAT12 for CircuitPython)
        to a local file."""
        assert self.board.layout is not None, "board.layout must be set to dump a flash image"
        if self.circuitpython:
            dump_circuitpython_flash_image(filename, self.mcu, self.board.layout)
        else:
            dump_micropython_flash_image(filename, self.mcu, self.board.layout)
