# Cython view of core/i2c.hpp and core/fifo.hpp (docs/records/0096-cpp-mcu-core.md, Phase 4) and the declaration of the native I2C shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool


from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "fifo.hpp" namespace "rp2040core":
    cdef cppclass Fifo16 "rp2040core::Fifo<16>":
        uint32_t size() noexcept
        uint32_t count() noexcept
        cppbool empty() noexcept
        cppbool full() noexcept
        uint32_t at(uint32_t index) noexcept
        void push(uint32_t value) noexcept
        uint32_t pull() noexcept
        uint32_t peek() noexcept
        void reset() noexcept

cdef extern from "i2c.hpp" namespace "rp2040core":

    ctypedef cppbool (*I2cStartFn)(void* ctx, cppbool repeated)
    ctypedef cppbool (*I2cConnectFn)(void* ctx, uint32_t address, uint32_t mode)
    ctypedef cppbool (*I2cWriteFn)(void* ctx, uint32_t value)
    ctypedef cppbool (*I2cReadFn)(void* ctx, cppbool ack)
    ctypedef cppbool (*I2cStopFn)(void* ctx)

    cdef cppclass I2cBlock:
        uint32_t state
        cppbool busy
        cppbool stop
        cppbool pending_restart
        cppbool abort_pending
        cppbool first_byte
        uint32_t enable
        uint32_t rx_threshold
        uint32_t tx_threshold
        uint32_t control
        uint32_t ss_clock_high
        uint32_t ss_clock_low
        uint32_t fs_clock_high
        uint32_t fs_clock_low
        uint32_t target_address
        uint32_t slave_address
        uint32_t abort_source
        uint32_t int_raw
        uint32_t int_enable
        uint32_t spikelen
        uint32_t sda_hold
        uint32_t sda_setup
        uint32_t ack_general_call
        uint32_t slv_data_nack_only
        uint32_t dma_control
        uint32_t dma_tdlr
        uint32_t dma_rdlr
        Fifo16 rx
        Fifo16 tx
        I2cBlock() noexcept
        int64_t raw_write_value() noexcept
        uint32_t int_status() noexcept
        uint32_t master_bits() noexcept
        cppbool reset() noexcept
        cppbool check_interrupts() noexcept
        cppbool complete_start() noexcept
        cppbool complete_connect(cppbool ack, uint32_t nack_byte) noexcept
        cppbool complete_write(cppbool ack) noexcept
        cppbool complete_read(uint32_t value) noexcept
        cppbool complete_stop() noexcept
        cppbool arbitration_lost() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPI2C:
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block
    cdef I2cBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object rp2040
    cdef public object name
    cdef public object irq
    cdef object _on_start
    cdef object _on_connect
    cdef object _on_write_byte
    cdef object _on_read_byte
    cdef object _on_stop
