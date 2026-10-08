# Cython view of core/rtc.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native RTC shell. Header-only C++17, compiled into every extension that cimports it
# (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "rtc.hpp" namespace "rp2040core":
    cdef const uint32_t kRtcWarnRead
    cdef const uint32_t kRtcWarnReadAtomicArea
    cdef const uint32_t kRtcWarnWrite

    ctypedef void (*RtcWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)
    ctypedef cppbool (*RtcIrqFn)(void* ctx, cppbool level)

    cdef cppclass RtcHost:
        RtcHost() noexcept
        RtcWarnFn warn
        RtcIrqFn irq
        void* ctx
        const int* failed

    cdef cppclass RtcBlock:
        uint32_t clkdiv_m1
        uint32_t setup0
        uint32_t setup1
        uint32_t irq_setup0
        uint32_t irq_setup1
        uint32_t inte
        uint32_t intf
        cppbool force_not_leap_year
        cppbool enable
        uint32_t year
        uint32_t month
        uint32_t day
        uint32_t dotw
        uint32_t hour
        uint32_t minute
        uint32_t second
        uint32_t latched_date
        RtcBlock() noexcept
        void init(Clock* clock, const RtcHost& host) noexcept
        void detach() noexcept
        int64_t raw_write_value() noexcept
        double clk_rtc() noexcept
        cppbool running() noexcept
        cppbool match_ena() noexcept
        uint32_t ctrl() noexcept
        cppbool set_ctrl(uint32_t value) noexcept
        uint32_t date() noexcept
        uint32_t time() noexcept
        cppbool raw_interrupt() noexcept
        cppbool clk_rtc_changed(double hz) noexcept
        double second_nanos() noexcept
        cppbool check_interrupts() noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        cppbool second_elapsed() noexcept
        WindowHandler window_handler() noexcept

cdef class RP2040RTC:
    cdef RtcBlock _block
    cdef public object rp2040
    cdef public object name
    cdef public object clock
