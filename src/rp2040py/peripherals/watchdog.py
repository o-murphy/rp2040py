from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.timer32 import Timer32, Timer32PeriodicAlarm, TimerMode

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPWatchdog",)

CTRL = 0x00  # Control register
LOAD = 0x04  # Load the watchdog timer.
REASON = 0x08  # Logs the reason for the last reset.
SCRATCH0 = 0x0C  # Scratch register
SCRATCH1 = 0x10  # Scratch register
SCRATCH2 = 0x14  # Scratch register
SCRATCH3 = 0x18  # Scratch register
SCRATCH4 = 0x1C  # Scratch register
SCRATCH5 = 0x20  # Scratch register
SCRATCH6 = 0x24  # Scratch register
SCRATCH7 = 0x28  # Scratch register
TICK = 0x2C  # Controls the tick generator

SCRATCH_REGS = (SCRATCH0, SCRATCH1, SCRATCH2, SCRATCH3, SCRATCH4, SCRATCH5, SCRATCH6, SCRATCH7)

# CTRL bits:
TRIGGER = 1 << 31
ENABLE = 1 << 30
PAUSE_DBG1 = 1 << 26
PAUSE_DBG0 = 1 << 25
PAUSE_JTAG = 1 << 24
TIME_MASK = 0xFFFFFF
TIME_SHIFT = 0

# LOAD bits
LOAD_MASK = 0xFFFFFF
LOAD_SHIFT = 0

# REASON bits:
FORCE = 1 << 1
TIMER = 1 << 0

# TICK bits:
COUNT_MASK = 0x1FF
COUNT_SHIFT = 11
RUNNING = 1 << 10
TICK_ENABLE = 1 << 9
CYCLES_MASK = 0x1FF
CYCLES_SHIFT = 0

# Actually 1 MHz, but due to errata RP2040-E1, the timer is decremented twice per tick
TICK_FREQUENCY = 2_000_000


class RPWatchdog(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.scratch_data = [0] * 8

        self._enable = False
        self._tick_enable = True
        self._reason = 0
        self._pause_dbg0 = True
        self._pause_dbg1 = True
        self._pause_jtag = True

        # Called when the watchdog triggers - override with your own soft reset implementation
        self.on_watchdog_trigger: Callable[[], None] = self._default_watchdog_trigger

        self.timer = Timer32(rp2040.clock, TICK_FREQUENCY)
        self.timer.mode = TimerMode.DECREMENT
        self.timer.enable = False

        def _on_alarm() -> None:
            self._reason = TIMER
            self.on_watchdog_trigger()

        self.alarm = Timer32PeriodicAlarm(self.timer, _on_alarm)
        self.alarm.target = 0
        self.alarm.enable = False

    def _default_watchdog_trigger(self) -> None:
        """The guest asked for a reset (TRIGGER, or a timeout) and nothing was installed over this
        hook: reset the chip.

        It used to log "no reset handler provided" and leave the guest spinning forever waiting for
        a reset that never came - correct only while the sequence lived in `BaseDevice`, which a
        bare `RP2040` has no access to. It does not any more (0057's option B), so the honest
        default is to do it: `rp2040py run` builds a bare `RP2040` + `USBCDC` and a guest calling
        `machine.reset()` there used to hang."""
        self.rp2040.enter_reset(from_watchdog=True)
        self.rp2040.leave_reset()

    def reset(self) -> None:
        """Clear the reset-cause bookkeeping this block carries across a reboot: REASON and the
        eight scratch registers.

        Deliberately *not* called on the watchdog's own reset path. On real silicon the watchdog
        block is not reset by a watchdog reboot - which is exactly why REASON still reads back the
        bit that caused it, and why `watchdog_enable()`'s `0x6ab73121` magic in SCRATCH[4] survives
        long enough for `watchdog_enable_caused_reboot()` to tell a timeout from a deliberate
        `watchdog_reboot()`. A RUN-pin/power-on reset does reset the block, and CircuitPython
        relies on the difference (`Processor.c`: "watchdog doesn't clear chip_reset, while
        chip_reset clears the watchdog"). See docs/records/0089-one-reset-for-every-trigger.md
        §1.3 for the full per-trigger table.

        Scoped to the reset-*cause* state on purpose: the timer/alarm/tick enables this block also
        owns are part of 0089's Phase 5 (the fuller `RP2040.reset()`), not of Phase 1.
        """
        self._reason = 0
        self.scratch_data = [0] * 8

    def read_uint32(self, offset: int) -> int:
        if offset == CTRL:
            return (
                (ENABLE if self.timer.enable else 0)
                | (PAUSE_DBG0 if self._pause_dbg0 else 0)
                | (PAUSE_DBG1 if self._pause_dbg1 else 0)
                | (PAUSE_JTAG if self._pause_jtag else 0)
                | ((self.timer.counter & TIME_MASK) << TIME_SHIFT)
            )

        if offset == REASON:
            return self._reason

        if offset in SCRATCH_REGS:
            return self.scratch_data[(offset - SCRATCH0) >> 2]

        if offset == TICK:
            # TODO COUNT bits
            return (RUNNING | TICK_ENABLE) if self._tick_enable else 0

        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CTRL:
            if value & TRIGGER:
                self._reason = FORCE
                self.on_watchdog_trigger()
            self._enable = bool(value & ENABLE)
            self.timer.enable = self._enable and self._tick_enable
            self.alarm.enable = self._enable and self._tick_enable
            self._pause_dbg0 = bool(value & PAUSE_DBG0)
            self._pause_dbg1 = bool(value & PAUSE_DBG1)
            self._pause_jtag = bool(value & PAUSE_JTAG)

        elif offset == LOAD:
            self.timer.set((value >> LOAD_SHIFT) & LOAD_MASK)

        elif offset in SCRATCH_REGS:
            self.scratch_data[(offset - SCRATCH0) >> 2] = value & 0xFFFFFFFF

        elif offset == TICK:
            self._tick_enable = bool(value & TICK_ENABLE)
            self.timer.enable = self._enable and self._tick_enable
            self.alarm.enable = self._enable and self._tick_enable
            # TODO - handle CYCLES (tick also affectes timer)

        else:
            super().write_uint32(offset, value)
