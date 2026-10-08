# Cython view of core/watchdog.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native WATCHDOG shell. Header-only C++17, compiled into every extension that cimports it
# (setup.py builds them all as C++). The `Timer32` declarations are the PWM's (`_pwm.pxd`): it is the same C++ counter.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._pwm cimport Timer32, Timer32PeriodicAlarm
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "watchdog.hpp" namespace "rp2040core":
    cdef const uint32_t kWatchdogWarnRead
    cdef const uint32_t kWatchdogWarnReadAtomicArea
    cdef const uint32_t kWatchdogWarnWrite

    ctypedef void (*WatchdogWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)
    ctypedef cppbool (*WatchdogTriggerFn)(void* ctx)

    cdef cppclass WatchdogHost:
        WatchdogHost() noexcept
        WatchdogWarnFn warn
        WatchdogTriggerFn trigger
        void* ctx
        const int* failed

    cdef cppclass WatchdogBlock:
        Timer32 timer
        Timer32PeriodicAlarm alarm
        uint32_t scratch[8]
        uint32_t reason
        uint32_t tick_cycles
        cppbool enable
        cppbool tick_enable
        cppbool pause_dbg0
        cppbool pause_dbg1
        cppbool pause_jtag
        WatchdogBlock() noexcept
        void init(Clock* clock, const WatchdogHost& host) noexcept
        void detach() noexcept
        int64_t raw_write_value() noexcept
        cppbool fire_timeout() noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPWatchdog:
    cdef WatchdogBlock _block
    cdef public object rp2040
    cdef public object name
    cdef public object clock
    cdef public object on_watchdog_trigger
    cdef public object scratch_data
    cdef public object timer
    cdef public object alarm
