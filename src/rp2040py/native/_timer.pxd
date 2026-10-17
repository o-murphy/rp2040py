# Cython view of core/timer.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2) and the declaration of the native TIMER
# facade. Header-only C++17, compiled into every extension that cimports it.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

from rp2040py.native._core_host cimport ChipPorts

from rp2040py.native._clock cimport Clock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "timer.hpp" namespace "rp2040core":

    cdef cppclass TimerBlock:
        TimerBlock() noexcept
        void init(Clock* clock, const ChipPorts* ports, uint32_t block, uint32_t irq_line) noexcept
        void detach() noexcept
        uint32_t int_status() noexcept
        int64_t raw_write_value() noexcept
        bint alarm_armed(int index) noexcept
        bint read32(uint32_t offset, uint32_t* out) noexcept
        int write32(uint32_t offset, int64_t value, int64_t raw) noexcept
        bint reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        void tick_changed(double tick_hz) noexcept
        double tick_hz() noexcept
        bint paused() noexcept
        WindowHandler window_handler() noexcept


cdef class RPTimer:
    cdef ChipPorts _ports  # a block made directly (`_own`) reaches the chip through these, trampolines into Python
    cdef TimerBlock* _block  # the chip's (`Mcu` builds and wires it), or `_own` for a block made directly (a stand-in chip, a test)
    cdef TimerBlock _own
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block (the watchdog's tick reaches it in C++)
    cdef public object rp2040
    cdef public object name
    cdef public object clock
