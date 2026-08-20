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
        # The second trigger installed downward onto an MCU-owned hook, same shape as the line
        # above (0089's D3): an `ExternalDevice` only ever gets `attach(rp2040)`, so without this
        # a RESET button could reach `mcu.reset()` but never `cdc.reset()` - a restarted chip
        # with a stale host-side console, which is worse than not modelling the button at all.
        self.mcu.on_run_pin_held = self._on_run_pin_held
        self.mcu.on_run_pin_reset = self._on_run_pin_reset
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

    def _on_run_pin_held(self) -> None:
        """RUN pulled low - a RESET button pressed and *held* (`external/reset_button.py`).

        The chip enters reset here rather than on the release: registers go to their reset values
        and the device drops off the USB bus, because a real board held in reset is gone from the
        host, not idle. `execute_batch()` separately stops executing anything while
        `mcu.run_pin_low` (0089 Phase 4), so nothing runs on the way back out either.

        Not a second implementation of `hard_reset()` - it is that method's first half."""
        self._enter_reset(ResetCause.RUN_PIN)

    def _on_run_pin_reset(self) -> None:
        """RUN released - the edge that boots the chip, and `hard_reset()`'s second half.

        `cause` was already recorded by `_on_run_pin_held()`; this is the one trigger for which
        `RUN_PIN` is literal rather than a stand-in."""
        self._leave_reset()

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
        self._enter_reset(cause)
        self._leave_reset()

    def _enter_reset(self, cause: ResetCause) -> None:
        """Put the chip *into* reset: registers to their reset values, the cause recorded, and the
        device off the USB bus. Everything except starting execution again.

        Split out because the RUN pin genuinely has two edges (0089 Phase 4's own known gap, closed
        the same day): pressing RESET does all of this and *stays* there, and the release is what
        runs `_leave_reset()`. Every other trigger - the watchdog, the host API - has no held
        phase, so `hard_reset()` above simply runs both halves back to back."""
        self.mcu.reset(preserve_flash=True, from_watchdog=cause is ResetCause.WATCHDOG)
        # After mcu.reset(), not before: 0089's Phase 5 widened it to the blocks a real reset
        # covers, so a cause recorded first would be wiped by the very reset it describes.
        self._record_reset_cause(cause)
        self.cdc.reset()

    def _leave_reset(self) -> None:
        """Release the chip: point the core at flash's entry point and let it run. The other half
        of `_enter_reset()`; see there for why the two are separable."""
        self.mcu.core.pc = FLASH_START_ADDRESS

    def _record_reset_cause(self, cause: ResetCause) -> None:
        """0089 §1.3's table, in three lines. A watchdog reboot is the *absence* of bookkeeping:
        the watchdog block survives its own reset with REASON (and `watchdog_enable()`'s SCRATCH[4]
        magic) intact, and CHIP_RESET is untouched, so firmware sees WDT/SOFTWARE/WATCHDOG exactly
        as it does on silicon."""
        if cause is ResetCause.WATCHDOG:
            return
        self.mcu.watchdog.reset()
        self.mcu.vreg_and_chip_reset.record_reset_cause(HAD_RUN if cause is ResetCause.RUN_PIN else HAD_POR)

    def _post_boot_handshake(self) -> None:
        """Nudge the firmware that has just enumerated into a usable prompt. A no-op here - not
        every firmware family needs one (Kaluma deliberately gets none, see `cli/__init__.py`) -
        and overridden by `MicroPythonDevice`, which does. Called from both paths that bring a
        console up: the cold boot below and the reset one (0089 Phase 0.1/2.1), so a family only
        ever writes it once."""

    def _arm_enumeration(self) -> "asyncio.Event":
        """Wire a fresh Event to `cdc.on_device_connected`, to be awaited by
        `_await_enumeration()`. Deliberately separate from the wait: it has to happen *before*
        whatever triggers the boot (`start_execution()` on a cold boot, `hard_reset()` on a reset),
        or the callback can fire before anything is listening for it. `cdc.reset()` leaves
        `on_device_connected` wired, which is what makes the reset case work at all."""
        connected = asyncio.Event()
        self.cdc.on_device_connected = connected.set
        return connected

    async def _await_enumeration(self, connected: "asyncio.Event", timeout: "float | None", what: str) -> None:
        """The waiting half of `_aconnect()`, shared with the reset path rather than copied into it
        (0089 Phase 2.2). `what` only names the event in the timeout message."""
        try:
            await self.simulator.wait_for(connected, timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"device did not {what} over USB within {timeout}s") from exc

    async def _aconnect(self, timeout: "float | None") -> None:
        connected = self._arm_enumeration()
        self.mcu.core.pc = FLASH_START_ADDRESS
        self.simulator.start_execution()
        await self._await_enumeration(connected, timeout, "enumerate")
        self._post_boot_handshake()

    async def _ahard_reset(self, timeout: "float | None", cause: ResetCause) -> None:
        """`hard_reset()` plus the waiting a host-side caller can do and a guest-triggered one
        cannot: the chip drops off the bus, boots again, re-enumerates, and the family's post-boot
        handshake runs a second time. The emulator is already executing, so nothing restarts it -
        `hard_reset()` only points the core back at the flash entry point."""
        connected = self._arm_enumeration()
        self.hard_reset(cause=cause)
        await self._await_enumeration(connected, timeout, "re-enumerate after the reset")
        self._post_boot_handshake()

    def hard_reset_async(
        self, timeout: "float | None" = DEFAULT_TIMEOUT, *, cause: ResetCause = ResetCause.RUN_PIN
    ) -> "Future[None]":
        """Reset the chip and wait for it to come back. Returns a Future that resolves once the
        device has re-enumerated over USB and its console is usable again, or fails with
        TimeoutError after `timeout` (if given).

        What it promises is "re-enumerated, console usable" - **not** "you will see the boot
        banner". Firmware only flushes CDC once DTR is asserted, exactly as a real board does to a
        terminal re-opening the port, so output printed during the boot that follows the reset is
        lost by construction (0089's Appendix, point 4). Assert on state (a variable is gone, a
        counter advanced), never on boot output.

        Not a second `astart()`: `_started` stays True across a reset, and `start_async()` keeps
        raising if called again. Anything the guest had in RAM is gone; flash is preserved.
        """
        if not self._started:
            raise RuntimeError("call astart()/start_async() (or enter as an async context manager) before resetting")
        return self.simulator.submit(self._ahard_reset(timeout, cause))

    async def ahard_reset(
        self, timeout: "float | None" = DEFAULT_TIMEOUT, *, cause: ResetCause = ResetCause.RUN_PIN
    ) -> None:
        """asyncio version of `hard_reset_async()`."""
        await _await(self.hard_reset_async(timeout, cause=cause), timeout)

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

    def set_control_lines(self, *, dtr: bool, rts: bool) -> None:
        """Assert or drop the CDC control lines (DTR/RTS) on the emulated USB link.

        The host half of what a terminal does when it opens or closes the port, and guest-visible:
        CircuitPython's `supervisor.runtime.serial_connected` and MicroPython's TX-flush arming
        both follow DTR (0088 section 1). Enumeration already asserts both, so this is only for
        *changing* them afterwards.

        Fire-and-forget, and handed to the engine-room loop rather than applied inline, because it
        poke*s* emulated registers from whatever thread the caller happens to be on - 0030's rule,
        the same one `ResetButton` follows. There is deliberately no awaitable form: the request's
        effect is guest state, so what proves it landed is asking the guest, not a Future here.
        """
        self.simulator.schedule_threadsafe(lambda: self.cdc.set_control_lines(dtr=dtr, rts=rts))

    def set_line_coding(self, baud_rate: int, *, stop_bits: int = 0, parity: int = 0, data_bits: int = 8) -> None:
        """Send CDC `SET_LINE_CODING` - baud rate, stop bits, parity, data bits.

        Emulated USB: the baud rate is guest-visible state, not a wire speed, and nothing here
        moves faster or slower for it. Same threading contract as `set_control_lines()`; see
        `USBCDC.set_line_coding()` for why the 1200-bps touch is not a reset trigger in this tree.
        """
        self.simulator.schedule_threadsafe(
            lambda: self.cdc.set_line_coding(baud_rate, stop_bits=stop_bits, parity=parity, data_bits=data_bits)
        )

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
