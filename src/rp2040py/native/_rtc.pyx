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

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.irq import IRQ

__all__ = ("RP2040RTC",)


cdef class RP2040RTC(NativeBlock):
    def __init__(self, rp2040, name):
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
        address = getattr(rp2040, "_native_rtc_address", None)
        self._from_mcu = address is not None
        if address is not None:
            # the chip's C++ `Mcu` has built the block already - on its clock, with its window on the bus and its line on the CPU - and this object is the Python view of it
            self._block = <RtcBlock*> <size_t> address()
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RP2040RTC is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    @property
    def running(self):
        """The counter is counting (the chip's clock tree and the clocks oracle read it)."""
        return bool(self._block.running())

    def snapshot(self):
        """The block's state in one call: the stored registers and counter fields (public fields of the C++ block) and what is derived from them."""
        cdef RtcBlock* b = self._block
        return {
            "clkdiv_m1": b.clkdiv_m1,
            "setup0": b.setup0,
            "setup1": b.setup1,
            "irq_setup0": b.irq_setup0,
            "irq_setup1": b.irq_setup1,
            "inte": b.inte,
            "intf": b.intf,
            "force_not_leap_year": bool(b.force_not_leap_year),
            "enable": bool(b.enable),
            "year": b.year,
            "month": b.month,
            "day": b.day,
            "dotw": b.dotw,
            "hour": b.hour,
            "minute": b.minute,
            "second": b.second,
            "latched_date": b.latched_date,
            "ctrl": b.ctrl(),
            "running": bool(b.running()),
            "match_ena": bool(b.match_ena()),
            "raw_interrupt": bool(b.raw_interrupt()),
            "date": b.date(),
            "time": b.time(),
        }

    def clk_rtc_changed(self, clk_rtc):
        """clk_rtc is now `clk_rtc` Hz (called by `update_clocks`; 0: the generator is stopped): the second follows it."""
        if not self._block.clk_rtc_changed(<double> clk_rtc):
            raise_if_pending()

    def second_nanos(self):
        return self._block.second_nanos()

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

    def reset(self):
        """`RESETS.RESET_RTC`: every register 0, the counter 0, the divider stopped (see the reference)."""
        if not self._block.reset():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------
