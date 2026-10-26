import math
from typing import TYPE_CHECKING

from rp2040py.clock._simulation_clock import TICKS_PER_NANO
from rp2040py.clock.clock import IAlarm, IClock
from rp2040py.irq import IRQ
from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPTimer",)

TIMEHW = 0x00
TIMELW = 0x04
TIMEHR = 0x08
TIMELR = 0x0C
TIMERAWH = 0x24
TIMERAWL = 0x28
ALARM0 = 0x10
ALARM1 = 0x14
ALARM2 = 0x18
ALARM3 = 0x1C
ARMED = 0x20
DBGPAUSE = 0x2C
PAUSE = 0x30
INTR = 0x34
INTE = 0x38
INTF = 0x3C
INTS = 0x40

ALARM_0 = 1 << 0
ALARM_1 = 1 << 1
ALARM_2 = 1 << 2
ALARM_3 = 1 << 3

TIMER_INTERRUPTS = [IRQ.TIMER_0, IRQ.TIMER_1, IRQ.TIMER_2, IRQ.TIMER_3]

ALARM_REGS = (ALARM0, ALARM1, ALARM2, ALARM3)
DBGPAUSE_MASK = 0x6  # DBG1 (bit 2) and DBG0 (bit 1); bit 0 is reserved
DBGPAUSE_RESET = 0x6  # both reset to 1 (RP2040 datasheet, TIMER DBGPAUSE). No debugger is ever attached here, so they change nothing.


class RPTimerAlarm:
    def __init__(self, bit_value: int, clock_alarm: IAlarm):
        self.bit_value = bit_value
        self.clock_alarm = clock_alarm
        self.armed = False
        self.target_micros = 0


