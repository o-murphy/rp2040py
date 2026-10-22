# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""Native Cython port of `SimulationClock`/`ClockAlarm` (`clock/_simulation_clock.py`'s own
pure-Python reference) - the last piece of the per-instruction hot path that still stayed a plain
Python object even under an otherwise fully-native `execute_batch()`
(`native/_simulator.pyx`/`native/_rp2040.pyx`): `clock.tick()` and its two properties
(`nanos_to_next_alarm`, `has_scheduled_alarm`) were ordinary Python calls on every single simulated
CPU instruction, paying full interpreter call overhead for a body that's a handful of attribute
reads/comparisons. See docs/tasks/simulation-clock-cython-port.md for the concrete motivation -
`nic.active(True)` on a real `v1.28.0` boot costing ~450s real time for ~1 simulated second, once
correctness bugs were separately ruled out (docs/records/0037-pio-clock-coupled-stepping.md,
docs/records/0038-cyw43-ioctl-response-zero-fill.md).

Faithful, mechanical transcription of `clock/_simulation_clock.py` - read that file for the
*meaning* of each branch; this file only re-derives them where the C types actually change
something. See `_simulation_clock.pxd`'s own comment for which fields/methods stay module-private
(`cdef`, not `public`/`cpdef`) and why `ClockAlarm._clock` stays `object`-typed rather than the
concrete `SimulationClock`.

`SimulationClock` deliberately does NOT subclass `clock.clock.IClock` (a `typing.Protocol`): a
`cdef class` can only extend `object` or another extension type, and a `Protocol`'s metaclass
machinery isn't compatible with that. Structural conformance (duck typing) is enough - every
caller type-hints `clock: IClock`, none ever does `isinstance(clock, IClock)`. Same convention as
this package's other native cdef classes (`RP2040`, `CortexM0Core`, `StateMachine`), none of which
subclass their own pure-Python protocol/reference type either.

`MockClock` (`clock/mock_clock.py`, a second `IClock` implementation used only by tests) subclasses
whichever `SimulationClock` the `clock.simulation_clock` facade resolves to - a plain Python class
subclassing a non-`final` `cdef class` is standard, supported Cython/CPython behavior, so this
stays correct with this native class active exactly as it did with the pure-Python one.


Since docs/records/0096-cpp-mcu-core.md (Phase 2) the arithmetic itself - the clock's time, the sorted
alarm list, `tick()` - is the C++ `Clock` of `core/clock.hpp`, and this file is the Python-facing shell
around it with the same API. What did not move: the callbacks are still Python callables (a Cython
trampoline calls them), and a callback that raises still propagates out of `tick()` with the clock left
at that alarm's time, exactly as before. The pure-Python reference stays the oracle -
tests/test_simulation_clock_parity.py replays randomized alarm scripts against both.
"""

import inspect
import weakref

from libcpp cimport bool

from rp2040py.native._pending cimport has_pending_error, park_error, raise_if_pending
from libc.stdint cimport int64_t

from rp2040py.native._clock cimport cancel_alarm, kTicksPerNano

cdef bool _fire_alarm(void* ctx) noexcept:
    """What the C++ clock calls when an alarm comes due: drop the alarm's keep-alive (it is no longer
    linked), then run its Python callback. False stops the tick - the callback raised."""
    cdef ClockAlarm alarm = <ClockAlarm> ctx
    (<SimulationClock> alarm._clock)._armed.discard(alarm)
    try:
        alarm.callback()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _rebase_trampoline(void* ctx, int64_t delta) noexcept:
    """The origin moved (the clock reached `kRebaseAt`): tells the Python holders of an absolute tick count. They must not raise; an error is parked like any callback's."""
    cdef SimulationClock clock = <SimulationClock> ctx
    for ref in list(clock._rebase_callbacks):
        callback = ref()
        if callback is None:
            clock._rebase_callbacks.remove(ref)
            continue
        try:
            callback(delta)
        except BaseException as error:
            park_error(error)


cdef class ClockAlarm:
    def __cinit__(self, *args, **kwargs):
        self._node.fire = _fire_alarm
        self._node.ctx = <void*> self

    def __dealloc__(self):
        # Reached while still linked only when the collector frees the clock's `_armed` set before this alarm; the C++ clock
        # holds a pointer into this object's node, so take it out of the list (a no-op if the clock itself is gone already).
        cancel_alarm(&self._node)

    def __init__(self, clock, callback):
        self._clock = clock
        self.callback = callback

    cpdef schedule(self, double delta_nanos):
        cdef SimulationClock clock = <SimulationClock> self._clock
        if self._node.scheduled:
            self.cancel()
        clock.link_alarm(delta_nanos, self)

    cpdef schedule_ticks(self, int64_t delta_ticks):
        cdef SimulationClock clock = <SimulationClock> self._clock
        if self._node.scheduled:
            self.cancel()
        clock.link_alarm_ticks(delta_ticks, self)

    @property
    def ticks(self):
        """The absolute due time while the alarm is linked, in ticks."""
        return self._node.ticks

    @property
    def nanos(self):
        return self._node.ticks / <double> kTicksPerNano

    cpdef cancel(self):
        cdef SimulationClock clock = <SimulationClock> self._clock
        clock.unlink_alarm(self)
        self._node.scheduled = False


