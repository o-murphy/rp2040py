// The ARMv6-M Cortex-M0+ core of the RP2040 in C++ (docs/records/0096-cpp-mcu-core.md, Phase 2 step 4b): registers, flags, the
// exception model and every Thumb instruction the Cython `CortexM0Core` (`_cortex_m0_core.pyx`) implements. That Cython file is
// the immediate reference, `_cortex_m0_core.py` the architectural one; `tests/test_cpu_parity.py` runs the same random
// instruction streams on this and on the pure-Python core and compares registers, flags, cycles, memory and the bus traffic.
//
// What it owns: all CPU state and the decode/execute loop. What it reaches through a `CpuHost` of function pointers (Python today,
// the C++ PPB/logger/debugger later): `on_break` (BKPT/UDF), `bl_taken` (a call-tracing hook, only called when one is installed),
// and `log` (the messages the Python core logs). The bus is a `Bus*`: the CPU calls it directly.
//
// Failure model: a Python callback that fails inside a bus access, an `on_break` or a hook cannot unwind through C++ frames; it
// parks its exception (see _pending.pyx) and raises the flag `*host.failed`. The CPU checks it after every such call and returns
// kCpuFault at once - leaving the state exactly as the Python exception would have: the instruction's earlier effects stay, its
// later ones (the register write after a failed load, the cycle count) do not happen. The caller re-raises.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_CPU_HPP
#define RP2040PY_CORE_CPU_HPP

#include <cstdint>

#include "bus.hpp"

namespace rp2040core {

constexpr int kCpuFault = -1;

enum CpuLog : uint32_t {
    kCpuLogMrsUnimplemented = 0,    // warning "MRS with unimplemented SYSm value: {a}"   (MRS and MSR share it, as in Python)
    kCpuLogInstrUnimplemented = 1,  // two warnings: "Warning: Instruction at {a:x} is not implemented yet!", "Opcode: 0x{b:x} (0x{c:x})"
    kCpuLogSev = 2,                 // info "SEV"
    kCpuLogYield = 3,               // info "Yield"
};

struct CpuHost {
    void (*on_break)(void* ctx, uint32_t imm) = nullptr;
    void (*bl_taken)(void* ctx, bool blx) = nullptr;
    void (*log)(void* ctx, uint32_t kind, uint32_t a, uint32_t b, uint32_t c) = nullptr;
    const int* failed = nullptr;  // nonzero: a callback parked a Python error
    void* ctx = nullptr;
};

namespace cpu_detail {
constexpr uint32_t EXC_RESET = 1, EXC_NMI = 2, EXC_HARDFAULT = 3, EXC_SVCALL = 11, EXC_PENDSV = 14, EXC_SYSTICK = 15;
constexpr uint32_t SYSM_APSR = 0, SYSM_XPSR = 3, SYSM_IPSR = 5, SYSM_MSP = 8, SYSM_PSP = 9, SYSM_PRIMASK = 16, SYSM_CONTROL = 20;
constexpr int LOWEST_PRIORITY = 4;
constexpr int MODE_THREAD = 0, MODE_HANDLER = 1;
constexpr int SP_MAIN = 0, SP_PROCESS = 1;
constexpr uint32_t SP_REGISTER = 13, PC_REGISTER = 15;
constexpr uint32_t WIDE_RANGE_START = 0xF000, WIDE_RANGE_END = 0xF800;

inline int64_t u32(int64_t n) noexcept { return n & 0xFFFFFFFFLL; }
inline int64_t s32(int64_t n) noexcept {
    const uint32_t u = static_cast<uint32_t>(n & 0xFFFFFFFFLL);
    return (u & 0x80000000u) ? static_cast<int64_t>(u) - 0x100000000LL : static_cast<int64_t>(u);
}
inline uint32_t sign_extend8(uint32_t v) noexcept { return static_cast<uint32_t>(static_cast<int32_t>(static_cast<int8_t>(v & 0xFF))); }
inline uint32_t sign_extend16(uint32_t v) noexcept { return static_cast<uint32_t>(static_cast<int32_t>(static_cast<int16_t>(v & 0xFFFF))); }
}  // namespace cpu_detail

class Cpu;
using OpFn = int (*)(Cpu&, uint32_t opcode, uint32_t opcode2, uint32_t opcode_pc);

class Cpu {
public:
    Cpu() noexcept { clear_state(); }
    Cpu(const Cpu&) = delete;
    Cpu& operator=(const Cpu&) = delete;

