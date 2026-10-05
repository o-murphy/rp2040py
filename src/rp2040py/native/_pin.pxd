# Cython view of core/pin.hpp (docs/records/0096-cpp-mcu-core.md, Phase 3): the pin level function and state.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport uint32_t
from libcpp cimport bool

cdef extern from "pin.hpp" namespace "rp2040core":
    cdef const uint32_t kSrcSioOe
    cdef const uint32_t kSrcSioValue
    cdef const uint32_t kSrcPio0Oe
    cdef const uint32_t kSrcPio0Value
    cdef const uint32_t kSrcPio1Oe
    cdef const uint32_t kSrcPio1Value
    cdef const uint32_t kSrcPwmDirection
    cdef const uint32_t kSrcPwmValue

    ctypedef bool (*PinSourceFn)(void* ctx, uint32_t source, uint32_t* out)
    ctypedef bool (*PinChangeFn)(void* ctx, uint32_t pin, int new_state, int old_state)
    ctypedef bool (*PinIoIrqFn)(void* ctx)
    ctypedef bool (*PinInputFn)(void* ctx, uint32_t pin, bool function_is_pwm)

    cdef cppclass PinHost:
        PinHost() noexcept
        PinSourceFn source
        PinChangeFn on_change
        PinIoIrqFn io_interrupt
        PinInputFn input_changed
        void* ctx

    cdef cppclass Pin:
        uint32_t index
        uint32_t ctrl
        uint32_t pad_value
        uint32_t irq_enable_mask
        uint32_t irq_force_mask
        uint32_t irq_status
        int last_state
        bool raw_input_value
        bool driven
        bool always_output_enabled

    bool apply_override(bool value, uint32_t override_type) noexcept

    cdef cppclass PinBank:
        PinBank() noexcept
        bool init(uint32_t count, const PinHost& host, const bool* always_output_enabled, uint32_t first_index) noexcept
        Pin* pin_ptr(uint32_t i) noexcept
        void bind_source(uint32_t source, const uint32_t* direct) noexcept
        bool add_direct_listener(uint32_t i, PinChangeFn fn, void* ctx) noexcept
        bool remove_direct_listener(uint32_t i, PinChangeFn fn, void* ctx) noexcept
        bool raw_output_enable(uint32_t i, uint32_t fsel, bool* out) noexcept
        bool raw_output_value(uint32_t i, uint32_t fsel, bool* out) noexcept
        bool eff_raw_input(uint32_t i) noexcept
        bool state_code(uint32_t i, int* out) noexcept
        bool raw_interrupt(uint32_t i) noexcept
        bool irq_value(uint32_t i) noexcept
        bool input_value(uint32_t i) noexcept
        bool status(uint32_t i, uint32_t* out) noexcept
        bool check_for_updates(uint32_t i) noexcept
        bool set_input_value(uint32_t i, bool value) noexcept
        bool release_input(uint32_t i) noexcept
        bool refresh_input(uint32_t i) noexcept
        bool apply_input_value(uint32_t i, bool value) noexcept
        bool update_irq_value(uint32_t i, uint32_t value) noexcept
        bool reset(uint32_t i, bool io, bool pads) noexcept
