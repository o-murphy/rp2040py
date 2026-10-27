# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 CLOCKS block and clock tree: a Python-facing shell over the C++ `ClocksBlock` of `core/clocks.hpp` (docs/records/0096-cpp-mcu-core.md, docs/tasks/clocks-port.md).
`peripherals/_clocks.py` is the pure-Python reference, kept as the oracle; `peripherals/clocks.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a register
access never enters Python. The tree itself - the registers, the frequency of each generator, `clk_sys` / `clk_peri` and the frequency counter - is C++. What stays Python is the edge: the logger,
the oscillators' output (the XOSC block's STABLE bit and the chip's `xosc_freq`, the chip's `rosc_freq`) and the four SINKS the tree tells of a change, in the order of the reference's `update_clocks()`:
sys (the chip's `clk_sys`, then SysTick, then every PWM counter, then the host's listeners), peri (the chip's `clk_peri`, then every UART), ref (the watchdog's tick) and rtc (the RTC). Wiring those
to the C++ blocks directly is the step that lets a headless core run the tree without Python (docs/tasks/clocks-port.md). Failures of the Python side are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in. The frequency counter counts against the chip's C++ `Clock`, so the block needs the native `SimulationClock`.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._dma cimport RPDMA
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._pll cimport RPPLL
from rp2040py.native._ppb cimport RPPPB
from rp2040py.native._pwm cimport RPPWM
from rp2040py.native._rosc cimport RPROSC
from rp2040py.native._rtc cimport RP2040RTC
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._watchdog cimport RPWatchdog
from rp2040py.native._window_map cimport WindowHandler
from rp2040py.native._xosc cimport RPXOSC

__all__ = ("RPClocks",)

XOSC_STATUS = 0x04
XOSC_STATUS_STABLE = 1 << 31


cdef cppbool _sys_sink(void* ctx, double hz, double old) noexcept:
    # First of the sys sinks: the chip's own `clk_sys` (the host reads it, and so does a native PWM block's shell), and the flag that ends the running batch.
    cdef RPClocks block = <RPClocks> ctx
    try:
        block.rp2040.clk_sys = hz
        block._sys_dirty = 1
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _listeners_sink(void* ctx, double hz, double old) noexcept:
    # Last of the sys sinks: the host's listeners (a PIO clock divider, say), after SysTick and the PWM counters have followed.
    cdef RPClocks block = <RPClocks> ctx
    try:
        for listener in list(block.rp2040._clock_listeners):
            listener(hz, old)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _peri_sink(void* ctx, double hz, double old) noexcept:
    cdef RPClocks block = <RPClocks> ctx
    try:
        rp2040 = block.rp2040
        rp2040.clk_peri = hz
        for uart in rp2040.uart:
            uart.clk_peri_changed()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class RPClocks(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
        pll_sys = rp2040.pll_sys  # the chip builds the PLLs first: the tree points at their C++ blocks
        pll_usb = rp2040.pll_usb
        clock = rp2040.clock
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus and its notices to the clock tree - and this object is the Python view of it
            self.rp2040 = rp2040
            self.name = name
            self.clock = clock
            self._pll_sys = pll_sys
            self._pll_usb = pll_usb
            self._block = <ClocksBlock*> <size_t> rp2040._native_clocks_address()
            self._ops = register_ops(self._block)
            self.rebind_consumers()
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPClocks is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def rebind_consumers(self):
        """(Re)registers the sinks, in the order of the reference's `update_clocks()`: the chip's `clk_sys`, SysTick, the PWM counters, the host's listeners; the chip's `clk_peri` and the UARTs (their
        baud-rate announcement is a host callback); the watchdog's tick; the RTC. Every consumer is a native block, told directly (core/clock_sinks.hpp). Called once, when the chip has built its last oscillator."""
        rp2040 = self.rp2040
        ppb, pwm, watchdog, rtc, dma = rp2040.ppb, rp2040.pwm, rp2040.watchdog, rp2040.rtc, rp2040.dma
        xosc, rosc = rp2040.xosc, rp2040.rosc
        self._consumers = (ppb, pwm, watchdog, rtc, dma, xosc, rosc, self._pll_sys, self._pll_usb)
        self._block.clear_sinks()
        ok = self._block.add_sys_sink(_sys_sink, <void*> self)
        ok = ok and self._block.add_sys_sink(ppb_sys_sink, <void*> (<RPPPB> ppb)._block)
        # core 1's own SysTick (the chip builds it, the Python layer has no view of it yet): the tree feeds it as it feeds core 0's
        core1_ppb_address = getattr(rp2040, "_native_ppb_address", None)  # (a stand-in chip of a test has no second core)
        if core1_ppb_address is not None:
            ok = ok and self._block.add_sys_sink(ppb_sys_sink, <void*> <size_t> core1_ppb_address(1))
        ok = ok and self._block.add_sys_sink(pwm_sys_sink, <void*> (<RPPWM> pwm)._block)
        # The oscillators are read from their C++ blocks, which tell the tree when they change, and the PLLs take the crystal from the tree: nothing of this goes through Python. The crystal's
        # frequency is the board's (`chip.xosc_freq`, a parameter of the world). Anything else (a recorder, a reference swapped in by an oracle) is read and told through its Python methods.
        self._block.set_crystal_hz(<double> rp2040.xosc_freq)
        if isinstance(xosc, RPXOSC):
            self._block.bind_xosc((<RPXOSC> xosc)._block)
            (<RPXOSC> xosc)._block.bind_stable_listener(clocks_retune, <void*> self._block)
        else:
            self._block.bind_xosc(NULL)
        if isinstance(rosc, RPROSC):
            self._block.bind_rosc((<RPROSC> rosc)._block)
            (<RPROSC> rosc)._block.bind_frequency_listener(clocks_retune_hz, <void*> self._block)
        else:
            self._block.bind_rosc(NULL)
        for pll in (self._pll_sys, self._pll_usb):
            (<RPPLL> pll)._block.bind_reference(clocks_xosc_hz, <void*> self._block)
        if isinstance(dma, RPDMA):
            # the DMA reads clk_sys from the tree and the tree keeps its cycle time current: the Python listener it registered for that is not needed any more
            (<RPDMA> dma)._block.bind_clock(clocks_clk_sys, <void*> self._block, self._block.clk_sys())
            ok = ok and self._block.add_sys_sink(dma_sys_sink, <void*> (<RPDMA> dma)._block)
        ok = ok and self._block.add_sys_sink(_listeners_sink, <void*> self)
        ok = ok and self._block.add_peri_sink(_peri_sink, <void*> self)
        ok = ok and self._block.add_ref_sink(watchdog_ref_sink, <void*> (<RPWatchdog> watchdog)._block)
        ok = ok and self._block.add_rtc_sink(rtc_rtc_sink, <void*> (<RP2040RTC> rtc)._block)
        if not ok:
            raise RuntimeError("the clock tree's sink lists are full")

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
