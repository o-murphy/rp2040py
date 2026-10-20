"""Pure-Python reference implementation of `SimulationClock`/`ClockAlarm`. `simulation_clock.py`
is the public facade that picks this or the native `rp2040py.native._simulation_clock` port;
callers should import `SimulationClock`/`ClockAlarm` from there, never from here directly - same
relationship `_state_machine.py` has to `state_machine.py`.
"""

from __future__ import annotations

import inspect
import math
import weakref
from collections.abc import Callable

from rp2040py.clock.clock import AlarmCallback, IAlarm, IClock

__all__ = (
    "MAX_TICKS",
    "TICKS_PER_NANO",
    "ClockAlarm",
    "SimulationClock",
    "nanos_to_ticks",
)

# Time is an INTEGER count of ticks of 1/16 ns (docs/records/0100-integer-clock.md): 18 years in an int64 (the C++ clock), exactly - the `float` nanoseconds this replaced stopped resolving 8 ns
# beyond 104 days. A 125 MHz cycle is 128 ticks.
TICKS_PER_NANO = 16
MAX_TICKS = (1 << 63) // 2 - 1  # "never": times saturate here instead of growing past the C++ clock's int64
# An idle chip fast-forwards (millions of simulated seconds per wall second when a periodic alarm is all that is pending), so the origin MOVES: once the time passes REBASE_AT the clock subtracts it from
# `now`, from every linked alarm and - through the listeners - from every absolute tick count a block holds (docs/records/0100-integer-clock.md). `nanos` still never goes back.
REBASE_AT = 1 << 58
NO_HORIZON = 1 << 62  # no alarm linked: time may run on without ever having to fire one
LAZY_DELTA = (
    1 << 40
)  # the largest step `defer_ticks` takes on its fast path (2**40 ticks, ~69 s of simulated time), far above any instruction's


def nanos_to_ticks(nanos: float) -> int:
    """A delta in nanoseconds as ticks: round half up (but a positive delta is at least one tick), saturating at +-MAX_TICKS, NaN as 0 - the same function as `nanos_to_ticks` in core/clock.hpp."""
    if math.isnan(nanos):
        return 0
    ticks = nanos * TICKS_PER_NANO
    if ticks >= MAX_TICKS:
        return MAX_TICKS
    if ticks <= -MAX_TICKS:
        return -MAX_TICKS
    rounded = math.floor(ticks + 0.5)
    # A delay that is positive stays at least one tick: an alarm that re-arms itself with a vanishing positive delay must still move time forward, or the clock would fire it again
    # at the very same instant, for ever (the float clock moved by the sliver, however small).
    return 1 if rounded == 0 and ticks > 0 else rounded


ClockEventCallback = Callable[[], None]


class ClockAlarm(IAlarm):
    def __init__(self, clock: SimulationClock, callback: AlarmCallback):
        self._clock = clock
        self.callback = callback
        self.next: ClockAlarm | None = None
        self.ticks: int = 0  # the absolute due time while linked
        self.scheduled = False

    @property
    def nanos(self) -> float:
        return self.ticks / TICKS_PER_NANO

    def schedule(self, delta_nanos: float) -> None:
        if self.scheduled:
            self.cancel()
        self._clock.link_alarm(delta_nanos, self)

    def schedule_ticks(self, delta_ticks: int) -> None:
        if self.scheduled:
            self.cancel()
        self._clock.link_alarm_ticks(delta_ticks, self)

    def cancel(self) -> None:
        self._clock.unlink_alarm(self)
        self.scheduled = False