class RPTimer(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.clock: IClock = rp2040.clock
        self._latched_time_high = 0
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0
        self._paused = False
        self._tick_hz = 0.0  # the watchdog's tick (RP2040 datasheet 4.6.4, 4.7.2): the count advances once per tick; 0 = the tick is not running, the count is frozen
        self._time_latch_low = 0  # TIMELW's latch: "writes do not get copied to time until timehw is written"
        self._dbgpause = DBGPAUSE_RESET
        # Simulated-time origin for this block's own count, so `reset()` can restart it from zero
        # the way real silicon does - `clock.nanos` is the *simulation's* time base and is never
        # reset (USB SOF, every other peripheral's alarms and the engine room all share it).
        self._epoch_ticks: int = 0  # in ticks of 1/16 ns, so that a difference with the clock stays exact however far into the run (docs/records/0100)
        # The count is an INTEGER (64 bits, wrapping): `_count` whole counts at the epoch, plus `_sub` ticks of the clock into the count after it (0 <= _sub < one period). The `n`th count after the
        # last one comes `ceil(n * period)` ticks later, so on the nominal 1 MHz tick (16000 ticks a count) everything is exact integer arithmetic, and a count is reached at a tick, never a fraction
        # of one before it. Frozen (PAUSE, or no tick): these two are the count and the epoch is not used. The epoch is folded into them whenever the clock's origin moves, so nothing grows
        # without bound (docs/records/0105, finding 6: the count used to be a `double` of microseconds with a fractional base).
        self._count = 0
        self._sub = 0
        self.clock.add_rebase_listener(self._rebased)  # ... and follows the clock when its origin moves
        self.alarms = [
            RPTimerAlarm(ALARM_0, self.clock.create_alarm(lambda: self._fire_alarm(0))),
            RPTimerAlarm(ALARM_1, self.clock.create_alarm(lambda: self._fire_alarm(1))),
            RPTimerAlarm(ALARM_2, self.clock.create_alarm(lambda: self._fire_alarm(2))),
            RPTimerAlarm(ALARM_3, self.clock.create_alarm(lambda: self._fire_alarm(3))),
        ]

    def _rebased(self, delta: int) -> None:
        self._epoch_ticks -= delta  # (the epoch is always near the clock's time, so this cannot run away)
        if not self._frozen():
            self._count, self._sub = self._count_and_sub()
        self._epoch_ticks = self.clock.ticks

    def reset(self) -> None:
        """Registers and all four alarms, back to power-on (0089 Phase 5).

        `clock_alarm.cancel()` is the load-bearing line: a scheduled alarm that survived would fire
        into freshly-reset registers and raise an interrupt for a deadline the guest set before the
        reset - the emulated equivalent of a timer that kept counting through a power cut. The
        `RPTimerAlarm` objects themselves stay, since the clock holds them.

        The count restarts from zero, as a real `TIMER` does: `_epoch_ticks` moves to now rather
        than the simulation clock being rewound. Guest code therefore sees `time.ticks_ms()` /
        `time.monotonic()` start over after a reset, which is the observable that made this a
        fidelity gap rather than a detail.
        """
        self._epoch_ticks = self.clock.ticks
        self._count = 0
        self._sub = 0
        self._latched_time_high = 0
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0
        self._paused = False
        # the tick is not part of the block's registers: it keeps running (or not) through a reset
        self._time_latch_low = 0
        self._dbgpause = DBGPAUSE_RESET
        for alarm in self.alarms:
            alarm.clock_alarm.cancel()
            alarm.armed = False
            alarm.target_micros = 0
        for irq in TIMER_INTERRUPTS:
            self.rp2040.set_interrupt(irq, False)

    @property
    def _int_status(self) -> int:
        return (self._int_raw & self._int_enable) | self._int_force

    def snapshot(self) -> dict[str, int | float]:
        """What the TIMER holds that its registers do not show: the tick it counts on and the interrupt lines it drives (everything else is read over the bus)."""
        return {"tick_hz": self._tick_hz, "int_status": self._int_status}

    def _frozen(self) -> bool:
        """The count does not advance: PAUSE is set, or the watchdog's tick is not running ("The Watchdog tick must be running for the timer to start counting", datasheet 4.6.4)."""
        return self._paused or self._tick_hz == 0.0

    def _period(self) -> float:
        """Ticks of the clock (1/16 ns) per count: 16000 at the nominal 1 MHz tick. Not a whole number for a tick that clk_ref / CYCLES does not divide evenly."""
        return max(TICKS_PER_NANO * 1e9 / self._tick_hz, 1.0)

    @staticmethod
    def _ticks_of(counts: int, period: float) -> int:
        """Ticks from a count to the `counts`th after it: `ceil(counts * period)`, exact when the period is a whole number."""
        whole = int(period)
        return counts * whole if whole == period else math.ceil(counts * period)

    def _split(self, total: int) -> tuple[int, int]:
        """`total` ticks after a count: (the counts that have passed, the ticks since the last of them)."""
        period = self._period()
        whole = int(period)
        if whole == period:
            return divmod(total, whole)
        counts = int(total / period)
        while self._ticks_of(counts + 1, period) <= total:
            counts += 1
        while counts > 0 and self._ticks_of(counts, period) > total:
            counts -= 1
        return counts, total - self._ticks_of(counts, period)

    def _count_and_sub(self) -> tuple[int, int]:
        """The count (64 bits, wrapping) and the ticks since it last changed. Frozen while PAUSE is set or the tick is stopped."""
        if self._frozen():
            return self._count, self._sub
        counts, sub = self._split(self._sub + self.clock.ticks - self._epoch_ticks)
        return (self._count + counts) & 0xFFFFFFFFFFFFFFFF, sub

    def _arm_clock_alarm(self, alarm: "RPTimerAlarm") -> None:
        """(Re)schedules `alarm`'s clock alarm for the moment the low 32 bits of the count reach its target, exactly: the tick at which that count begins (a target the count is already on fires at once)."""
        total = self._sub + self.clock.ticks - self._epoch_ticks
        passed, _sub = self._split(total)
        counts = (alarm.target_micros - self._count - passed) & 0xFFFFFFFF
        if counts == 0:
            alarm.clock_alarm.schedule_ticks(0)
            return
        alarm.clock_alarm.schedule_ticks(self._ticks_of(passed + counts, self._period()) - total)

    def read_uint32(self, offset: int) -> int:
        time = self._count_and_sub()[0]

        if offset == TIMEHR:
            return self._latched_time_high

        if offset == TIMELR:
            self._latched_time_high = time >> 32  # the 64-bit counter wraps
            return time & 0xFFFFFFFF

        if offset == TIMERAWH:
            return time >> 32

        if offset == TIMERAWL:
            return time & 0xFFFFFFFF

        if offset == ALARM0:
            return self.alarms[0].target_micros
        if offset == ALARM1:
            return self.alarms[1].target_micros
        if offset == ALARM2:
            return self.alarms[2].target_micros
        if offset == ALARM3:
            return self.alarms[3].target_micros

        if offset == PAUSE:
            return 1 if self._paused else 0

        if offset == DBGPAUSE:
            return self._dbgpause

        if offset == INTR:
            return self._int_raw
        if offset == INTE:
            return self._int_enable
        if offset == INTF:
            return self._int_force
        if offset == INTS:
            return self._int_status

        if offset == ARMED:
            result = 0
            for alarm in self.alarms:
                if alarm.armed:
                    result |= alarm.bit_value
            return result

        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset in ALARM_REGS:
            alarm_index = (offset - ALARM0) // 4
            alarm = self.alarms[alarm_index]
            alarm.armed = True
            # ALARMn is a 32-bit register: keep what a read of it would return, not the raw Python int the caller passed
            # (a negative or wider one made the bus's 32-bit read-back raise OverflowError).
            alarm.target_micros = value & 0xFFFFFFFF
            if (
                not self._frozen()
            ):  # the count is frozen: the alarm is armed and waits for PAUSE to be cleared / the tick to run
                self._arm_clock_alarm(alarm)

        elif offset == ARMED:
            for alarm in self.alarms:
                if self.raw_write_value & alarm.bit_value:
                    self._disarm_alarm(alarm)

        elif offset == PAUSE:
            self._set_paused(bool(value & 1))

        elif offset == DBGPAUSE:
            self._dbgpause = value & DBGPAUSE_MASK

        elif offset == TIMELW:
            self._time_latch_low = value & 0xFFFFFFFF

        elif offset == TIMEHW:
            # "always write timelw before timehw": the write of TIMEHW sets the whole 64-bit time (RP2040 datasheet, TIMER TIMEHW/TIMELW)
            self._set_time(((value & 0xFFFFFFFF) << 32) | self._time_latch_low)

        elif offset == INTR:
            self._int_raw &= ~self.raw_write_value
            self._check_interrupts()

        elif offset == INTE:
            self._int_enable = value & 0xF
            self._check_interrupts()

        elif offset == INTF:
            self._int_force = value & 0xF
            self._check_interrupts()

        else:
            super().write_uint32(offset, value)

    def _set_paused(self, paused: bool) -> None:
        """PAUSE: "Set high to pause the timer" (RP2040 datasheet). The count freezes and no alarm can come due; clearing it resumes from the frozen count."""
        if paused != self._paused:
            self._apply(paused, self._tick_hz)

    def tick_changed(self, tick_hz: float) -> None:
        """The watchdog's tick is now `tick_hz` (0: stopped): the count continues from where it is at the new rate, or freezes - and the armed alarms are re-timed."""
        if tick_hz != self._tick_hz:
            self._apply(self._paused, tick_hz)

    def _apply(self, paused: bool, tick_hz: float) -> None:
        """Switch to a new PAUSE / tick state with the count continuous across it (the ticks into the current count are kept in proportion when the period changes)."""
        was_frozen = self._frozen()
        count, sub = self._count_and_sub()
        old_period = None if was_frozen else self._period()
        self._paused, self._tick_hz = paused, tick_hz
        if self._frozen():
            self._count, self._sub = count, sub
            if not was_frozen:
                for alarm in self.alarms:
                    alarm.clock_alarm.cancel()
        else:
            if old_period is not None:
                sub = int(sub * self._period() / old_period)
            self._count, self._sub, self._epoch_ticks = count, sub, self.clock.ticks
            self._reschedule_armed_alarms()

    def _set_time(self, count: int) -> None:
        """The count is now `count`; every armed alarm is re-timed against it (an alarm matches the counter, not a moment of simulated time)."""
        if self._frozen():
            self._count, self._sub = count, 0
            return
        self._count, self._sub, self._epoch_ticks = count, 0, self.clock.ticks
        self._reschedule_armed_alarms()

    def _reschedule_armed_alarms(self) -> None:
        for alarm in self.alarms:
            if alarm.armed:
                self._arm_clock_alarm(alarm)

    def _fire_alarm(self, index: int) -> None:
        alarm = self.alarms[index]
        self._disarm_alarm(alarm)
        self._int_raw |= alarm.bit_value
        self._check_interrupts()

    def _check_interrupts(self) -> None:
        int_status = self._int_status
        for i in range(len(self.alarms)):
            self.rp2040.set_interrupt(TIMER_INTERRUPTS[i], bool(int_status & (1 << i)))

    def _disarm_alarm(self, alarm: RPTimerAlarm) -> None:
        alarm.clock_alarm.cancel()
        alarm.armed = False