    // `max_hardware_irq`: the highest IRQ number a chip raises (a pending bit above it is dropped on delivery).
    void init(Bus* bus, const CpuHost& host, uint32_t max_hardware_irq) noexcept {
        bus_ = bus;
        host_ = host;
        if (host_.failed == nullptr) host_.failed = &never_failed_;
        max_hw_irq_ = max_hardware_irq;
        table_ = dispatch_table();
    }

    // ---- architectural state: public, the Python shell exposes it field by field -----------------------------------------
    uint32_t registers[16];
    uint32_t banked_sp;
    uint64_t cycles;
    bool event_registered, waiting;
    bool n, c, z, v;
    uint32_t break_rewind;
    bool pm;
    int sp_sel;
    bool n_priv;
    int current_mode;
    uint32_t ipsr, interrupt_nmi_mask, pending_interrupts, enabled_interrupts;
    uint32_t interrupt_priorities[4];
    bool pending_nmi, pending_pend_sv, pending_svcall, pending_systick, interrupts_updated;
    uint32_t vtor, shpr2, shpr3;
    bool bl_hook_enabled = false;  // call host.bl_taken on BL/BLX

    Bus& bus() noexcept { return *bus_; }
    const CpuHost& host() const noexcept { return host_; }
    bool failed() const noexcept { return *host_.failed != 0; }

    // Power-on state, then SP and PC from the vector table. False: a bus access failed (the state up to it is kept).
    bool reset() noexcept {
        clear_state();
        registers[13] = bus_->read32(vtor);
        if (failed()) return false;
        registers[13] &= 0xFFFFFFFCu;
        registers[15] = bus_->read32(vtor + 4);
        if (failed()) return false;
        registers[15] &= 0xFFFFFFFEu;
        return true;
    }

    // ---- register views with the Python properties' masking ---------------------------------------------------------------
    uint32_t apsr() const noexcept {
        return (n ? 0x80000000u : 0) | (z ? 0x40000000u : 0) | (c ? 0x20000000u : 0) | (v ? 0x10000000u : 0);
    }
    void set_apsr(uint32_t value) noexcept {
        n = (value & 0x80000000u) != 0;
        z = (value & 0x40000000u) != 0;
        c = (value & 0x20000000u) != 0;
        v = (value & 0x10000000u) != 0;
    }
    uint32_t x_psr() const noexcept { return apsr() | ipsr | (1u << 24); }
    void set_x_psr(uint32_t value) noexcept {
        set_apsr(value);
        ipsr = value & 0x3F;
    }
    void set_sp(uint32_t value) noexcept { registers[13] = value & 0xFFFFFFFCu; }
    uint32_t sp_process() const noexcept { return sp_sel == cpu_detail::SP_PROCESS ? registers[13] : banked_sp; }
    void set_sp_process(uint32_t value) noexcept {
        if (sp_sel == cpu_detail::SP_PROCESS) set_sp(value);
        else banked_sp = value;
    }
    uint32_t sp_main() const noexcept { return sp_sel == cpu_detail::SP_MAIN ? registers[13] : banked_sp; }
    void set_sp_main(uint32_t value) noexcept {
        if (sp_sel == cpu_detail::SP_MAIN) set_sp(value);
        else banked_sp = value;
    }
    uint32_t pend_sv_priority() const noexcept { return (shpr3 >> 22) & 0x3; }
    uint32_t sv_call_priority() const noexcept { return shpr2 >> 30; }
    uint32_t systick_priority() const noexcept { return shpr3 >> 30; }

    bool check_condition(uint32_t cond) const noexcept {
        const uint32_t base = cond >> 1;
        bool result = false;
        switch (base) {
            case 0: result = z; break;
            case 1: result = c; break;
            case 2: result = n; break;
            case 3: result = v; break;
            case 4: result = c && !z; break;
            case 5: result = n == v; break;
            case 6: result = (n == v) && !z; break;
            default: result = true; break;
        }
        if ((cond & 1) && cond != 0xF) return !result;
        return result;
    }

