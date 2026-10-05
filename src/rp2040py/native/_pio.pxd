# Cython view of core/pio.hpp (docs/records/0096-cpp-mcu-core.md, Phase 3) and the declaration of the native PIO shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int32_t, int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pin cimport PinBank
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "pio.hpp" namespace "rp2040core":
    cdef const uint32_t kPioWarnRead
    cdef const uint32_t kPioWarnReadAtomicArea
    cdef const uint32_t kPioWarnWrite
    cdef const uint32_t kPioErrorSmRead
    cdef const uint32_t kPioErrorSmWrite

    ctypedef cppbool (*PioIrqFn)(void* ctx, uint32_t line, cppbool level)
    ctypedef cppbool (*PioDreqFn)(void* ctx, uint32_t channel, cppbool set)
    ctypedef void (*PioLogFn)(void* ctx, uint32_t kind, uint32_t a, uint32_t b)
    ctypedef cppbool (*PioPinUpdateFn)(void* ctx, uint32_t pin)
    ctypedef cppbool (*PioPinInputFn)(void* ctx, uint32_t pin, cppbool* level)
    ctypedef cppbool (*PioStartedFn)(void* ctx)

    cdef cppclass PioHost:
        PioHost() noexcept
        PioIrqFn set_irq
        PioDreqFn dreq
        PioLogFn log
        PioPinUpdateFn pin_update
        PioPinInputFn pin_input
        PioStartedFn started
        void* ctx

    cdef cppclass PioFifo:
        uint32_t buffer[4]
        uint32_t start
        uint32_t used
        cppbool empty() noexcept
        cppbool full() noexcept
        void push(uint32_t value) noexcept
        uint32_t pull() noexcept
        uint32_t peek() noexcept
        void reset() noexcept

    cdef cppclass PioMachine:
        uint32_t index
        cppbool enabled
        uint32_t x, y, pc
        uint32_t input_shift_reg, input_shift_count, output_shift_reg, output_shift_count
        int64_t cycles
        uint32_t exec_opcode
        cppbool exec_valid, update_pc
        uint32_t clock_div_int, clock_div_frac
        int64_t div_fp, next_due_fp
        cppbool due_rearmed
        uint32_t exec_ctrl, shift_ctrl, pin_ctrl
        PioFifo rx, tx
        uint32_t out_pin_values, out_pin_direction
        cppbool waiting
        uint32_t wait_type, wait_index
        cppbool wait_polarity
        int32_t wait_delay
        uint32_t dreq_rx, dreq_tx
        uint32_t push_threshold() noexcept
        uint32_t pull_threshold() noexcept
        uint32_t sideset_count() noexcept
        uint32_t set_count() noexcept
        uint32_t out_count() noexcept
        uint32_t in_base() noexcept
        uint32_t sideset_base() noexcept
        uint32_t set_base() noexcept
        uint32_t out_base() noexcept
        uint32_t jmp_pin() noexcept
        uint32_t wrap_top() noexcept
        uint32_t wrap_bottom() noexcept
        uint32_t status() noexcept
        uint32_t fifo_stat() noexcept
        cppbool write_fifo(uint32_t value) noexcept
        cppbool read_fifo(uint32_t* out) noexcept
        cppbool execute_instruction(uint32_t opcode) noexcept
        cppbool step() noexcept
        cppbool check_wait() noexcept
        cppbool restart() noexcept
        void clk_div_restart() noexcept
        cppbool reset() noexcept
        cppbool read32(uint32_t offset, uint32_t* out) noexcept
        cppbool write32(uint32_t offset, uint32_t value) noexcept

    cdef cppclass PioBlock:
        PioBlock() noexcept
        PioMachine machines[4]
        uint32_t instructions[32]
        uint32_t irq, fdebug, tx_stall, rx_stall, input_sync_bypass
        uint32_t pin_values, pin_directions, old_pin_values, old_pin_directions
        uint32_t irq0_int_enable, irq0_int_force, irq1_int_enable, irq1_int_force
        int32_t stopped
        int64_t cycle_fp, next_due_fp, backlog_drops, raw_write_value
        cppbool init(const PioHost& host, uint32_t first_irq, uint32_t index) noexcept
        void bind_pin(uint32_t gpio, PinBank* bank) noexcept
        uint32_t int_raw() noexcept
        uint32_t irq0_int_status() noexcept
        uint32_t irq1_int_status() noexcept
        cppbool check_interrupts() noexcept
        cppbool irq_updated() noexcept
        cppbool gpio_values(uint32_t* out) noexcept
        void pin_values_changed(uint32_t value, uint32_t first_pin, uint32_t count) noexcept
        void pin_directions_changed(uint32_t value, uint32_t first_pin, uint32_t count) noexcept
        cppbool check_changed_pins() noexcept
        void recompute_due() noexcept
        void notify_due(int64_t due_fp) noexcept
        cppbool advance(int64_t cycles) noexcept
        cppbool run_due() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool read32(uint32_t offset, uint32_t* out) noexcept
        cppbool write32(uint32_t offset, uint32_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        cppbool reset() noexcept
        void stop() noexcept
        WindowHandler window_handler() noexcept


cdef class RPPIO:
    cdef PioBlock _block
    cdef public object rp2040
    cdef public str name
    cdef public list machines
    cdef public object dreq_rx
    cdef public object dreq_tx
    cdef public unsigned int first_irq
    cdef public unsigned int index
    cdef object _instructions
    cdef object _run_task
    cdef list _pin_refs

    cdef _warn(self, msg)
    cpdef check_changed_pins(self)
    cpdef recompute_due(self)
    cpdef notify_due(self, long long due_fp)
    cpdef advance(self, long long cycles)
    cpdef step(self)
