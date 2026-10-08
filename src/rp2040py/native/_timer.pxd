# Cython view of core/timer.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2) and the declaration of the native TIMER
# facade. Header-only C++17, compiled into every extension that cimports it.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

from rp2040py.native._clock cimport Clock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "timer.hpp" namespace "rp2040core":
    ctypedef bool (*TimerIrqFn)(void* ctx, uint32_t line, bool level)
    ctypedef void (*TimerWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef const uint32_t kTimerWarnRead
    cdef const uint32_t kTimerWarnReadAtomicArea
    cdef const uint32_t kTimerWarnWrite

    cdef cppclass TimerHost:
        TimerIrqFn irq
        TimerWarnFn warn
        void* ctx
        uint32_t lines[4]

    cdef cppclass TimerBlock:
        TimerBlock() noexcept
        void init(Clock* clock, const TimerHost& host) noexcept
        void detach() noexcept
        uint32_t int_status() noexcept
        int64_t raw_write_value() noexcept
        bint alarm_armed(int index) noexcept
        bint read32(uint32_t offset, uint32_t* out) noexcept
        int write32(uint32_t offset, int64_t value, int64_t raw) noexcept
        bint reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        void write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        void tick_changed(double tick_hz) noexcept
        double tick_hz() noexcept
        bint paused() noexcept
        WindowHandler window_handler() noexcept


cdef class RPTimer:
    cdef TimerBlock _block
    cdef public object rp2040
    cdef public object name
    cdef public object clock
