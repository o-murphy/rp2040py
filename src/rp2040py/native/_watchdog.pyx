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

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._views cimport Timer32View
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPWatchdog",)


cdef cppbool _tick_tap(void* ctx, double tick_hz) noexcept:
    # The listeners of `add_tick_listener`: they hear the tick before the TIMER and SysTick follow it.
    cdef RPWatchdog watchdog = <RPWatchdog> ctx
    try:
        for listener in list(watchdog._tick_listeners):
            listener(tick_hz)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class RPWatchdog(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
        cdef Timer32View timer
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
        self._on_watchdog_trigger = self._default_watchdog_trigger
        self._tick_listeners = []
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already and wired it (its window, its lines, the clock tree, the watchdog's tick) - this object is the Python view of it
            self._block = <WatchdogBlock*> <size_t> rp2040._native_watchdog_address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPWatchdog is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
        timer = Timer32View.__new__(Timer32View)
        timer._owner = self
        timer._timer = &self._block.timer
        self.timer = timer

    @property
    def on_watchdog_trigger(self):
        return self._on_watchdog_trigger

    @on_watchdog_trigger.setter
    def on_watchdog_trigger(self, hook):
        self._on_watchdog_trigger = hook
        self.rp2040._reset_hooks_changed()

    def _world_trigger(self):
        """The watchdog fired (or was triggered): the chip's reset, whatever `on_watchdog_trigger` is now."""
        self._on_watchdog_trigger()

    def _default_watchdog_trigger(self):
        """The guest asked for a reset (TRIGGER, or a timeout) and nothing was installed over this hook: reset the chip (see the reference)."""
        self.rp2040.enter_reset(from_watchdog=True)
        self.rp2040.leave_reset()

    def _fire_timeout(self):
        """What the alarm runs at its target; the tests call it as `alarm.callback()`."""
        if not self._block.fire_timeout():
            raise_if_pending()

    def add_tick_listener(self, listener):
        """Calls `listener(tick_hz)` whenever the tick changes (0: stopped), after the TIMER and SysTick have been told. Returns the function that unsubscribes it."""
        self._tick_listeners.append(listener)
        self._block.tick_tap = _tick_tap
        self._block.tick_tap_ctx = <void*> self

        def unsubscribe():
            if listener in self._tick_listeners:
                self._tick_listeners.remove(listener)
            if not self._tick_listeners:
                self._block.tick_tap = NULL

        return unsubscribe

    def clk_ref_changed(self, clk_ref):
        """clk_ref is now `clk_ref` Hz (called by `update_clocks`): the tick follows it, and so do the countdown, the TIMER and SysTick."""
        if not self._block.clk_ref_changed(<double> clk_ref):
            raise_if_pending()

    def fire_timeout(self):
        """What the countdown does at zero without a feed: REASON = TIMER, then the chip's reset. Tests call it by hand."""
        self._fire_timeout()

    def snapshot(self):
        """The block's state in one call: the registers and flags, the eight scratch words, the tick, and the countdown (its enables, target and rate)."""
        cdef WatchdogBlock* b = self._block
        return {
            "reason": b.reason,
            "enable": bool(b.enable),
            "tick_enable": bool(b.tick_enable),
            "tick_cycles": b.tick_cycles,
            "tick_hz": b.tick_hz,
            "pause_dbg0": bool(b.pause_dbg0),
            "pause_dbg1": bool(b.pause_dbg1),
            "pause_jtag": bool(b.pause_jtag),
            "scratch_data": tuple([b.scratch[i] for i in range(8)]),
            "timer_enable": bool(b.timer.enable()),
            "alarm_enable": bool(b.alarm.enable()),
            "alarm_target": b.alarm.target(),
            "timer_frequency": b.timer.frequency(),
        }

    def reset(self):
        """Back to power-on (a RUN-pin/power-on reset): the reset-cause bookkeeping, CTRL and TICK (see the reference, and 0089 section 1.3)."""
        if not self._block.reset():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------
