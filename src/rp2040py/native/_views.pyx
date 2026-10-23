# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""Python views of C++ state that more than one shell shows the way the reference does (docs/records/0096-cpp-mcu-core.md, Phase 5b): the `Timer32` and
`Timer32PeriodicAlarm` of a block (SysTick, a PWM channel, the watchdog) and a fixed array of words (the watchdog's scratch registers, BUSCTRL's counters).
The state lives in the C++ block; a view holds a pointer into it and the owner that keeps it alive. The shell sets `_timer` / `_alarm` / `_words` and `_n` itself.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._pwm cimport Timer32, Timer32PeriodicAlarm

from rp2040py.utils.timer32 import TimerMode

__all__ = ("Timer32AlarmView", "Timer32View", "WordsView")


cdef class Timer32View:
    """A C++ `Timer32` as the reference's `Timer32` shows it (a block's countdown: SysTick, a PWM channel, the watchdog)."""

    @property
    def counter(self):
        return self._timer.counter()

    @property
    def raw_counter(self):
        return self._timer.raw_counter()

    @property
    def top(self):
        return self._timer.top()

    @top.setter
    def top(self, value):
        self._timer.set_top(<int64_t> value)

    @property
    def frequency(self):
        return self._timer.frequency()

    @frequency.setter
    def frequency(self, value):
        self._timer.set_frequency(<double> value)

    @property
    def prescaler(self):
        return self._timer.prescaler()

    @prescaler.setter
    def prescaler(self, value):
        self._timer.set_prescaler(<double> value)

    @property
    def enable(self):
        return bool(self._timer.enable())

    @enable.setter
    def enable(self, value):
        self._timer.set_enable(bool(value))

    @property
    def mode(self):
        return TimerMode(self._timer.mode_raw())

    @mode.setter
    def mode(self, value):
        self._timer.set_mode_raw(<uint32_t> int(value))

    def set(self, value, zig_zag_down=False):
        self._timer.set(<int64_t> value, bool(zig_zag_down))

    def advance(self, delta):
        self._timer.advance(<int64_t> delta)

    def reset(self):
        self._timer.reset()

    def to_nanos(self, cycles):
        return self._timer.to_nanos(<int64_t> cycles)


cdef class Timer32AlarmView:
    """A C++ `Timer32PeriodicAlarm` as the reference's `Timer32PeriodicAlarm` shows it (`target`, `enable`): the state lives in the C++ block."""

    @property
    def target(self):
        return self._alarm.target()

    @target.setter
    def target(self, value):
        self._alarm.set_target(<int64_t> value)

    @property
    def enable(self):
        return bool(self._alarm.enable())

    @enable.setter
    def enable(self, value):
        self._alarm.set_enable(bool(value))


cdef class WordsView:
    """A reference's list of 32-bit words (`scratch_data`, `perf_ctr`) as a view of a C++ array: indexable, assignable (the tests poke `scratch_data[4] = MAGIC`, `perf_ctr[1] = 99`) and equal to a list of the same values."""


    def __len__(self):
        return self._n

    def __getitem__(self, index):
        return list(self)[index]

    def __setitem__(self, index, value):
        cdef int i = range(self._n)[index]
        self._words[i] = <uint32_t> (value & 0xFFFFFFFF)

    def __iter__(self):
        for i in range(self._n):
            yield self._words[i]

    def __eq__(self, other):
        try:
            return list(self) == list(other)
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return repr(list(self))