class SimulationClock(IClock):
    def __init__(self, frequency: float = 125e6):
        self.frequency = frequency
        self._next_alarm: ClockAlarm | None = None
        self._ticks: int = 0
        self._rebased_ticks = 0
        self._rebase_depth = 0
        self._rebase_listeners: list[Callable[[], Callable[[int], None] | None]] = []
        self._cycle_remainder = 0.0
        self._pending = (
            0  # ticks deferred by `defer_ticks`, not yet applied to `_ticks` (always below `_horizon` while at rest)
        )
        self._horizon = (
            NO_HORIZON  # `_next_alarm.ticks - _ticks`: how far the deferred time may grow before the first alarm is due
        )
        self.saturated = False  # a time was clamped to MAX_TICKS

    @property
    def ticks(self) -> int:
        """The time in ticks of 1/16 ns, exactly."""
        if self._pending:
            self._flush()
        return self._ticks

    @property
    def nanos(self) -> float:
        """The time in nanoseconds, derived (inexact beyond 2**53 / 16 ns: take differences in `ticks`)."""
        if self._pending:
            self._flush()
        return (self._ticks + self._rebased_ticks) / TICKS_PER_NANO

    @property
    def rebased_ticks(self) -> int:
        """The ticks the origin has been moved by so far (`nanos` includes them, so it never goes back)."""
        return self._rebased_ticks

    def add_rebase_listener(self, callback: Callable[[int], None]) -> Callable[[], None]:
        """`callback(delta)` is called when the origin moves: whoever holds an absolute `clock.ticks` value subtracts `delta` from it. Returns the function that unsubscribes it."""
        # A bound method is held weakly: the clock must not keep its holder (a block, a chip) alive.
        ref = weakref.WeakMethod(callback) if inspect.ismethod(callback) else (lambda: callback)
        self._rebase_listeners.append(ref)

        def unsubscribe() -> None:
            if ref in self._rebase_listeners:
                self._rebase_listeners.remove(ref)

        return unsubscribe

    def rebase(self, delta: int) -> None:
        """Moves the origin by `delta` ticks (the clock does it itself at REBASE_AT; the tests call it by hand). Not from inside an alarm's callback."""
        if self._pending:
            self._flush()
        if delta <= 0 or delta > self._ticks:
            return
        self._ticks -= delta
        self._rebased_ticks += delta
        alarm = self._next_alarm
        while alarm:
            if alarm.ticks < MAX_TICKS:  # a "never" alarm stays at the end
                alarm.ticks -= delta
            alarm = alarm.next
        for ref in list(self._rebase_listeners):
            callback = ref()
            if callback is None:
                self._rebase_listeners.remove(ref)
            else:
                callback(delta)

    def _to_ticks(self, nanos: float) -> int:
        """`nanos_to_ticks`, recording a clamp."""
        ticks = nanos_to_ticks(nanos)
        if ticks in (MAX_TICKS, -MAX_TICKS):
            self.saturated = True
        return ticks

    def _add_ticks(self, a: int, b: int) -> int:
        if b > 0 and a > MAX_TICKS - b:
            self.saturated = True
            return MAX_TICKS
        return a + b

    @property
    def micros(self) -> float:
        return self.nanos / 1000

    def create_alarm(self, callback: ClockEventCallback) -> ClockAlarm:
        return ClockAlarm(self, callback)

    def link_alarm(self, nanos: float, alarm: ClockAlarm) -> ClockAlarm:
        return self.link_alarm_ticks(self._to_ticks(nanos), alarm)

    def link_alarm_ticks(self, ticks: int, alarm: ClockAlarm) -> ClockAlarm:
        if self._pending:
            self._flush()
        alarm.ticks = self._add_ticks(self._ticks, ticks)
        alarm_list_item = self._next_alarm
        last_item = None
        # `<=`, not `<`: a same-timestamp alarm already in the list must fire before one just
        # inserted "at" that same instant (FIFO tie-break), or a self-rescheduling zero-delay
        # producer (e.g. a DMA TX channel) can perpetually cut in front of an already-pending
        # zero-delay consumer alarm (e.g. that same transfer's paired RX channel) and starve it -
        # see docs/tasks/main-spi-hang.md.
        while alarm_list_item and alarm_list_item.ticks <= alarm.ticks:
            last_item = alarm_list_item
            alarm_list_item = alarm_list_item.next
        if last_item:
            last_item.next = alarm
            alarm.next = alarm_list_item
        else:
            self._next_alarm = alarm
            alarm.next = alarm_list_item
        alarm.scheduled = True
        self._refresh_horizon()
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
                self._refresh_horizon()
                return True
            last_item = alarm_list_item
            alarm_list_item = alarm_list_item.next
        return False

    def tick(self, delta_nanos: float) -> None:
        self.tick_ticks(self._to_ticks(delta_nanos))

    def tick_ticks(self, delta_ticks: int) -> None:
        if self._pending:
            self._flush()
        self._tick_now(delta_ticks)
        self._refresh_horizon()

    def defer_ticks(self, delta_ticks: int) -> None:
        """The batch loop's way of letting time pass: records `delta_ticks` as run but not yet applied. Nothing is applied until an alarm is due (`_pending` reaches `_horizon`, the time to the next
        alarm) or somebody reads the time, so a long stretch of instructions that touches no timing-dependent block costs one addition and one comparison each. Equivalent to `tick_ticks(delta)` after
        every instruction: an alarm fires at the end of the very instruction that makes it due, and every read sees the time as of the end of the previous one. The only way to learn the time, or to
        measure a delay from it, is through this class (`ticks`, `nanos`, `ticks_to_next_alarm`, `link_alarm*`, `rebase`, `tick*`), and each of them settles first."""
        if delta_ticks < 0 or delta_ticks >= LAZY_DELTA:
            self.tick_ticks(delta_ticks)
            return
        self._pending += delta_ticks
        if self._pending >= self._horizon:
            self._flush()

    def _flush(self) -> None:
        """Applies the deferred ticks."""
        delta = self._pending
        self._pending = 0
        try:
            self._tick_now(delta)
        finally:
            self._refresh_horizon()

    def _refresh_horizon(self) -> None:
        alarm = self._next_alarm
        self._horizon = alarm.ticks - self._ticks if alarm else NO_HORIZON

    def _tick_now(self, delta_ticks: int) -> None:
        target = self._add_ticks(self._ticks, delta_ticks)
        self._rebase_depth += 1
        try:
            alarm = self._next_alarm
            while alarm and alarm.ticks <= target:
                self._next_alarm = alarm.next
                self._ticks = max(
                    self._ticks, alarm.ticks
                )  # a callback may have ticked on itself: time never goes back
                alarm.callback()
                alarm = self._next_alarm
            if (
                delta_ticks < 0 or target > self._ticks
            ):  # (a callback that ticked on itself may have gone past it already)
                self._ticks = target
        finally:
            self._rebase_depth -= 1
        if self._rebase_depth == 0 and self._ticks >= REBASE_AT:  # the outermost tick only: nothing is walking the list
            self.rebase(self._ticks)

    def advance_cycles(self, cycles: int, ticks_per_cycle: float) -> None:
        """Advances by `cycles` periods of `ticks_per_cycle` ticks each (not a whole number in general: 120.30 at 133 MHz) with the fraction CARRIED between calls, so the long-run rate is exact
        however the cycles are split into calls - the CPU's time, one call per instruction."""
        self._cycle_remainder += cycles * ticks_per_cycle
        whole = math.floor(self._cycle_remainder)
        self._cycle_remainder -= whole
        self.tick_ticks(min(MAX_TICKS, whole))

    @property
    def ticks_to_next_alarm(self) -> int:
        if self._pending:
            self._flush()
        if self._next_alarm:
            return self._next_alarm.ticks - self._ticks
        return 0

    @property
    def nanos_to_next_alarm(self) -> float:
        return self.ticks_to_next_alarm / TICKS_PER_NANO

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