    void switch_stack(int stack) noexcept {
        if (sp_sel != stack) {
            const uint32_t temp = registers[13];
            registers[13] = banked_sp & 0xFFFFFFFCu;  // as the `sp` setter of the Python core masks it
            banked_sp = temp;
            sp_sel = stack;
        }
    }

    // ---- exceptions ---------------------------------------------------------------------------------------------------------
    bool exception_entry(uint32_t exception_number) noexcept {
        using namespace cpu_detail;
        uint32_t frame_ptr, frame_ptr_align;
        if (sp_sel && current_mode == MODE_THREAD) {
            frame_ptr_align = (sp_process() & 0x4) ? 1 : 0;
            set_sp_process((sp_process() - 0x20) & ~0x4u);
            frame_ptr = sp_process();
        } else {
            frame_ptr_align = (sp_main() & 0x4) ? 1 : 0;
            set_sp_main((sp_main() - 0x20) & ~0x4u);
            frame_ptr = sp_main();
        }
        // only the stack locations, not the store order, are architected
        const uint32_t frame[8] = {registers[0], registers[1], registers[2], registers[3], registers[12], registers[14],
                                   registers[15] & ~1u, (x_psr() & ~(1u << 9)) | (frame_ptr_align << 9)};
        for (uint32_t i = 0; i < 8; ++i) {
            bus_->write32(frame_ptr + 4 * i, frame[i]);
            if (failed()) return false;
        }
        if (current_mode == MODE_HANDLER) registers[14] = 0xFFFFFFF1u;
        else if (!sp_sel) registers[14] = 0xFFFFFFF9u;
        else registers[14] = 0xFFFFFFFDu;
        // ExceptionTaken:
        current_mode = MODE_HANDLER;  // Enter Handler Mode, now Privileged
        ipsr = exception_number;
        switch_stack(SP_MAIN);
        event_registered = true;
        const uint32_t target = bus_->read32(vtor + 4 * exception_number);
        if (failed()) return false;
        registers[15] = target;
        return true;
    }

    bool exception_return(uint32_t exc_return) noexcept {
        using namespace cpu_detail;
        uint32_t frame_ptr = sp_main();
        const uint32_t selector = exc_return & 0xF;
        if (selector == 0b0001) {  // Return to Handler
            current_mode = MODE_HANDLER;
            switch_stack(SP_MAIN);
        } else if (selector == 0b1001) {  // Return to Thread using Main stack
            current_mode = MODE_THREAD;
            switch_stack(SP_MAIN);
        } else if (selector == 0b1101) {  // Return to Thread using Process stack
            frame_ptr = sp_process();
            current_mode = MODE_THREAD;
            switch_stack(SP_PROCESS);
        }
        // PopStack:
        static constexpr uint32_t slots[7] = {0, 1, 2, 3, 12, 14, 15};
        for (uint32_t i = 0; i < 7; ++i) {
            const uint32_t word = bus_->read32(frame_ptr + (i < 4 ? 4 * i : (i == 4 ? 0x10 : (i == 5 ? 0x14 : 0x18))));
            if (failed()) return false;
            registers[slots[i]] = word;
        }
        const uint32_t psr = bus_->read32(frame_ptr + 0x1C);
        if (failed()) return false;
        const uint32_t frame_ptr_align = (psr & (1u << 9)) ? 0b100 : 0;
        if (selector == 0b0001 || selector == 0b1001) set_sp_main((sp_main() + 0x20) | frame_ptr_align);
        else if (selector == 0b1101) set_sp_process((sp_process() + 0x20) | frame_ptr_align);
        set_apsr(psr & 0xF0000000u);
        const bool force_thread = (current_mode == MODE_THREAD) && n_priv;
        ipsr = force_thread ? 0 : (psr & 0x3F);
        interrupts_updated = true;
        event_registered = true;  // Thumb bit should always be one: EPSR<24> = psr<24> is loaded from memory
        return true;
    }

    int exception_priority(uint32_t exc) const noexcept {
        using namespace cpu_detail;
        if (exc == EXC_RESET) return -3;
        if (exc == EXC_NMI) return -2;
        if (exc == EXC_HARDFAULT) return -1;
        if (exc == EXC_SVCALL) return static_cast<int>(sv_call_priority());
        if (exc == EXC_PENDSV) return static_cast<int>(pend_sv_priority());
        if (exc == EXC_SYSTICK) return static_cast<int>(systick_priority());
        if (exc < 16) return LOWEST_PRIORITY;
        const uint32_t int_num = exc - 16;
        if (int_num >= 32) return LOWEST_PRIORITY;  // no priority register covers it
        for (int priority = 0; priority < 4; ++priority) {
            if (interrupt_priorities[priority] & (1u << int_num)) return priority;
        }
        return LOWEST_PRIORITY;
    }

