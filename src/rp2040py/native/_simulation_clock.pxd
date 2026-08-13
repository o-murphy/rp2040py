# Declaration file paired with _simulation_clock.pyx. See that file's module docstring for the
# overall port rationale.
#
# ClockAlarm's next/nanos/scheduled/callback and SimulationClock's link_alarm/unlink_alarm stay
# plain `cdef` (module-private, not `public`/`cpdef`) - nothing outside this one file ever touches
# them (confirmed via grep this session - every external caller only uses IClock/IAlarm's own
# create_alarm()/alarm.schedule()/alarm.cancel(), plus SimulationClock's own extra nanos/micros/
# tick()/nanos_to_next_alarm/has_scheduled_alarm).
#
# ClockAlarm.next is typed as the concrete ClockAlarm (not object): SimulationClock.tick() walks
# this pointer chain on the per-instruction hot path, so it must resolve as a direct C-level
# struct-field read, not a Python attribute lookup. ClockAlarm._clock stays `object`, not
# SimulationClock: it's only read from schedule()/cancel() (armed/re-armed far less often than
# every instruction, unlike tick()), and typing it would need SimulationClock and ClockAlarm to
# forward-declare each other in this one module for no hot-path benefit - not worth the added
# fragility. Defining ClockAlarm first (SimulationClock needs the already-complete ClockAlarm type
# for its own _next_alarm field and link_alarm/unlink_alarm signatures) sidesteps the issue
# entirely.
cdef class ClockAlarm:
    cdef object _clock
    cdef object callback
    cdef ClockAlarm next
    cdef double nanos
    cdef bint scheduled

    cpdef schedule(self, double delta_nanos)
    cpdef cancel(self)


cdef class SimulationClock:
    cdef public double frequency
    cdef ClockAlarm _next_alarm
    cdef double _nanos_counter

    cpdef ClockAlarm create_alarm(self, callback)
    cpdef ClockAlarm link_alarm(self, double nanos, ClockAlarm alarm)
    cpdef bint unlink_alarm(self, ClockAlarm alarm) except -1
    cpdef tick(self, double delta_nanos)
