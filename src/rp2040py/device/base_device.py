"""Common UF2-boot lifecycle shared by device wrappers (`MicroPythonDevice`, `KalumaDevice`):
loads a UF2 image, creates the USB-CDC console, and starts/stops the emulator running. Subclasses
add their own image-specific extras (littlefs/fat12 loading, a raw-REPL `exec()` API, ...) on top.

Async-native only (docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape" - the caller owns
`asyncio.run()`, not this class): `astart()`/`start_async()`. There is no blocking `start()` -
calling `start_async()`'s returned `Future.result()` from within the same loop `astart()` would
bind to deadlocks (the loop can't process the coroutine that resolves the Future while its own
thread is blocked waiting on it), so a blocking wrapper here would be a footgun, not a
convenience. A caller that wants a blocking call can write `asyncio.run(device.astart())` itself,
same as `Simulator.execute()`'s own contract - one extra line, no silent deadlock risk baked in.
"""

import asyncio
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from os import PathLike
from pathlib import Path
from typing import TypeVar

from rp2040py.boards import BoardSpec, build_rp2040_from_spec
from rp2040py.device.load_flash import load_uf2
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = ("DEFAULT_TIMEOUT", "BaseDevice")

DEFAULT_TIMEOUT = 30.0

_T = TypeVar("_T")


async def _await(future: "Future[_T]", timeout: "float | None") -> _T:
    """asyncio.wrap_future(future), raising the builtin TimeoutError uniformly - distinct from
    concurrent.futures' own TimeoutError, which `except TimeoutError` wouldn't otherwise catch."""
    try:
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
    except (FutureTimeoutError, asyncio.TimeoutError) as exc:
        raise TimeoutError(f"did not complete within {timeout}s") from exc


class BaseDevice:
    """Boots a UF2 image and exposes its USB-CDC console (`.cdc`). Use as an async context
    manager (`async with`), or call `astart()`/`stop()` directly for more control over the
    lifecycle."""

    def __init__(
        self,
        *,
        board: BoardSpec,
        bootrom_words: "list[int] | None" = None,
        log_level: LogLevel = LogLevel.ERROR,
    ) -> None:
        assert board.image is not None, "board.image must already be resolved (see resolve_board_spec())"
        self.board = board
        self.simulator = Simulator(rp2040=build_rp2040_from_spec(board))
        self.mcu: RP2040 = self.simulator.rp2040

        if bootrom_words is None:
            from rp2040py.device.bootrom import BOOTROM_B1

            bootrom_words = BOOTROM_B1

        self.mcu.load_bootrom(bootrom_words)
        self.mcu.logger = ConsoleLogger(log_level)
        # Path(...): BoardSpec.image is typed str | Path | None (a hand-built BoardSpec may
        # reasonably set it to a plain string) - load_uf2() itself wants a real PathLike.
        load_uf2(Path(board.image), self.mcu)
        self.cdc = USBCDC(self.mcu.usb_ctrl)
        self.mcu.watchdog.on_watchdog_trigger = self._on_watchdog_trigger
        self._started = False

    def _on_watchdog_trigger(self) -> None:
        """A real `machine.reset()`/`machine.bootloader()` (mpremote's `reset`/`bootloader`
        shortcuts) writes the watchdog's TRIGGER bit to force a hardware reset - without this,
        RPWatchdog's default handler just logs a warning and the emulated CPU spins forever
        waiting for a reset that never happens. Mirrors `_aconnect()`'s own cold-boot sequence
        (reset then jump straight to flash's entry point), but preserving flash content and
        resetting mcu/cdc in place rather than replacing them - external code (this object's own
        self.cdc) holds a direct reference to mcu.usb_ctrl that must stay valid."""
        self.mcu.reset(preserve_flash=True)
        self.mcu.core.pc = FLASH_START_ADDRESS
        self.cdc.reset()

    async def _aconnect(self, timeout: "float | None") -> None:
        connected = asyncio.Event()
        self.cdc.on_device_connected = connected.set
        self.mcu.core.pc = FLASH_START_ADDRESS
        self.simulator.start_execution()
        try:
            await self.simulator.wait_for(connected, timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"device did not enumerate over USB within {timeout}s") from exc

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
        thread, matching how `cli/__init__.py`'s `_cmd_run` already drives a bare `Simulator`."""
        self.simulator.bind_loop()
        await _await(self.start_async(timeout), timeout)

    def stop(self) -> None:
        """Halt the emulator. Safe to call even if `astart()`/`start_async()` was never called -
        synchronous and non-blocking (a plain flag flip - see `Simulator.stop()`), so there's no
        async/blocking-facade distinction needed here the way there is for `astart()`. A
        stop() mid-`exec_async()`/`start_async()` call (even with `timeout=None`) no longer leaves
        that call's Future pending forever - `Simulator.wait_for()` (used by `MicroPythonDevice`'s
        own `_aconnect()`/`_aexec()`) unblocks with a clear error the moment execute() actually
        ends, for any reason, not just a crash."""
        self.simulator.stop()

    async def __aenter__(self) -> "BaseDevice":  # noqa: PYI034 (Self needs Python 3.11+)
        await self.astart()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.stop()  # synchronous and quick - no need for an async variant

    def dump_flash_image(self, filename: PathLike) -> None:
        raise NotImplementedError
