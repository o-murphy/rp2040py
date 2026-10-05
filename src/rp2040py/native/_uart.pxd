# Cython view of core/uart.hpp (docs/records/0096-cpp-mcu-core.md, Phase 4) and the declaration of the native UART shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "uart.hpp" namespace "rp2040core":
    cdef const uint32_t kUartWarnRead
    cdef const uint32_t kUartWarnReadAtomicArea
    cdef const uint32_t kUartWarnWrite

    ctypedef cppbool (*UartIrqFn)(void* ctx, cppbool level)
    ctypedef cppbool (*UartDreqFn)(void* ctx, cppbool asserted)
    ctypedef cppbool (*UartByteFn)(void* ctx, uint32_t byte)
    ctypedef cppbool (*UartBaudFn)(void* ctx)
    ctypedef void (*UartWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass UartHost:
        UartHost() noexcept
        UartIrqFn irq
        UartDreqFn dreq
        UartByteFn on_byte
        UartBaudFn baud_changed
        UartWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass UartBlock:
        uint32_t ctrl
        uint32_t line_ctrl
        uint32_t int_divisor
        uint32_t frac_divisor
        uint32_t interrupt_mask
        uint32_t interrupt_status
        UartBlock() noexcept
        void init(const UartHost& host) noexcept
        int64_t raw_write_value() noexcept
        uint32_t rx_count() noexcept
        cppbool rx_empty() noexcept
        cppbool rx_full() noexcept
        uint32_t rx_at(uint32_t index) noexcept
        void rx_push(uint32_t value) noexcept
        uint32_t rx_pull() noexcept
        uint32_t rx_peek() noexcept
        void rx_reset() noexcept
        uint32_t flags() noexcept
        cppbool enabled() noexcept
        cppbool check_interrupts() noexcept
        cppbool feed_byte(uint32_t value) noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPUART:
    cdef UartBlock _block
    cdef public object rp2040
    cdef public object name
    cdef public object irq
    cdef public object dreq
    cdef public object on_byte
    cdef public object on_baud_rate_change
