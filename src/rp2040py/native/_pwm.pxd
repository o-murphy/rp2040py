# Cython view of core/pwm.hpp and core/timer32.hpp (docs/records/0096-cpp-mcu-core.md, Phase 4) and the declaration of the native PWM shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "timer32.hpp" namespace "rp2040core":
    cdef cppclass Timer32:
        Clock* clock() noexcept
        void reset() noexcept
        void set(int64_t value, cppbool zig_zag_down) noexcept
        void advance(int64_t delta) noexcept
        int64_t raw_counter() noexcept
        uint32_t counter() noexcept
        int64_t top() noexcept
        void set_top(int64_t value) noexcept
        double frequency() noexcept
        void set_frequency(double value) noexcept
        double prescaler() noexcept
        void set_prescaler(double value) noexcept
        double to_nanos(int64_t cycles) noexcept
        cppbool enable() noexcept
        void set_enable(cppbool value) noexcept
        uint32_t mode_raw() noexcept
        void set_mode_raw(uint32_t value) noexcept
        int64_t base_value() noexcept
        double base_nanos() noexcept

    cdef cppclass Timer32PeriodicAlarm:
        cppbool enable() noexcept
        void set_enable(cppbool value) noexcept
        int64_t target() noexcept
        void set_target(int64_t value) noexcept
        cppbool scheduled() noexcept

cdef extern from "pwm.hpp" namespace "rp2040core":
    cdef const uint32_t kPwmWarnRead
    cdef const uint32_t kPwmWarnReadAtomicArea
    cdef const uint32_t kPwmWarnWrite

    ctypedef cppbool (*PwmIrqFn)(void* ctx, cppbool level)
    ctypedef cppbool (*PwmDreqFn)(void* ctx, uint32_t channel)
    ctypedef cppbool (*PwmPinChangedFn)(void* ctx, uint32_t pin)
    ctypedef cppbool (*PwmPinReadFn)(void* ctx, uint32_t pin, cppbool* level)
    ctypedef void (*PwmWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass PwmHost:
        PwmHost() noexcept
        PwmIrqFn irq
        PwmDreqFn dreq
        PwmPinChangedFn pin_changed
        PwmPinReadFn pin_read
        PwmWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass PwmChannel:
        Timer32 timer
        Timer32PeriodicAlarm alarm_a
        Timer32PeriodicAlarm alarm_b
        Timer32PeriodicAlarm alarm_bottom
        uint32_t csr
        uint32_t div
        uint32_t cc
        uint32_t top
        cppbool last_b_value
        cppbool counting_up
        cppbool cc_updated
        cppbool top_updated
        double tick_counter
        int64_t pin_a1
        int64_t pin_b1
        int64_t pin_a2
        int64_t pin_b2
        uint32_t index
        uint32_t div_mode_raw() noexcept
        void set_div_mode_raw(uint32_t value) noexcept
        uint32_t read_register(uint32_t offset) noexcept
        cppbool write_register(uint32_t offset, uint32_t value) noexcept
        cppbool reset() noexcept
        cppbool set_a(cppbool value) noexcept
        cppbool set_b(cppbool value) noexcept
        cppbool gpio_b_value(cppbool* level) noexcept
        cppbool gpio_b_changed() noexcept
        cppbool update_enable() noexcept
        uint32_t en() noexcept
        cppbool set_en(cppbool value) noexcept
        void update_double_buffered() noexcept

    cdef cppclass PwmBlock:
        PwmChannel channels[8]
        uint32_t int_raw
        uint32_t int_enable
        uint32_t int_force
        uint32_t gpio_value
        uint32_t gpio_direction
        PwmBlock() noexcept
        void init(Clock* clock, const PwmHost& host, double clock_freq) noexcept
        void detach() noexcept
        Clock* clock() noexcept
        double clock_freq() noexcept
        void set_clock_freq(double freq) noexcept
        int64_t raw_write_value() noexcept
        uint32_t int_status() noexcept
        cppbool check_interrupts() noexcept
        cppbool channel_interrupt(uint32_t index) noexcept
        cppbool gpio_set(int64_t index, cppbool value) noexcept
        cppbool gpio_set_dir(int64_t index, cppbool output) noexcept
        cppbool gpio_read(int64_t index, cppbool* level) noexcept
        cppbool gpio_on_input(uint32_t index) noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPPWM:
    cdef PwmBlock _block
    cdef public object rp2040
    cdef public object name
    cdef public object clock
    cdef public object channels
