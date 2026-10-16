# Cython view of core/ppb.hpp (docs/records/0096-cpp-mcu-core.md, the PPB design note) and the declaration of the native PPB shell. Header-only C++17, compiled into every
# extension that cimports it (setup.py builds them all as C++). The `Timer32` declarations are the PWM's (`_pwm.pxd`): it is the same C++ counter.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._clock cimport Clock
from rp2040py.native._cpu cimport Cpu
from rp2040py.native._pwm cimport Timer32, Timer32PeriodicAlarm
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "ppb.hpp" namespace "rp2040core":
    cdef const uint32_t kPpbWarnRead
    cdef const uint32_t kPpbWarnWrite


    ctypedef void (*PpbSystemResetFn)(void* ctx)

    cdef cppclass PpbHost:
        PpbHost() noexcept
        PpbSystemResetFn system_reset_request
        void* ctx

    cdef cppclass PpbBlock:
        Timer32 timer
        Timer32PeriodicAlarm alarm
        cppbool count_flag
        cppbool clk_source
        cppbool int_enable
        cppbool systick_enable
        uint32_t reload
        uint32_t scr
        uint32_t mpu_ctrl
        uint32_t mpu_rnr
        uint32_t mpu_rbar[8]
        uint32_t mpu_rasr[8]
        PpbBlock() noexcept
        void init(Cpu* cpu, Clock* clock, const ChipPorts* ports, uint32_t block, const PpbHost& host, double clk_sys, uint32_t max_hardware_irq) noexcept
        void detach() noexcept
        void reset() noexcept
        double clk_sys() noexcept
        double tick_hz() noexcept
        void tick_changed(double hz) noexcept
        void clk_sys_changed(double hz) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        int64_t raw_write_value() noexcept
        WindowHandler window_handler() noexcept

cdef class RPPPB:
    cdef PpbBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block (the watchdog's tick reaches it in C++)
    cdef public object rp2040
    cdef public object name
    cdef public object clock
    cdef public object systick_timer
    cdef public object systick_alarm
    cdef public object on_system_reset_request
    cdef object _core  # keeps the core (and so the `Cpu` the block points into) alive
