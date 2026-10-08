# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 RTC: a Python-facing shell over the C++ `RtcBlock` of `core/rtc.hpp` (docs/records/0096-cpp-mcu-core.md). `peripherals/_rtc.py` is the pure-Python reference, kept as the
oracle (tests/test_rtc_diff.py); `peripherals/rtc.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`). The second is a node
of the chip's C++ clock, so the shell needs the native `SimulationClock`. What stays Python is the edge: the logger and the NVIC line (`rp2040.set_interrupt(IRQ.RTC, level)`, through a trampoline whose
failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in).

The API is the reference's: the register attributes (`clkdiv_m1`, `setup0`, `setup1`, `irq_setup0`, `irq_setup1`, `inte`, `intf`, `force_not_leap_year`, `enable`, the seven counter fields,
`latched_date`), `ctrl`, `running`, `raw_interrupt`, `date`, `time`, `clk_rtc_changed`, `second_nanos`, `check_interrupts`, `reset`, plus `BasePeripheral`'s surface.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.irq import IRQ

__all__ = ("RP2040RTC",)


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RP2040RTC block = <RP2040RTC> ctx
    try:
        if kind == kRtcWarnRead:
            block.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kRtcWarnReadAtomicArea:
            block.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            block.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef cppbool _irq_trampoline(void* ctx, cppbool level) noexcept:
    cdef RP2040RTC block = <RP2040RTC> ctx
    try:
        block.rp2040.set_interrupt(IRQ.RTC, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class RP2040RTC:
    def __init__(self, rp2040, name):
        cdef RtcHost host
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native RTC schedules its second on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_rtc.RP2040RTC with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        host.warn = _warn_trampoline
        host.irq = _irq_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(&(<SimulationClock> clock)._clock, host)

    def __dealloc__(self):
        # Unlink the second from the clock before the block goes away.
        self._block.detach()

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    # --- the reference's attributes, over the C++ state ------------------------------------------

    @property
    def clkdiv_m1(self):
        return self._block.clkdiv_m1

    @clkdiv_m1.setter
    def clkdiv_m1(self, value):
        self._block.clkdiv_m1 = <uint32_t> (value & 0xFFFF)

    @property
    def setup0(self):
        return self._block.setup0

    @setup0.setter
    def setup0(self, value):
        self._block.setup0 = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def setup1(self):
        return self._block.setup1

    @setup1.setter
    def setup1(self, value):
        self._block.setup1 = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def irq_setup0(self):
        return self._block.irq_setup0

    @irq_setup0.setter
    def irq_setup0(self, value):
        self._block.irq_setup0 = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def irq_setup1(self):
        return self._block.irq_setup1

    @irq_setup1.setter
    def irq_setup1(self, value):
        self._block.irq_setup1 = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def inte(self):
        return self._block.inte

    @inte.setter
    def inte(self, value):
        self._block.inte = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def intf(self):
        return self._block.intf

    @intf.setter
    def intf(self, value):
        self._block.intf = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def force_not_leap_year(self):
        return bool(self._block.force_not_leap_year)

    @force_not_leap_year.setter
    def force_not_leap_year(self, value):
        self._block.force_not_leap_year = bool(value)

    @property
    def enable(self):
        return bool(self._block.enable)

    @enable.setter
    def enable(self, value):
        self._block.enable = bool(value)

    @property
    def year(self):
        return self._block.year

    @year.setter
    def year(self, value):
        self._block.year = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def month(self):
        return self._block.month

    @month.setter
    def month(self, value):
        self._block.month = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def day(self):
        return self._block.day

    @day.setter
    def day(self, value):
        self._block.day = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def dotw(self):
        return self._block.dotw

    @dotw.setter
    def dotw(self, value):
        self._block.dotw = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def hour(self):
        return self._block.hour

    @hour.setter
    def hour(self, value):
        self._block.hour = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def minute(self):
        return self._block.minute

    @minute.setter
    def minute(self, value):
        self._block.minute = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def second(self):
        return self._block.second

    @second.setter
    def second(self, value):
        self._block.second = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def latched_date(self):
        return self._block.latched_date

    @latched_date.setter
    def latched_date(self, value):
        self._block.latched_date = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def ctrl(self):
        return self._block.ctrl()

    @ctrl.setter
    def ctrl(self, value):
        if not self._block.set_ctrl(<uint32_t> (value & 0xFFFFFFFF)):
            raise_if_pending()

    @property
    def running(self):
        return bool(self._block.running())

    @property
    def match_ena(self):
        return bool(self._block.match_ena())

    @property
    def raw_interrupt(self):
        return bool(self._block.raw_interrupt())

    @property
    def date(self):
        return self._block.date()

    @property
    def time(self):
        return self._block.time()

    def clk_rtc_changed(self, clk_rtc):
        """clk_rtc is now `clk_rtc` Hz (called by `update_clocks`; 0: the generator is stopped): the second follows it."""
        if not self._block.clk_rtc_changed(<double> clk_rtc):
            raise_if_pending()

    def second_nanos(self):
        return self._block.second_nanos()

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

    def second_elapsed(self):
        """A second has gone by (the clock's alarm runs it; the tests call it by hand)."""
        if not self._block.second_elapsed():
            raise_if_pending()

    def reset(self):
        """`RESETS.RESET_RTC`: every register 0, the counter 0, the divider stopped (see the reference)."""
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
