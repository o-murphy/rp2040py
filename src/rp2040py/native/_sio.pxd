# Cython view of core/sio.hpp + core/interpolator.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2) and the declaration of
# the native SIO shell. Header-only C++17, compiled into every extension that cimports it.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "sio.hpp" namespace "rp2040core":
    ctypedef uint32_t (*SioInputFn)(void* ctx)
    ctypedef bool (*SioUpdatePinsFn)(void* ctx, uint32_t pin_mask, bool qspi)
    ctypedef void (*SioCyclesFn)(void* ctx, uint32_t cycles)

    cdef const uint32_t kSioWarnFifo
    cdef const uint32_t kSioWarnReadInvalid
    cdef const uint32_t kSioWarnWriteInvalid
    cdef const uint32_t kSioFailDivideByZero

    cdef cppclass SioHost:
        SioInputFn gpio_in
        SioInputFn qspi_in
        SioUpdatePinsFn update_pins
        SioCyclesFn add_cycles
        void* ctx

    cdef cppclass Interpolator:
        int64_t accum0, accum1, base0, base1, base2, ctrl0, ctrl1
        uint32_t result0, result1, result2, smresult0, smresult1
        void update() noexcept
        void writeback() noexcept
        void set_base01(int64_t value) noexcept
        void reset() noexcept

    cdef cppclass SioCoreState:
        int64_t div_dividend, div_divisor, div_remainder
        double div_quotient
        uint32_t div_csr

    cdef cppclass SioBlock:
        uint32_t gpio_value, gpio_output_enable, qspi_gpio_value, qspi_gpio_output_enable
        uint32_t spin_lock
        SioCoreState& core(int index) noexcept
        SioBlock() noexcept
        void init(const ChipPorts* ports, uint32_t block, const SioHost& host) noexcept
        void reset() noexcept
        Interpolator& interp(int index) noexcept
        bool update_hardware_divider(bool signed_division) noexcept
        double read_wide(uint32_t offset) noexcept
        uint32_t read32(uint32_t offset) noexcept
        bool write32(uint32_t offset, int64_t value) noexcept
        WindowHandler window_handler() noexcept


cdef class RPSIO:
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block
    cdef SioBlock* _block  # the chip's (`Mcu` builds it), or `_own` for a block made directly (a stand-in chip, a test)
    cdef SioBlock _own
    cdef ChipPorts _ports  # a block made directly (`_own`) reaches the chip through these
    cdef public object rp2040
    cdef public object name
