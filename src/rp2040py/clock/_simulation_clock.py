"""Pure-Python reference implementation of `SimulationClock`/`ClockAlarm`. `simulation_clock.py`
is the public facade that picks this or the native `rp2040py.native._simulation_clock` port;
callers should import `SimulationClock`/`ClockAlarm` from there, never from here directly - same
relationship `_state_machine.py` has to `state_machine.py`.
"""

from __future__ import annotations

from collections.abc import Callable

from rp2040py.clock.clock import AlarmCallback, IAlarm, IClock

__all__ = (
    "ClockAlarm",
    "SimulationClock",
)

ClockEventCallback = Callable[[], None]


class ClockAlarm(IAlarm):
    def __init__(self, clock: SimulationClock, callback: AlarmCallback):
        self._clock = clock
        self.callback = callback
        self.next: ClockAlarm | None = None
        self.nanos: float = 0
        self.scheduled = False

    def schedule(self, delta_nanos: float) -> None:
        if self.scheduled:
            self.cancel()
        self._clock.link_alarm(delta_nanos, self)

    def cancel(self) -> None:
        self._clock.unlink_alarm(self)
        self.scheduled = False


class SimulationClock(IClock):
    def __init__(self, frequency: float = 125e6):
        self.frequency = frequency
        self._next_alarm: ClockAlarm | None = None
        self._nanos_counter: float = 0

    @property
    def nanos(self) -> float:
        return self._nanos_counter

    @property
    def micros(self) -> float:
        return self.nanos / 1000

    def create_alarm(self, callback: ClockEventCallback) -> ClockAlarm:
        return ClockAlarm(self, callback)

    def link_alarm(self, nanos: float, alarm: ClockAlarm) -> ClockAlarm:
        alarm.nanos = self.nanos + nanos
        alarm_list_item = self._next_alarm
        last_item = None
        while alarm_list_item and alarm_list_item.nanos < alarm.nanos:
            last_item = alarm_list_item
            alarm_list_item = alarm_list_item.next
        if last_item:
            last_item.next = alarm
            alarm.next = alarm_list_item
        else:
            self._next_alarm = alarm
            alarm.next = alarm_list_item
        alarm.scheduled = True
        return alarm

    def unlink_alarm(self, alarm: ClockAlarm) -> bool:
        alarm_list_item = self._next_alarm
        if not alarm_list_item:
            return False
        last_item = None
        while alarm_list_item:
            if alarm_list_item is alarm:
                if last_item:
                    last_item.next = alarm_list_item.next
                else:
                    self._next_alarm = alarm_list_item.next
                return True
            last_item = alarm_list_item
            alarm_list_item = alarm_list_item.next
        return False

    def tick(self, delta_nanos: float) -> None:
        target_nanos = self._nanos_counter + delta_nanos
        alarm = self._next_alarm
        while alarm and alarm.nanos <= target_nanos:
            self._next_alarm = alarm.next
            self._nanos_counter = alarm.nanos
            alarm.callback()
            alarm = self._next_alarm
        self._nanos_counter = target_nanos

    @property
    def nanos_to_next_alarm(self) -> float:
        if self._next_alarm:
            return self._next_alarm.nanos - self.nanos
        return 0

    @property
    def has_scheduled_alarm(self) -> bool:
        """Distinguishes "no alarm scheduled at all" from "an alarm is scheduled and due right
        now" - both read as `nanos_to_next_alarm == 0`, which is fine for that property's own two
        existing callers (both just want "how long to jump forward," and jumping forward by 0 when
        nothing's scheduled is already correct) but is genuinely ambiguous for
        simulator.py's opt-in clock-tick-batching (RP2040PY_CLOCK_TICK_BATCH): with no alarm
        scheduled, there's no due time to avoid overshooting, so batching can run unbounded rather
        than conservatively flushing every instruction."""
        return self._next_alarm is not None