    uint32_t vect_pending() const noexcept {
        using namespace cpu_detail;
        if (pending_nmi) return EXC_NMI;
        const uint32_t sv = sv_call_priority(), st = systick_priority(), ps = pend_sv_priority();
        for (uint32_t priority = 0; priority < static_cast<uint32_t>(LOWEST_PRIORITY); ++priority) {
            const uint32_t level = pending_interrupts & interrupt_priorities[priority];
            if (pending_svcall && priority == sv) return EXC_SVCALL;
            if (pending_pend_sv && priority == ps) return EXC_PENDSV;
            if (pending_systick && priority == st) return EXC_SYSTICK;
            if (level) {
                for (uint32_t i = 0; i < 32; ++i) {
                    if (level & (1u << i)) return 16 + i;
                }
            }
        }
        return 0;
    }

    // 1: an exception was taken, 0: none, kCpuFault: a bus access failed while taking it.
    int check_for_interrupts() noexcept {
        using namespace cpu_detail;
        int current_priority;
        if (waiting) {
            current_priority = pm ? exception_priority(ipsr) : LOWEST_PRIORITY;
        } else {
            const int a = exception_priority(ipsr), b = pm ? 0 : LOWEST_PRIORITY;
            current_priority = a < b ? a : b;
        }
        const uint32_t interrupt_set = pending_interrupts & enabled_interrupts;
        const uint32_t sv = sv_call_priority(), st = systick_priority(), ps = pend_sv_priority();
        if (pending_nmi) {
            pending_nmi = false;
            return exception_entry(EXC_NMI) ? 1 : kCpuFault;
        }
        for (int p = 0; p < current_priority; ++p) {
            const uint32_t priority = static_cast<uint32_t>(p);
            const uint32_t level = interrupt_set & interrupt_priorities[priority];
            if (pending_svcall && priority == sv) {
                pending_svcall = false;
                return exception_entry(EXC_SVCALL) ? 1 : kCpuFault;
            }
            if (pending_pend_sv && priority == ps) {
                pending_pend_sv = false;
                return exception_entry(EXC_PENDSV) ? 1 : kCpuFault;
            }
            if (pending_systick && priority == st) {
                pending_systick = false;
                return exception_entry(EXC_SYSTICK) ? 1 : kCpuFault;
            }
            if (level) {
                for (uint32_t i = 0; i < 32; ++i) {
                    if (level & (1u << i)) {
                        if (i > max_hw_irq_) pending_interrupts &= ~(1u << i);
                        return exception_entry(16 + i) ? 1 : kCpuFault;
                    }
                }
            }
        }
        interrupts_updated = false;
        return 0;
    }

    // An interrupt line changed. False: waking the core took an exception and its bus access failed.
    bool set_interrupt(uint32_t irq, bool value) noexcept {
        if (irq >= 32) return true;  // no such line
        const uint32_t bit = 1u << irq;
        if (value && !(pending_interrupts & bit)) {
            pending_interrupts |= bit;
            interrupts_updated = true;
            if (waiting) {
                const int r = check_for_interrupts();
                if (r < 0) return false;
                if (r) waiting = false;
            }
        } else if (!value) {
            pending_interrupts &= ~bit;
        }
        return true;
    }

    bool read_special_register(uint32_t sysm, uint32_t* out) noexcept {
        using namespace cpu_detail;
        switch (sysm) {
            case SYSM_APSR: *out = apsr(); return true;
            case SYSM_XPSR: *out = x_psr(); return true;
            case SYSM_IPSR: *out = ipsr; return true;
            case SYSM_PRIMASK: *out = pm ? 1 : 0; return true;
            case SYSM_MSP: *out = sp_main(); return true;
            case SYSM_PSP: *out = sp_process(); return true;
            case SYSM_CONTROL: *out = (sp_sel == SP_PROCESS ? 2 : 0) | (n_priv ? 1 : 0); return true;
            default: break;
        }
        *out = 0;
        log(kCpuLogMrsUnimplemented, sysm, 0, 0);
        return !failed();
    }

