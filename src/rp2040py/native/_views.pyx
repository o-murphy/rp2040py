# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The one Python view of C++ state that more than one shell keeps (docs/records/0096-cpp-mcu-core.md, Phase 5b): the `Timer32` of a block (SysTick, a PWM channel, the watchdog), which
the clock tree retunes (`frequency`) and the chip reads. A view holds a pointer into the block and the owner that keeps it alive; the shell sets `_timer` itself. A block's *state* is its `snapshot()`.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._pwm cimport Timer32

from rp2040py.utils.timer32 import TimerMode

__all__ = ("Timer32View",)


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
