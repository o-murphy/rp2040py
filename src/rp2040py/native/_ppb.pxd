# Cython view of core/ppb.hpp (docs/records/0096-cpp-mcu-core.md, the PPB design note) and the declaration of the native PPB shell. Header-only C++17, compiled into every
# extension that cimports it (setup.py builds them all as C++). The `Timer32` declarations are the PWM's (`_pwm.pxd`): it is the same C++ counter.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._cpu cimport Cpu
from rp2040py.native._pwm cimport Timer32, Timer32PeriodicAlarm
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "ppb.hpp" namespace "rp2040core":
    cdef const uint32_t kPpbWarnRead
    cdef const uint32_t kPpbWarnWrite

    ctypedef void (*PpbWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass PpbHost:
        PpbHost() noexcept
        PpbWarnFn warn
        void* ctx

    cdef cppclass PpbBlock:
        Timer32 timer
        Timer32PeriodicAlarm alarm
        cppbool count_flag
        cppbool clk_source
        cppbool int_enable
        uint32_t reload
        PpbBlock() noexcept
        void init(Cpu* cpu, Clock* clock, const PpbHost& host, double clk_sys, uint32_t max_hardware_irq) noexcept
        void detach() noexcept
        void reset() noexcept
        double clk_sys() noexcept
        void clk_sys_changed(double hz) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        int64_t raw_write_value() noexcept
        WindowHandler window_handler() noexcept

cdef class RPPPB:
    cdef PpbBlock _block
    cdef public object rp2040
    cdef public object name
    cdef public object clock
    cdef public object systick_timer
    cdef public object systick_alarm
    cdef object _core  # keeps the core (and so the `Cpu` the block points into) alive
