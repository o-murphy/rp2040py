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
TICKS_PER_COUNT = TICK_FREQUENCY // 1_000_000  # the countdown decrements this many times per tick (errata RP2040-E1)


def tick_frequency(enable: bool, cycles: int, clk_ref: float) -> float:
    """The tick generator's output in Hz (RP2040 datasheet 4.7.2): `clk_tick` (driven from clk_ref) divided by TICK.CYCLES, one tick every CYCLES cycles, while TICK.ENABLE is set; 0 when it is
    not running. CYCLES = 0 is taken as "not running" - the datasheet does not say what it does, but it is the reset value and the TIMER does not count out of reset ("The Watchdog tick must be
    running for the timer to start counting", 4.6.4), which matches what bare-metal code finds on silicon. Not independently sourced."""
    if not enable or cycles == 0 or clk_ref <= 0:
        return 0.0
    return clk_ref / cycles


class RPWatchdog(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.scratch_data = [0] * 8

        self._enable = False
        self._tick_enable = True
        self._tick_cycles = 0  # TICK.CYCLES: the clk_tick cycles per tick (reset 0, table 550)
        self._clk_ref = 0.0  # clk_ref in Hz, pushed by the chip (`clk_ref_changed`): the tick is derived from it
        self.tick_hz = 0.0  # the tick generator's output; 0 = not running. The TIMER, the countdown and SysTick's reference clock follow it
        self._tick_listeners: list[Callable[[float], None]] = []
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

    def add_tick_listener(self, listener: "Callable[[float], None]") -> "Callable[[], None]":
        """Calls `listener(tick_hz)` whenever the tick changes (0: stopped), after the TIMER and SysTick have been told - for anything else that counts on the tick, or watches it (the trace
        recorder of the tests). Returns the function that unsubscribes it."""
        self._tick_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._tick_listeners:
                self._tick_listeners.remove(listener)

        return unsubscribe

    def clk_ref_changed(self, clk_ref: float) -> None:
        """clk_ref is now `clk_ref` Hz (called by `update_clocks`): the tick follows it."""
        self._clk_ref = clk_ref
        self._retick()

    def _retick(self) -> None:
        """Recompute the tick from TICK and clk_ref; on a change the countdown, the TIMER and SysTick follow it."""
        hz = tick_frequency(self._tick_enable, self._tick_cycles, self._clk_ref)
        if hz == self.tick_hz:
            return
        self.tick_hz = hz
        if hz:
            self.timer.frequency = hz * TICKS_PER_COUNT  # decremented twice per tick (errata RP2040-E1)
        self.timer.enable = self._enable and hz > 0
        self.alarm.enable = self._enable and hz > 0
        for name in ("timer", "ppb"):  # the tick is the TIMER's reference and SysTick's "external reference clock"
            consumer = getattr(self.rp2040, name, None)
            if consumer is not None:
                consumer.tick_changed(hz)
        for listener in list(self._tick_listeners):
            listener(hz)

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
        """Back to power-on: the reset-cause bookkeeping this block carries across a reboot (REASON and the eight scratch registers), and the rest of it - CTRL (disabled, the three PAUSE bits
        set, the countdown at 0) and TICK (ENABLE set, CYCLES 0): the watchdog "is reset by rst_n_run" (datasheet 4.7.1), so after a RUN-pin or power-on reset the tick generator is not running and
        the TIMER does not count until the firmware has started it again.

        Deliberately *not* called on the watchdog's own reset path. On real silicon the watchdog
        block is not reset by a watchdog reboot - which is exactly why REASON still reads back the
        bit that caused it, and why `watchdog_enable()`'s `0x6ab73121` magic in SCRATCH[4] survives
        long enough for `watchdog_enable_caused_reboot()` to tell a timeout from a deliberate
        `watchdog_reboot()`. A RUN-pin/power-on reset does reset the block, and CircuitPython
        relies on the difference (`Processor.c`: "watchdog doesn't clear chip_reset, while
        chip_reset clears the watchdog"). See docs/records/0089-one-reset-for-every-trigger.md
        §1.3 for the full per-trigger table.

        """
        self._reason = 0
        self.scratch_data = [0] * 8
        self._enable = False
        self._pause_dbg0 = self._pause_dbg1 = self._pause_jtag = True
        self.timer.enable = False
        self.alarm.enable = False
        self.timer.set(0)
        self._tick_enable = True
        self._tick_cycles = 0
        self._retick()

    def read_uint32(self, offset: int) -> int:
        if offset == CTRL:
            return (
                (ENABLE if self._enable else 0)
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
            # COUNT (19:11, the cycles left before the next tick) reads 0: no model behind it, not independently sourced.
            return self._tick_cycles | (RUNNING if self.tick_hz else 0) | (TICK_ENABLE if self._tick_enable else 0)

        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CTRL:
            if value & TRIGGER:
                self._reason = FORCE
                self.on_watchdog_trigger()
            self._enable = bool(value & ENABLE)
            self.timer.enable = self._enable and self.tick_hz > 0
            self.alarm.enable = self._enable and self.tick_hz > 0
            self._pause_dbg0 = bool(value & PAUSE_DBG0)
            self._pause_dbg1 = bool(value & PAUSE_DBG1)
            self._pause_jtag = bool(value & PAUSE_JTAG)

        elif offset == LOAD:
            self.timer.set((value >> LOAD_SHIFT) & LOAD_MASK)

        elif offset == REASON:
            return  # read-only (table 548): the write has no effect

        elif offset in SCRATCH_REGS:
            self.scratch_data[(offset - SCRATCH0) >> 2] = value & 0xFFFFFFFF

        elif offset == TICK:
            self._tick_enable = bool(value & TICK_ENABLE)
            self._tick_cycles = (value >> CYCLES_SHIFT) & CYCLES_MASK
            self._retick()

        else:
            super().write_uint32(offset, value)
