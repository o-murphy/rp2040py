# Cython view of core/sio.hpp + core/interpolator.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2) and the declaration of
# the native SIO shell. Header-only C++17, compiled into every extension that cimports it.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "sio.hpp" namespace "rp2040core":
    ctypedef uint32_t (*SioInputFn)(void* ctx)
    ctypedef bool (*SioUpdatePinsFn)(void* ctx, uint32_t pin_mask)
    ctypedef void (*SioCyclesFn)(void* ctx, uint32_t cycles)
    ctypedef void (*SioWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef const uint32_t kSioWarnFifo
    cdef const uint32_t kSioWarnReadInvalid
    cdef const uint32_t kSioWarnWriteInvalid
    cdef const uint32_t kSioFailDivideByZero

    cdef cppclass SioHost:
        SioInputFn gpio_in
        SioInputFn qspi_in
        SioUpdatePinsFn update_pins
        SioCyclesFn add_cycles
        SioWarnFn warn
        void* ctx

    cdef cppclass Interpolator:
        int64_t accum0, accum1, base0, base1, base2, ctrl0, ctrl1
        uint32_t result0, result1, result2, smresult0, smresult1
        void update() noexcept
        void writeback() noexcept
        void set_base01(int64_t value) noexcept
        void reset() noexcept

    cdef cppclass SioBlock:
        uint32_t gpio_value, gpio_output_enable, qspi_gpio_value, qspi_gpio_output_enable
        int64_t div_dividend, div_divisor, div_remainder
        double div_quotient
        uint32_t div_csr, spin_lock
        SioBlock() noexcept
        void init(const SioHost& host) noexcept
        void reset() noexcept
        Interpolator& interp(int index) noexcept
        bool update_hardware_divider(bool signed_division) noexcept
        double read_wide(uint32_t offset) noexcept
        uint32_t read32(uint32_t offset) noexcept
        bool write32(uint32_t offset, int64_t value) noexcept
        WindowHandler window_handler() noexcept


cdef class _InterpolatorView:
    cdef RPSIO _owner
    cdef int _index


cdef class RPSIO:
    cdef SioBlock _block
    cdef public object rp2040
    cdef public object name
    cdef object _interp0
    cdef object _interp1
