# Cython view of core/ssi.hpp (docs/records/0096-cpp-mcu-core.md, the flash-path design note) and the declaration of the native SSI shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint8_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pin cimport PinBank
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "ssi.hpp" namespace "rp2040core":
    cdef const uint32_t kSsiWarnRead
    cdef const uint32_t kSsiWarnReadAtomicArea
    cdef const uint32_t kSsiWarnWrite

    ctypedef void (*SsiWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)
    ctypedef cppbool (*SsiCsLowFn)(void* ctx, cppbool* low)

    cdef cppclass SsiHost:
        SsiHost() noexcept
        SsiWarnFn warn
        SsiCsLowFn cs_low
        void* ctx

    cdef cppclass SsiBlock:
        SsiBlock() noexcept
        void init(uint8_t* flash, uint32_t flash_size, const SsiHost& host) noexcept
        cppbool bind_cs(PinBank* bank) noexcept
        void detach() noexcept
        void on_cs_change(cppbool low) noexcept
        cppbool reset() noexcept
        uint32_t ssienr() noexcept
        uint32_t txflr() noexcept
        uint32_t stored(uint32_t index) noexcept
        cppbool write_enabled() noexcept
        cppbool cs_asserted() noexcept
        uint32_t rx_count() noexcept
        uint8_t rx_at(uint32_t i) noexcept
        uint32_t tx_length() noexcept
        uint8_t tx_at(uint32_t i) noexcept
        int64_t raw_write_value() noexcept
        void set_write_enabled(cppbool value) noexcept
        void set_ssienr(uint32_t value) noexcept
        void tx_append(uint8_t byte) noexcept
        void rx_push(uint8_t byte) noexcept
        uint32_t read(uint32_t offset) noexcept
        void write(uint32_t offset, int64_t value) noexcept
        void write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept


cdef class RPSSI:
    cdef SsiBlock _block
    cdef object _cs_pin
    cdef bint _cs_native
    cdef bint _cs_listening
    cdef public object rp2040
    cdef public object name
