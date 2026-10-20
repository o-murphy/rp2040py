# Declaration file paired with _simulation_clock.pyx. See that file's module docstring for the
# overall port rationale.
#
# Since docs/records/0096-cpp-mcu-core.md Phase 2 the time and the sorted alarm list live in a C++
# `Clock` (core/clock.hpp) embedded in `SimulationClock`; `ClockAlarm` embeds the C++ `Alarm` node the
# clock links by pointer. `SimulationClock._armed` is the Python-side owner of every linked alarm: the C++
# list holds raw pointers, so something the garbage collector can see has to keep a *scheduled* alarm
# alive (a fire-and-forget `clock.create_alarm(cb).schedule(n)` must still fire) and has to make the
# clock <-> alarm cycle collectable once the clock itself is dropped.
#
# `_clock` is declared here, not hidden, so a module that cimports SimulationClock (native/_simulator.pyx's
# per-instruction loop) can call the C++ `tick()`/`nanos_to_next_alarm()` directly. ClockAlarm._clock stays
# `object`-typed, not SimulationClock: it is only read from schedule()/cancel(), and typing it would need
# the two classes to forward-declare each other for no hot-path benefit.
from libc.stdint cimport int64_t

from rp2040py.native._clock cimport Alarm, Clock, RebaseListener


cdef class ClockAlarm:
    cdef object _clock
    cdef object callback
    cdef Alarm _node

    cpdef schedule(self, double delta_nanos)
    cpdef schedule_ticks(self, int64_t delta_ticks)
    cpdef cancel(self)


cdef class SimulationClock:
    cdef Clock _clock
    cdef set _armed
    cdef list _rebase_callbacks
    cdef RebaseListener _rebase_node

    cpdef ClockAlarm create_alarm(self, callback)
    cpdef ClockAlarm link_alarm(self, double nanos, ClockAlarm alarm)
    cpdef ClockAlarm link_alarm_ticks(self, int64_t ticks, ClockAlarm alarm)
    cpdef bint unlink_alarm(self, ClockAlarm alarm) except -1
    cpdef tick(self, double delta_nanos)
    cpdef tick_ticks(self, int64_t delta_ticks)
    cpdef defer_ticks(self, int64_t delta_ticks)
    cpdef advance_cycles(self, int64_t cycles, double ticks_per_cycle)
