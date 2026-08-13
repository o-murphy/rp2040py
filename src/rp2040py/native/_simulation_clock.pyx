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
"""


cdef class ClockAlarm:
    def __init__(self, clock, callback):
        self._clock = clock
        self.callback = callback
        self.next = None
        self.nanos = 0.0
        self.scheduled = False

    cpdef schedule(self, double delta_nanos):
        if self.scheduled:
            self.cancel()
        self._clock.link_alarm(delta_nanos, self)

    cpdef cancel(self):
        self._clock.unlink_alarm(self)
        self.scheduled = False


cdef class SimulationClock:
    def __init__(self, double frequency=125e6):
        self.frequency = frequency
        self._next_alarm = None
        self._nanos_counter = 0.0

    @property
    def nanos(self):
        return self._nanos_counter

    @property
    def micros(self):
        return self._nanos_counter / 1000

    cpdef ClockAlarm create_alarm(self, callback):
        return ClockAlarm(self, callback)

    cpdef ClockAlarm link_alarm(self, double nanos, ClockAlarm alarm):
        cdef ClockAlarm alarm_list_item = self._next_alarm
        cdef ClockAlarm last_item = None
        alarm.nanos = self._nanos_counter + nanos
        while alarm_list_item is not None and alarm_list_item.nanos < alarm.nanos:
            last_item = alarm_list_item
            alarm_list_item = alarm_list_item.next
        if last_item is not None:
            last_item.next = alarm
            alarm.next = alarm_list_item
        else:
            self._next_alarm = alarm
            alarm.next = alarm_list_item
        alarm.scheduled = True
        return alarm

    cpdef bint unlink_alarm(self, ClockAlarm alarm) except -1:
        cdef ClockAlarm alarm_list_item = self._next_alarm
        cdef ClockAlarm last_item = None
        if alarm_list_item is None:
            return False
        while alarm_list_item is not None:
            if alarm_list_item is alarm:
                if last_item is not None:
                    last_item.next = alarm_list_item.next
                else:
                    self._next_alarm = alarm_list_item.next
                return True
            last_item = alarm_list_item
            alarm_list_item = alarm_list_item.next
        return False

    cpdef tick(self, double delta_nanos):
        cdef double target_nanos = self._nanos_counter + delta_nanos
        cdef ClockAlarm alarm = self._next_alarm
        while alarm is not None and alarm.nanos <= target_nanos:
            self._next_alarm = alarm.next
            self._nanos_counter = alarm.nanos
            alarm.callback()
            alarm = self._next_alarm
        self._nanos_counter = target_nanos

    @property
    def nanos_to_next_alarm(self):
        if self._next_alarm is not None:
            return self._next_alarm.nanos - self._nanos_counter
        return 0

    @property
    def has_scheduled_alarm(self):
        """See `_simulation_clock.py`'s own copy of this docstring for why this is a separate
        property from `nanos_to_next_alarm == 0` - distinguishes "no alarm scheduled" from "an
        alarm is scheduled and due right now", which matters for `simulator.py`'s opt-in
        clock-tick-batching (RP2040PY_CLOCK_TICK_BATCH)."""
        return self._next_alarm is not None
