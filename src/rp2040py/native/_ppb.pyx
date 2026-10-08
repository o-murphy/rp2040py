# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 PPB (the Cortex-M0's private peripheral bus: SysTick, NVIC, SCB registers): a Python-facing shell over the C++ `PpbBlock` of `core/ppb.hpp`
(docs/records/0096-cpp-mcu-core.md, the PPB design note). `peripherals/_ppb.py` is the pure-Python reference, kept as the oracle (tests/test_ppb_diff.py); `peripherals/ppb.py` is the
facade that picks between them, as for the other native ports.

The PPB is not a peripheral-table window (its range is 0xE000E000), so the chip learns about this block through its `ppb` property: when the object assigned has a `_native_window`
on its type, the bus calls the block's C++ functions directly - a NVIC enable or a SysTick read never touches Python on the access path. The block works on the C++ `Cpu` of the
chip's native core (the pending and enabled words, the priority bitmaps, VTOR are the CPU's own fields), so it needs the native core, and SysTick's alarm is a node of the chip's C++
clock, so it needs the native `SimulationClock` as well - a running SysTick costs no Python. The only thing that stays Python is the logger (the warning for an offset that is not a
register), reached through a trampoline whose failures are parked in the shared slot of `_pending.pyx`.

The API is the reference's: `systick_count_flag`, `systick_clk_source`, `systick_int_enable`, `systick_enable`, `systick_reload`, `systick_timer` (a view of the C++ `Timer32` with the reference's
attributes: `frequency` is what `update_clocks` retunes), `systick_alarm`, `reset`, plus `BasePeripheral`'s surface.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._cpu cimport Cpu
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.irq import MAX_HARDWARE_IRQ
from rp2040py.utils.timer32 import TimerMode

__all__ = ("RPPPB",)


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPPPB ppb = <RPPPB> ctx
    try:
        if kind == kPpbWarnRead:
            ppb.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        else:
            ppb.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class _AlarmView:
    """SysTick's compare alarm as the reference's `Timer32PeriodicAlarm` shows it (`target`, `enable`): the state lives in the C++ block."""

    cdef RPPPB _owner
    cdef Timer32PeriodicAlarm* _alarm

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


cdef class _TimerView:
    """SysTick's `Timer32` as the reference shows it."""

    cdef RPPPB _owner
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


cdef class RPPPB:
    def __init__(self, rp2040, name):
        cdef PpbHost host
        cdef _TimerView timer
        cdef _AlarmView alarm
        cdef Cpu* cpu
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native PPB schedules SysTick's alarm on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_ppb.RPPPB with it)"
            )
        native_cpu_address = getattr(rp2040.core, "_native_cpu_address", None)
        if native_cpu_address is None:
            raise TypeError(
                "the native PPB works on the native core's C++ CPU state; "
                f"{type(rp2040).__name__} with {type(rp2040.core).__name__} is not that "
                "(use the pure-Python peripherals/_ppb.RPPPB with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        self._core = rp2040.core
        cpu = <Cpu*> (<size_t> native_cpu_address())
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        self._block.init(cpu, &(<SimulationClock> clock)._clock, host, <double> rp2040.clk_sys, <uint32_t> int(MAX_HARDWARE_IRQ))
        timer = _TimerView.__new__(_TimerView)
        timer._owner = self
        timer._timer = &self._block.timer
        self.systick_timer = timer
        alarm = _AlarmView.__new__(_AlarmView)
        alarm._owner = self
        alarm._alarm = &self._block.alarm
        self.systick_alarm = alarm

    def __dealloc__(self):
        # Safe whichever of this block and its clock the collector frees first: the C++ clock unlinks its alarms when it dies.
        self._block.detach()

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def systick_count_flag(self):
        return bool(self._block.count_flag)

    @systick_count_flag.setter
    def systick_count_flag(self, value):
        self._block.count_flag = bool(value)

    @property
    def systick_clk_source(self):
        return bool(self._block.clk_source)

    @systick_clk_source.setter
    def systick_clk_source(self, value):
        self._block.clk_source = bool(value)

    @property
    def systick_int_enable(self):
        return bool(self._block.int_enable)

    @systick_int_enable.setter
    def systick_int_enable(self, value):
        self._block.int_enable = bool(value)

    @property
    def systick_reload(self):
        return self._block.reload

    @systick_reload.setter
    def systick_reload(self, value):
        self._block.reload = <uint32_t> (<int64_t> value)

    # --- the reference's methods ------------------------------------------------------------------

    @property
    def systick_enable(self):
        return bool(self._block.systick_enable)

    @systick_enable.setter
    def systick_enable(self, value):
        self._block.systick_enable = bool(value)

    def tick_changed(self, tick_hz):
        """The watchdog's tick is now `tick_hz` (0: stopped): SysTick follows it when its CLKSOURCE says reference clock."""
        self._block.tick_changed(<double> tick_hz)

    @property
    def tick_hz(self):
        return self._block.tick_hz()

    def clk_sys_changed(self, clk_sys):
        """clk_sys is now `clk_sys` (called by `update_clocks`): SysTick follows it when its CLKSOURCE says processor clock."""
        self._block.clk_sys_changed(<double> clk_sys)

    @property
    def clk_sys(self):
        return self._block.clk_sys()

    def reset(self):
        """SysTick back to stopped, reload and counter 0xFFFFFF (0089 Phase 5)."""
        self._block.reset()

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        self._block.write(<uint32_t> offset, <int64_t> value)
        raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type)
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
        """What `RP2040` registers for the PPB range instead of calling this object: the addresses of the block's own read/write functions and
        its context. Looked up on the *type* by the bus, so a recorder that forwards attributes is not bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