    bool write_special_register(uint32_t sysm, uint32_t value) noexcept {
        using namespace cpu_detail;
        switch (sysm) {
            case SYSM_APSR: set_apsr(value); break;
            case SYSM_XPSR: set_x_psr(value); break;
            case SYSM_IPSR: ipsr = value; break;
            case SYSM_PRIMASK:
                pm = (value & 1) != 0;
                interrupts_updated = true;
                break;
            case SYSM_MSP: set_sp_main(value); break;
            case SYSM_PSP: set_sp_process(value); break;
            case SYSM_CONTROL:
                n_priv = (value & 1) != 0;
                if (current_mode == MODE_THREAD) switch_stack((value & 2) ? SP_PROCESS : SP_MAIN);
                break;
            default:
                log(kCpuLogMrsUnimplemented, sysm, 0, 0);
                return !failed();
        }
        return true;
    }

    // One instruction. Returns its cycle count, or kCpuFault (see the failure model above).
    int execute() noexcept {
        using namespace cpu_detail;
        if (interrupts_updated) {
            const int r = check_for_interrupts();
            if (r < 0) return kCpuFault;
            if (r) waiting = false;
        }
        // ARM Thumb instruction encoding - 16 bits / 2 bytes
        const uint32_t opcode_pc = registers[15] & 0xFFFFFFFEu;  // ensure no LSB set PC are executed
        const uint32_t opcode = bus_->read16(opcode_pc);
        if (failed()) return kCpuFault;
        const bool wide = opcode >= 0xE800;  // 0b11101xxx.. and 0b1111xxx.. : the two 32-bit encodings' first halfwords
        uint32_t opcode2 = 0;
        if (wide) {
            opcode2 = bus_->read16(opcode_pc + 2);
            if (failed()) return kCpuFault;
        }
        registers[15] += 2;

        OpFn handler = (opcode >= WIDE_RANGE_START && opcode < WIDE_RANGE_END) ? resolve_wide(opcode, opcode2) : table_[opcode];
        int delta;
        if (handler != nullptr) {
            delta = handler(*this, opcode, opcode2, opcode_pc);
            if (delta < 0) return kCpuFault;
        } else {
            delta = 1;
            log(kCpuLogInstrUnimplemented, opcode_pc, opcode, opcode2);
            if (failed()) return kCpuFault;
        }
        cycles += static_cast<uint64_t>(delta);
        return delta;
    }

    void log(uint32_t kind, uint32_t a, uint32_t b, uint32_t c_) noexcept {
        if (host_.log) host_.log(host_.ctx, kind, a, b, c_);
    }
    void on_break(uint32_t imm) noexcept {
        if (host_.on_break) host_.on_break(host_.ctx, imm);
    }
    void bl_taken(bool blx) noexcept {
        if (bl_hook_enabled && host_.bl_taken) host_.bl_taken(host_.ctx, blx);
    }

    static OpFn resolve_wide(uint32_t opcode, uint32_t opcode2) noexcept;
    static const OpFn* dispatch_table() noexcept;

private:
    void clear_state() noexcept {
        using namespace cpu_detail;
        for (uint32_t& r : registers) r = 0;
        banked_sp = 0;
        cycles = 0;
        event_registered = waiting = false;
        n = c = z = v = false;
        break_rewind = 0;
        pm = false;
        sp_sel = SP_MAIN;
        n_priv = false;
        current_mode = MODE_THREAD;
        ipsr = interrupt_nmi_mask = pending_interrupts = enabled_interrupts = 0;
        interrupt_priorities[0] = 0xFFFFFFFFu;
        interrupt_priorities[1] = interrupt_priorities[2] = interrupt_priorities[3] = 0;
        pending_nmi = pending_pend_sv = pending_svcall = pending_systick = interrupts_updated = false;
        vtor = shpr2 = shpr3 = 0;
    }

    Bus* bus_ = nullptr;
    CpuHost host_;
    uint32_t max_hw_irq_ = 0;
    const OpFn* table_ = nullptr;
    int never_failed_ = 0;
};

}  // namespace rp2040core

#include "cpu_ops.hpp"

#endif  // RP2040PY_CORE_CPU_HPP
