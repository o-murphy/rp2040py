# Cython view of core/spi.hpp and core/fifo.hpp (docs/records/0096-cpp-mcu-core.md, Phase 4) and the declaration of the native SPI shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool


from rp2040py.native._clock cimport Alarm, Clock
from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._native_block cimport NativeBlock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "fifo.hpp" namespace "rp2040core":
    cdef cppclass Fifo8 "rp2040core::Fifo<8>":
        uint32_t size() noexcept
        uint32_t count() noexcept
        cppbool empty() noexcept
        cppbool full() noexcept
        uint32_t at(uint32_t index) noexcept
        void push(uint32_t value) noexcept
        uint32_t pull() noexcept
        uint32_t peek() noexcept
        void reset() noexcept

cdef extern from "spi.hpp" namespace "rp2040core":

    ctypedef cppbool (*SpiBitFn)(void* ctx, double* nanos)
    ctypedef cppbool (*SpiTransmitFn)(void* ctx, uint32_t value)

    cdef cppclass SpiBlock:
        uint32_t control0
        uint32_t control1
        uint32_t dma_control
        uint32_t clock_divisor
        uint32_t int_raw
        uint32_t int_enable
        cppbool busy
        Alarm frame_alarm
        cppbool frame_active
        cppbool has_reply
        uint32_t reply
        Fifo8 rx
        Fifo8 tx
        SpiBlock() noexcept
        void detach() noexcept
        int64_t raw_write_value() noexcept
        uint32_t int_status() noexcept
        cppbool enabled() noexcept
        uint32_t data_bits() noexcept
        cppbool power_on() noexcept
        cppbool reset() noexcept
        cppbool check_interrupts() noexcept
        cppbool complete_transmit(uint32_t rx_value) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPSPI(NativeBlock):
    cdef SpiBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object irq
    cdef public object dreq
    cdef public object clock
    cdef object _on_transmit
