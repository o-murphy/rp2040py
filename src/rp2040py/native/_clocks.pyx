# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 CLOCKS block and clock tree: a Python-facing shell over the C++ `ClocksBlock` of `core/clocks.hpp` (docs/records/0096-cpp-mcu-core.md, docs/tasks/clocks-port.md).
`peripherals/_clocks.py` is the pure-Python reference, kept as the oracle; `peripherals/clocks.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a register
access never enters Python. The tree itself - the registers, the frequency of each generator, `clk_sys` / `clk_peri` and the frequency counter - is C++. What stays Python is the edge: the logger,
the oscillators' output (the XOSC block's STABLE bit and the chip's `xosc_freq`, the chip's `rosc_freq`) and the board's crystal. The four sink lists the tree tells of a change (sys: SysTick of both cores, the PWM
counters, the DMA; peri: the UARTs; ref: the watchdog's tick; rtc: the RTC) are the `Mcu`'s own (`bind_tree_sinks()`), and the host's observers are the two ports of `ClocksBlock` (`tap` before the sinks, `after_tap`
after them). Failures of the Python side are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in. The frequency counter counts against the chip's C++ `Clock`, so the block needs the native `SimulationClock`.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPClocks",)

cdef class RPClocks(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
        clock = rp2040.clock
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus and its notices to the clock tree - and this object is the Python view of it
            self.rp2040 = rp2040
            self.name = name
            self.clock = clock
            self._block = <ClocksBlock*> <size_t> rp2040._native_clocks_address()
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPClocks is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def reset(self):
        """The CLOCKS registers back to power-on (the clock tree's `clk_sys` / `clk_peri` are `reset_tree()`'s)."""
        self._block.reset()

    def reset_tree(self):
        """The CLOCKS domain was reset (after `reset()`): both PLLs to power-on, clk_sys / clk_peri back to 125 MHz through the sinks."""
        if not self._block.reset_tree():
            raise_if_pending()

    def update(self):
        """Re-derives `clk_sys` / `clk_peri` and retunes what runs from them (the chip's `update_clocks()`)."""
        if not self._block.retune():
            raise_if_pending()
        raise_if_pending()

    def set_crystal_hz(self, hz):
        """The board's crystal (`chip.xosc_freq`): what the crystal block delivers while it is stable."""
        self._block.set_crystal_hz(<double> hz)

    def snapshot(self):
        """The six clock generators' CTRL and DIV words (ref, peri, usb, sys, adc, rtc) and the frequencies derived from them, in one call."""
        cdef ClocksBlock* b = self._block
        state = {"ref_ctrl": b.ref_ctrl, "ref_div": b.ref_div, "peri_ctrl": b.peri_ctrl, "peri_div": b.peri_div, "usb_ctrl": b.usb_ctrl, "usb_div": b.usb_div, "sys_ctrl": b.sys_ctrl, "sys_div": b.sys_div, "adc_ctrl": b.adc_ctrl, "adc_div": b.adc_div, "rtc_ctrl": b.rtc_ctrl, "rtc_div": b.rtc_div}
        state["ref_freq"] = b.ref_freq()
        state["sys_freq"] = b.sys_freq()
        state["peri_freq"] = b.peri_freq()
        state["rtc_freq"] = b.rtc_freq()
        state["usb_freq"] = b.usb_freq()
        state["adc_freq"] = b.adc_freq()
        raise_if_pending()
        return state

    # --- the native bus protocol -----------------------------------------------------------------
