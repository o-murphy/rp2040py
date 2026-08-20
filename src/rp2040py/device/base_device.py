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
from enum import Enum, auto
from os import PathLike
from pathlib import Path
from typing import TypeVar

from rp2040py.boards import BoardSpec, build_rp2040_from_spec
from rp2040py.device.load_flash import load_uf2
from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.peripherals.vreg_and_chip_reset import HAD_POR, HAD_RUN
from rp2040py.rp2040 import RP2040
from rp2040py.simulator import Simulator
from rp2040py.usb.cdc import USBCDC
from rp2040py.utils.logging import ConsoleLogger, LogLevel

__all__ = ("DEFAULT_TIMEOUT", "BaseDevice", "ResetCause")

DEFAULT_TIMEOUT = 30.0

_T = TypeVar("_T")


class ResetCause(Enum):
    """Why a chip-level reset happened - what `hard_reset()` records so the firmware coming back up
    can report it (`machine.reset_cause()` on MicroPython, `microcontroller.cpu.reset_reason` on
    CircuitPython).

    The three are genuinely different signatures in hardware, not labels on one event
    (docs/records/0089-one-reset-for-every-trigger.md §1.3):

    - `WATCHDOG` - the guest reset itself, via the watchdog's TRIGGER bit (`machine.reset()`,
      `microcontroller.reset()`, `mpremote reset`) or a watchdog timeout. The watchdog block keeps
      its own REASON/SCRATCH bookkeeping across it, and CHIP_RESET is left alone; both firmwares
      read that as WDT/SOFTWARE/WATCHDOG.
    - `RUN_PIN` - the RESET button pulling RUN low, and the default for a host-side reset (0089's
      D4: what "reset the board" corresponds to physically). Clears the watchdog's REASON/scratch
      and sets CHIP_RESET.HAD_RUN; firmware reads PWRON/RESET_PIN.
    - `POWER_ON` - a power cycle/brown-out. Same clearing, but CHIP_RESET.HAD_POR - which is also
      the state a freshly constructed device already starts in, so nothing needs to ask for it
      today.
    """

    POWER_ON = auto()
    RUN_PIN = auto()
    WATCHDOG = auto()


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
        waiting for a reset that never happens. One of `hard_reset()`'s callers, not a second
        implementation of it (docs/records/0089-one-reset-for-every-trigger.md).

        `cause=WATCHDOG` covers both ways to get here - a deliberate `watchdog_reboot()` and a real
        timeout - because RPWatchdog has already written the REASON bit that tells them apart
        (FORCE vs TIMER) by the time it calls this."""
        self.hard_reset(cause=ResetCause.WATCHDOG)

    def hard_reset(self, *, cause: ResetCause = ResetCause.RUN_PIN) -> None:
        """Chip-level reset: restart the RP2040 with flash preserved and drop off the USB bus,
        the way a real RUN-pin reset or a guest's own `machine.reset()` does.

        **The one owner of the hard-reset sequence** (0089): every trigger - the watchdog's
        TRIGGER bit above, a RESET button/RUN pin, a host-side API call - routes here rather than
        growing its own variant of these three lines. Mirrors `_aconnect()`'s own cold-boot
        sequence (reset then jump straight to flash's entry point), but preserving flash content
        and resetting mcu/cdc in place rather than replacing them - external code (this object's
        own self.cdc) holds a direct reference to mcu.usb_ctrl that must stay valid.

        Synchronous and fire-and-forget by design: it is called from inside an emulated register
        write (the watchdog path), where there is nothing to await and nobody waiting. `cdc.reset()`
        deliberately leaves `on_device_connected` wired, so it fires again on the next enumeration
        - which is what makes an awaitable host-initiated form possible later (0089 Phase 2).
        `_started` stays True across this: a reset is explicitly not a second `start()`.

        `cause` is what the firmware coming back up will report from `machine.reset_cause()` /
        `microcontroller.cpu.reset_reason` - see `ResetCause` for the three signatures and why the
        default is the RUN pin rather than "whatever the last watchdog write left behind".
        """
        self.mcu.reset(preserve_flash=True)
        # After mcu.reset(), not before: today `RP2040.reset()` touches neither the watchdog nor
        # VREG_AND_CHIP_RESET, but 0089's Phase 5 widens it to the blocks a real reset covers - at
        # which point a cause recorded first would be wiped by the very reset it describes.
        self._record_reset_cause(cause)
        self.mcu.core.pc = FLASH_START_ADDRESS
        self.cdc.reset()

    def _record_reset_cause(self, cause: ResetCause) -> None:
        """0089 §1.3's table, in three lines. A watchdog reboot is the *absence* of bookkeeping:
        the watchdog block survives its own reset with REASON (and `watchdog_enable()`'s SCRATCH[4]
        magic) intact, and CHIP_RESET is untouched, so firmware sees WDT/SOFTWARE/WATCHDOG exactly
        as it does on silicon."""
        if cause is ResetCause.WATCHDOG:
            return
        self.mcu.watchdog.reset()
        self.mcu.vreg_and_chip_reset.record_reset_cause(HAD_RUN if cause is ResetCause.RUN_PIN else HAD_POR)

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
