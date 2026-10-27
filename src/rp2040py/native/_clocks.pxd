# Cython view of core/clocks.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native CLOCKS shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._native_block cimport NativeBlock
from rp2040py.native._pll cimport PllBlock
from rp2040py.native._rosc cimport RoscBlock
from rp2040py.native._window_map cimport WindowHandler
from rp2040py.native._xosc cimport XoscBlock

cdef extern from "clocks.hpp" namespace "rp2040core":

    ctypedef double (*ClockHzFn)(void* ctx)
    ctypedef cppbool (*ClockSinkFn)(void* ctx, double hz, double old)
    ctypedef cppbool (*ClockTap)(void* ctx, uint32_t domain, double hz, double old)

    cdef cppclass ClocksBlock:
        ClocksBlock() noexcept
        ClockTap tap
        void* tap_ctx
        ClockTap after_tap
        void* after_tap_ctx
        void detach() noexcept
        void clear_sinks() noexcept
        void bind_xosc(const XoscBlock* block) noexcept
        void bind_rosc(const RoscBlock* block) noexcept
        void set_crystal_hz(double hz) noexcept
        int64_t raw_write_value() noexcept
        cppbool add_sys_sink(ClockSinkFn fn, void* ctx) noexcept
        cppbool add_peri_sink(ClockSinkFn fn, void* ctx) noexcept
        cppbool add_ref_sink(ClockSinkFn fn, void* ctx) noexcept
        cppbool add_rtc_sink(ClockSinkFn fn, void* ctx) noexcept
        double clk_sys() noexcept
        double clk_peri() noexcept
        void force_clk_peri(double hz) noexcept
        void force_clk_sys(double hz) noexcept
        void reset() noexcept
        cppbool reset_tree() noexcept
        double ref_freq() noexcept
        double sys_freq() noexcept
        double peri_freq() noexcept
        double rtc_freq() noexcept
        double usb_freq() noexcept
        double adc_freq() noexcept
        double frequency_of(uint32_t source) noexcept
        cppbool retune() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept
        uint32_t ref_ctrl
        uint32_t ref_div
        uint32_t peri_ctrl
        uint32_t peri_div
        uint32_t usb_ctrl
        uint32_t usb_div
        uint32_t sys_ctrl
        uint32_t sys_div
        uint32_t adc_ctrl
        uint32_t adc_div
        uint32_t rtc_ctrl
        uint32_t rtc_div

cdef extern from "clock_sinks.hpp" namespace "rp2040core":
    cppbool ppb_sys_sink(void* ctx, double hz, double old) noexcept
    cppbool pwm_sys_sink(void* ctx, double hz, double old) noexcept
    cppbool dma_sys_sink(void* ctx, double hz, double old) noexcept
    double clocks_clk_sys(void* ctx) noexcept
    double clocks_xosc_hz(void* ctx) noexcept
    cppbool clocks_retune(void* ctx) noexcept
    cppbool clocks_retune_hz(void* ctx, double hz) noexcept
    cppbool watchdog_ref_sink(void* ctx, double hz, double old) noexcept
    cppbool rtc_rtc_sink(void* ctx, double hz, double old) noexcept

cdef class RPClocks(NativeBlock):
    cdef ClocksBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object clock
    cdef object _pll_sys
    cdef object _pll_usb
    cdef object _consumers  # the blocks the direct sinks point into: kept alive while they are bound
    cdef public unsigned char _sys_dirty  # raised whenever clk_sys changes: the batch loop ends its batch on it (core/batch.hpp `clk_changed`)
