# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 WATCHDOG: a Python-facing shell over the C++ `WatchdogBlock` of `core/watchdog.hpp` (docs/records/0096-cpp-mcu-core.md). `peripherals/_watchdog.py` is the pure-Python
reference, kept as the oracle (tests/test_watchdog_diff.py); `peripherals/watchdog.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`). The countdown is a
node of the chip's C++ clock, so the shell needs the native `SimulationClock`. What stays Python is the edge: the logger, and the chip's reset - `on_watchdog_trigger`, the attribute a device installs
(the reference's), is called through a trampoline whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in.

The API is the reference's: `scratch_data` (a view of the eight words), `timer` and `alarm` (views with the reference's attributes; `alarm.callback` fires the timeout by hand), `on_watchdog_trigger`,
`reset`, plus `BasePeripheral`'s surface and the private flags the tests read.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.utils.timer32 import TimerMode

__all__ = ("RPWatchdog",)


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPWatchdog block = <RPWatchdog> ctx
    try:
        if kind == kWatchdogWarnRead:
            block.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kWatchdogWarnReadAtomicArea:
            block.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            block.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef cppbool _trigger_trampoline(void* ctx) noexcept:
    """The chip's reset: whatever `on_watchdog_trigger` is now. False: it raised, and the error is parked."""
    cdef RPWatchdog block = <RPWatchdog> ctx
    try:
        block.on_watchdog_trigger()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _tick_trampoline(void* ctx, double tick_hz) noexcept:
    """The tick changed: the TIMER and SysTick count on it, so they are told (the reference's `_retick`). False: one of them raised, and the error is parked."""
    cdef RPWatchdog block = <RPWatchdog> ctx
    try:
        for name in ("timer", "ppb"):
            consumer = getattr(block.rp2040, name, None)
            if consumer is not None:
                consumer.tick_changed(tick_hz)
        for listener in list(block._tick_listeners):
            listener(tick_hz)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class _Words:
    """The reference's `scratch_data` list as a view of the C++ array: indexable, assignable (the tests poke `scratch_data[4] = MAGIC`) and equal to a list of the same values."""

    cdef uint32_t* _words

    def __len__(self):
        return 8

    def __getitem__(self, index):
        return list(self)[index]

    def __setitem__(self, index, value):
        cdef int i = range(8)[index]
        self._words[i] = <uint32_t> (value & 0xFFFFFFFF)

    def __iter__(self):
        for i in range(8):
            yield self._words[i]

    def __eq__(self, other):
        try:
            return list(self) == list(other)
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return repr(list(self))


cdef class _AlarmView:
    """The watchdog's compare alarm as the reference's `Timer32PeriodicAlarm` shows it (`target`, `enable`): the state lives in the C++ block."""

    cdef RPWatchdog _owner
    cdef Timer32PeriodicAlarm* _alarm

    @property
    def target(self):
        return self._alarm.target()

    @target.setter
    def target(self, value):
        self._alarm.set_target(<int64_t> value)

    @property
    def callback(self):
        """What the alarm runs at its target: REASON = TIMER, then the chip's reset (the tests call it by hand)."""
        return self._owner._fire_timeout

    @property
    def enable(self):
        return bool(self._alarm.enable())

    @enable.setter
    def enable(self, value):
        self._alarm.set_enable(bool(value))


cdef class _TimerView:
    """The watchdog's `Timer32` as the reference shows it."""

    cdef RPWatchdog _owner
    cdef Timer32* _timer

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


cdef class RPWatchdog:
    def __init__(self, rp2040, name):
        cdef WatchdogHost host
        cdef _TimerView timer
        cdef _AlarmView alarm
        cdef _Words words
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native WATCHDOG schedules its countdown on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_watchdog.RPWatchdog with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        self.on_watchdog_trigger = self._default_watchdog_trigger
        self._tick_listeners = []
        host.warn = _warn_trampoline
        host.trigger = _trigger_trampoline
        host.tick_changed = _tick_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(&(<SimulationClock> clock)._clock, host)
        words = _Words.__new__(_Words)
        words._words = self._block.scratch
        self.scratch_data = words
        timer = _TimerView.__new__(_TimerView)
        timer._owner = self
        timer._timer = &self._block.timer
        self.timer = timer
        alarm = _AlarmView.__new__(_AlarmView)
        alarm._owner = self
        alarm._alarm = &self._block.alarm
        self.alarm = alarm

    def __dealloc__(self):
        # Unlink the alarm from the clock before the block goes away.
        self._block.detach()

    def _default_watchdog_trigger(self):
        """The guest asked for a reset (TRIGGER, or a timeout) and nothing was installed over this hook: reset the chip (see the reference)."""
        self.rp2040.enter_reset(from_watchdog=True)
        self.rp2040.leave_reset()

    def _fire_timeout(self):
        """What the alarm runs at its target; the tests call it as `alarm.callback()`."""
        if not self._block.fire_timeout():
            raise_if_pending()

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    def add_tick_listener(self, listener):
        """Calls `listener(tick_hz)` whenever the tick changes (0: stopped), after the TIMER and SysTick have been told. Returns the function that unsubscribes it."""
        self._tick_listeners.append(listener)

        def unsubscribe():
            if listener in self._tick_listeners:
                self._tick_listeners.remove(listener)

        return unsubscribe

    def clk_ref_changed(self, clk_ref):
        """clk_ref is now `clk_ref` Hz (called by `update_clocks`): the tick follows it, and so do the countdown, the TIMER and SysTick."""
        if not self._block.clk_ref_changed(<double> clk_ref):
            raise_if_pending()

    @property
    def tick_hz(self):
        return self._block.tick_hz

    @property
    def _reason(self):
        return self._block.reason

    @_reason.setter
    def _reason(self, value):
        self._block.reason = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _enable(self):
        return bool(self._block.enable)

    @_enable.setter
    def _enable(self, value):
        self._block.enable = bool(value)

    @property
    def _tick_enable(self):
        return bool(self._block.tick_enable)

    @_tick_enable.setter
    def _tick_enable(self, value):
        self._block.tick_enable = bool(value)

    @property
    def _tick_cycles(self):
        return self._block.tick_cycles

    @_tick_cycles.setter
    def _tick_cycles(self, value):
        self._block.tick_cycles = <uint32_t> (value & 0x1FF)

    @property
    def _pause_dbg0(self):
        return bool(self._block.pause_dbg0)

    @_pause_dbg0.setter
    def _pause_dbg0(self, value):
        self._block.pause_dbg0 = bool(value)

    @property
    def _pause_dbg1(self):
        return bool(self._block.pause_dbg1)

    @_pause_dbg1.setter
    def _pause_dbg1(self, value):
        self._block.pause_dbg1 = bool(value)

    @property
    def _pause_jtag(self):
        return bool(self._block.pause_jtag)

    @_pause_jtag.setter
    def _pause_jtag(self, value):
        self._block.pause_jtag = bool(value)

    def reset(self):
        """Back to power-on (a RUN-pin/power-on reset): the reset-cause bookkeeping, CTRL and TICK (see the reference, and 0089 section 1.3)."""
        if not self._block.reset():
            raise_if_pending()

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        if not self._block.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    # --- the native bus protocol -----------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the block's
        own read/write functions and its context. Looked up on the *type* by the bus, so a wrapper that merely forwards attributes (a recorder,
        a profiler) cannot lend its target's fast path and be bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
