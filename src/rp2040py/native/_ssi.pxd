# Cython view of core/ssi.hpp (docs/records/0096-cpp-mcu-core.md, the flash-path design note) and the declaration of the native SSI shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint8_t, uint32_t
from libcpp cimport bool as cppbool


from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._pin cimport PinBank
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "ssi.hpp" namespace "rp2040core":

    ctypedef cppbool (*SsiCsLowFn)(void* ctx, cppbool* low)

    cdef cppclass SsiBlock:
        SsiBlock() noexcept
        cppbool bind_cs(PinBank* bank, uint32_t slot) noexcept
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
        uint32_t sticky() noexcept
        void set_sticky(uint32_t value) noexcept
        cppbool irq_level() noexcept
        cppbool dreq_tx() noexcept
        cppbool dreq_rx() noexcept
        uint32_t risr() noexcept
        uint32_t isr() noexcept
        void set_write_enabled(cppbool value) noexcept
        void set_ssienr(uint32_t value) noexcept
        void tx_append(uint8_t byte) noexcept
        void rx_push(uint8_t byte) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept


cdef class RPSSI:
    cdef SsiBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public bint _from_mcu
    cdef public object rp2040
    cdef public object name