cdef class SimulationClock:
    def __cinit__(self, *args, **kwargs):
        self._armed = set()
        self._rebase_callbacks = []
        self._clock.add_rebase_listener(&self._rebase_node, _rebase_trampoline, <void*> self)

    def __init__(self, double frequency=125e6):
        self._clock.frequency = frequency

    @property
    def frequency(self):
        return self._clock.frequency

    @frequency.setter
    def frequency(self, double value):
        self._clock.frequency = value

    @property
    def nanos(self):
        return self._clock.nanos()

    @property
    def ticks(self):
        """The time in ticks of 1/16 ns, exactly (`nanos` is derived from it)."""
        return self._clock.ticks()

    @property
    def rebased_ticks(self):
        """The ticks the origin has been moved by so far (`nanos` includes them, so it never goes back)."""
        return self._clock.rebased_ticks()

    def add_rebase_listener(self, callback):
        """`callback(delta)` is called when the origin moves: whoever holds an absolute `clock.ticks` value subtracts `delta` from it. Returns the function that unsubscribes it."""
        # A bound method is held weakly: the clock must not keep its holder (a block, a chip) alive.
        ref = weakref.WeakMethod(callback) if inspect.ismethod(callback) else (lambda: callback)
        self._rebase_callbacks.append(ref)

        def unsubscribe():
            if ref in self._rebase_callbacks:
                self._rebase_callbacks.remove(ref)

        return unsubscribe

    def rebase(self, delta):
        """Moves the origin by `delta` ticks (the clock does it itself at `kRebaseAt`; the tests call it by hand)."""
        self._clock.rebase(<int64_t> delta)

    @property
    def saturated(self):
        return self._clock.saturated() != 0

    @property
    def micros(self):
        return self._clock.nanos() / 1000

    cpdef ClockAlarm create_alarm(self, callback):
        return ClockAlarm(self, callback)

    cpdef ClockAlarm link_alarm(self, double nanos, ClockAlarm alarm):
        # Same due time as alarms already linked: this one goes AFTER them (FIFO) - see core/clock.hpp for why
        # (a zero-delay producer must not starve a pending zero-delay consumer, docs/records/0044).
        if alarm._node.scheduled:
            self._clock.unlink(&alarm._node)  # never link one alarm twice
        self._clock.link(&alarm._node, nanos)
        self._armed.add(alarm)
        return alarm

    cpdef ClockAlarm link_alarm_ticks(self, int64_t ticks, ClockAlarm alarm):
        if alarm._node.scheduled:
            self._clock.unlink(&alarm._node)
        self._clock.link_ticks(&alarm._node, ticks)
        self._armed.add(alarm)
        return alarm

    cpdef bint unlink_alarm(self, ClockAlarm alarm) except -1:
        cdef bint found = self._clock.unlink(&alarm._node)
        if found:
            alarm._node.scheduled = False
            self._armed.discard(alarm)
        return found

    cpdef tick(self, double delta_nanos):
        if not self._clock.tick(delta_nanos):
            if not has_pending_error():
                raise RuntimeError("the clock stopped ticking without a pending error")
            raise_if_pending()

    cpdef tick_ticks(self, int64_t delta_ticks):
        if not self._clock.tick_ticks(delta_ticks):
            if not has_pending_error():
                raise RuntimeError("the clock stopped ticking without a pending error")
            raise_if_pending()

    cpdef defer_ticks(self, int64_t delta_ticks):
        """Like `tick_ticks`, but the time is only recorded as run: it is applied when an alarm is due or when anything reads it (the batch loop of a CPU running alone)."""
        if not self._clock.defer_ticks(delta_ticks):
            if not has_pending_error():
                raise RuntimeError("the clock stopped ticking without a pending error")
            raise_if_pending()

    cpdef advance_cycles(self, int64_t cycles, double ticks_per_cycle):
        """Advances by `cycles` periods of `ticks_per_cycle` ticks, the fraction carried between calls (the CPU's time)."""
        if not self._clock.advance_cycles(cycles, ticks_per_cycle):
            if not has_pending_error():
                raise RuntimeError("the clock stopped ticking without a pending error")
            raise_if_pending()

    @property
    def nanos_to_next_alarm(self):
        return self._clock.nanos_to_next_alarm()

    @property
    def ticks_to_next_alarm(self):
        return self._clock.ticks_to_next_alarm()

    @property
    def has_scheduled_alarm(self):
        """See `_simulation_clock.py`'s own copy of this docstring for why this is a separate
        property from `nanos_to_next_alarm == 0` - distinguishes "no alarm scheduled" from "an
        alarm is scheduled and due right now", which matters for `simulator.py`'s opt-in
        clock-tick-batching (RP2040PY_CLOCK_TICK_BATCH)."""
        return self._clock.has_alarm()
