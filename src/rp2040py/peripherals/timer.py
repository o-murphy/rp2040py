import math
from typing import TYPE_CHECKING

from rp2040py.clock.clock import IAlarm, IClock
from rp2040py.irq import IRQ
from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPTimer",)

TIMEHR = 0x08
TIMELR = 0x0C
TIMERAWH = 0x24
TIMERAWL = 0x28
ALARM0 = 0x10
ALARM1 = 0x14
ALARM2 = 0x18
ALARM3 = 0x1C
ARMED = 0x20
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
SCRATCH_LIKE_MASK = 0xFFFFFFFF


def _to_uint32(value: float) -> int:
    return int(value) & SCRATCH_LIKE_MASK


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
        self.alarms = [
            RPTimerAlarm(ALARM_0, self.clock.create_alarm(lambda: self._fire_alarm(0))),
            RPTimerAlarm(ALARM_1, self.clock.create_alarm(lambda: self._fire_alarm(1))),
            RPTimerAlarm(ALARM_2, self.clock.create_alarm(lambda: self._fire_alarm(2))),
            RPTimerAlarm(ALARM_3, self.clock.create_alarm(lambda: self._fire_alarm(3))),
        ]

    def reset(self) -> None:
        """Registers and all four alarms, back to power-on (0089 Phase 5).

        `clock_alarm.cancel()` is the load-bearing line: a scheduled alarm that survived would fire
        into freshly-reset registers and raise an interrupt for a deadline the guest set before the
        reset - the emulated equivalent of a timer that kept counting through a power cut. The
        `RPTimerAlarm` objects themselves stay, since the clock holds them.

        The timer's own count is not reset: it reads from `clock.nanos`, which is the simulation's
        time base rather than this block's state. A real `TIMER` does restart from zero, so this is
        a known deviation - shared with every other reset path here, and unchanged by this phase.
        """
        self._latched_time_high = 0
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0
        self._paused = False
        for alarm in self.alarms:
            alarm.clock_alarm.cancel()
            alarm.armed = False
            alarm.target_micros = 0
        for irq in TIMER_INTERRUPTS:
            self.rp2040.set_interrupt(irq, False)

    @property
    def int_status(self) -> int:
        return (self._int_raw & self._int_enable) | self._int_force

    def read_uint32(self, offset: int) -> int:
        time = self.clock.nanos / 1000

        if offset == TIMEHR:
            return self._latched_time_high

        if offset == TIMELR:
            self._latched_time_high = math.floor(time / 2**32)
            return _to_uint32(time)

        if offset == TIMERAWH:
            return math.floor(time / 2**32)

        if offset == TIMERAWL:
            return _to_uint32(time)

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

        if offset == INTR:
            return self._int_raw
        if offset == INTE:
            return self._int_enable
        if offset == INTF:
            return self._int_force
        if offset == INTS:
            return self.int_status

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
            delta_micros = _to_uint32(value - self.clock.nanos / 1000)
            alarm.armed = True
            alarm.target_micros = value
            alarm.clock_alarm.schedule(delta_micros * 1000)

        elif offset == ARMED:
            for alarm in self.alarms:
                if self.raw_write_value & alarm.bit_value:
                    self._disarm_alarm(alarm)

        elif offset == PAUSE:
            self._paused = bool(value & 1)
            if self._paused:
                self.warn("Unimplemented Timer Pause")
            # TODO actually pause the timer

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

    def _fire_alarm(self, index: int) -> None:
        alarm = self.alarms[index]
        self._disarm_alarm(alarm)
        self._int_raw |= alarm.bit_value
        self._check_interrupts()

    def _check_interrupts(self) -> None:
        int_status = self.int_status
        for i in range(len(self.alarms)):
            self.rp2040.set_interrupt(TIMER_INTERRUPTS[i], bool(int_status & (1 << i)))

    def _disarm_alarm(self, alarm: RPTimerAlarm) -> None:
        alarm.clock_alarm.cancel()
        alarm.armed = False
