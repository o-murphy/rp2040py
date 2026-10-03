# Cython view of core/cpu.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2 step 4b): the Cortex-M0+ core. Header-only C++17.

from libc.stdint cimport int64_t, uint32_t, uint64_t
from libcpp cimport bool

from rp2040py.native._bus cimport Bus

cdef extern from "cpu.hpp" namespace "rp2040core":
    ctypedef void (*CpuBreakFn)(void* ctx, uint32_t imm)
    ctypedef void (*CpuBlFn)(void* ctx, bool blx)
    ctypedef void (*CpuLogFn)(void* ctx, uint32_t kind, uint32_t a, uint32_t b, uint32_t c)

    cdef const int kCpuFault
    cdef const uint32_t kCpuLogMrsUnimplemented
    cdef const uint32_t kCpuLogInstrUnimplemented
    cdef const uint32_t kCpuLogSev
    cdef const uint32_t kCpuLogYield

    cdef cppclass CpuHost:
        CpuBreakFn on_break
        CpuBlFn bl_taken
        CpuLogFn log
        const int* failed
        void* ctx

    cdef cppclass Cpu:
        uint32_t registers[16]
        uint32_t banked_sp
        uint64_t cycles
        bool event_registered, waiting
        bool n, c, z, v
        uint32_t break_rewind
        bool pm
        int sp_sel
        bool n_priv
        int current_mode
        uint32_t ipsr, interrupt_nmi_mask, pending_interrupts, enabled_interrupts
        uint32_t interrupt_priorities[4]
        bool pending_nmi, pending_pend_sv, pending_svcall, pending_systick, interrupts_updated
        uint32_t vtor, shpr2, shpr3
        bool bl_hook_enabled

        Cpu() noexcept
        void init(Bus* bus, const CpuHost& host, uint32_t max_hardware_irq) noexcept
        bool reset() noexcept
        uint32_t apsr() noexcept
        void set_apsr(uint32_t value) noexcept
        uint32_t x_psr() noexcept
        void set_x_psr(uint32_t value) noexcept
        void set_sp(uint32_t value) noexcept
        uint32_t sp_process() noexcept
        void set_sp_process(uint32_t value) noexcept
        uint32_t sp_main() noexcept
        void set_sp_main(uint32_t value) noexcept
        uint32_t pend_sv_priority() noexcept
        uint32_t sv_call_priority() noexcept
        uint32_t systick_priority() noexcept
        bool check_condition(uint32_t cond) noexcept
        void switch_stack(int stack) noexcept
        bool exception_entry(uint32_t exception_number) noexcept
        bool exception_return(uint32_t exc_return) noexcept
        int exception_priority(uint32_t exc) noexcept
        uint32_t vect_pending() noexcept
        int check_for_interrupts() noexcept
        bool set_interrupt(uint32_t irq, bool value) noexcept
        bool read_special_register(uint32_t sysm, uint32_t* out) noexcept
        bool write_special_register(uint32_t sysm, uint32_t value) noexcept
        int execute() noexcept
