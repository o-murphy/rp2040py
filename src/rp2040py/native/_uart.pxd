# Cython view of core/uart.hpp (docs/records/0096-cpp-mcu-core.md, Phase 4) and the declaration of the native UART shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._core_host cimport ChipPorts

from rp2040py.native._clock cimport Alarm, Clock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "uart.hpp" namespace "rp2040core":

    ctypedef cppbool (*UartByteFn)(void* ctx, uint32_t byte)
    ctypedef cppbool (*UartBaudFn)(void* ctx)
    ctypedef cppbool (*UartBitFn)(void* ctx, double* nanos)

    cdef cppclass UartHost:
        UartHost() noexcept
        UartByteFn on_byte
        UartBaudFn baud_changed
        UartBitFn bit_ns
        void* ctx

    cdef cppclass UartBlock:
        uint32_t ctrl
        uint32_t line_ctrl
        uint32_t int_divisor
        uint32_t frac_divisor
        uint32_t interrupt_mask
        uint32_t interrupt_status
        uint32_t ifls
        uint32_t ilpr
        uint32_t dmacr
        uint32_t rsr
        Alarm tx_alarm
        UartBlock() noexcept
        void init(Clock* clock, const ChipPorts* ports, uint32_t block, uint32_t irq_line, uint32_t dreq_base, const UartHost& host) noexcept
        void detach() noexcept
        int64_t raw_write_value() noexcept
        uint32_t rx_count() noexcept
        cppbool rx_empty() noexcept
        cppbool rx_full() noexcept
        uint32_t rx_at(uint32_t index) noexcept
        void rx_push(uint32_t value) noexcept
        uint32_t rx_pull() noexcept
        uint32_t rx_peek() noexcept
        void rx_reset() noexcept
        uint32_t tx_count() noexcept
        cppbool tx_empty() noexcept
        uint32_t tx_capacity() noexcept
        cppbool tx_full() noexcept
        uint32_t tx_at(uint32_t index) noexcept
        uint32_t tx_trigger() noexcept
        cppbool tx_shifting() noexcept
        void tx_push(uint32_t value) noexcept
        uint32_t tx_pull() noexcept
        void tx_reset() noexcept
        uint32_t flags() noexcept
        cppbool enabled() noexcept
        cppbool update_dreq() noexcept
        cppbool check_interrupts() noexcept
        cppbool feed_byte(uint32_t value) noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPUART:
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block
    cdef UartBlock* _block  # the chip's (`Mcu` builds and wires it), or `_own` for a block made directly (a stand-in chip, a test)
    cdef UartBlock _own
    cdef ChipPorts _ports  # a block made directly (`_own`) reaches the chip through these, trampolines into Python
    cdef uint32_t _dreq_base  # the stand-in's TX request number: the number the block passes for TX, the other is RX
    cdef public object rp2040
    cdef public object clock
    cdef public object name
    cdef public object irq
    cdef public object dreq
    cdef public object on_byte
    cdef public object on_baud_rate_change
