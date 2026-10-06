// The Cortex-M0's private peripheral bus in C++ (docs/records/0096-cpp-mcu-core.md, the PPB design note): a faithful translation of peripherals/_ppb.py, which stays as the
// pure-Python reference and the oracle (tests/test_ppb_diff.py).
//
// What it is: the registers the processor itself owns, at 0xE000E000 + offset - SysTick (SYST_CSR / RVR / CVR / CALIB), the NVIC (set/clear enable and pending words, the eight
// priority words), and the SCB registers the RP2040 firmware touches (CPUID, ICSR, VTOR, SHPR2/3). Almost all of it is a *view of the CPU's own state*: the pending and enabled
// words, the priority bitmaps, the exception flags and VTOR live in `Cpu` (core/cpu.hpp), which already reads them to take an exception - the block reads and writes those fields,
// it owns none of them. What it does own is SysTick: a 24-bit down-counter on a `Timer32` (core/timer32.hpp) clocked from clk_sys whose TOP is RELOAD (a cycle of RELOAD + 1 ticks, which is also
// how the counter comes round to RELOAD), and one compare alarm at 0 that sets COUNTFLAG and pends the SysTick exception when TICKINT is set.
//
// So a core that is embedded without a Python host needs nothing else for its private bus: the only thing a host tells this block is the logger (`warn`), through the same
// `RegWarnFn` data-not-strings convention as every other block (core_host.hpp). The chip's clk_sys is told to the block by `clk_sys_changed()` (the C++ clock tree, or the Python
// one today, calls it when clk_sys changes - what `update_clocks` does to `systick_timer.frequency`).
//
// Quirks of the reference that are kept (pinned by tests/test_ppb_diff.py and the C++ checks):
//   - SysTick's `clk_source` selects the counter's clock: 0 the 1 MHz reference clock (the reset state), 1 clk_sys; the block retunes the timer on a CSR write and when told clk_sys changed;
//   - SYST_CALIB reads 0x0000270F (measured on the silicon) whatever the clock;
//   - SYST_RVR is 24 bits wide (RELOAD, bits 23:0 - pico-sdk hardware/regs/m0plus.h, M0PLUS_SYST_RVR): a write keeps the low 24 bits and sets the timer's TOP; a RELOAD of 0 disarms the alarm;
//   - a read of SYST_CSR returns COUNTFLAG and clears it - the one register read with a side effect;
//   - ISPR/ICPR read the pending word and ISER/ICER the enabled word (the hardware does too); ICPR cannot clear a hardware line (bits 0..MAX_HW_IRQ-1 stay: the peripheral owns them);
//   - the priority registers are views of the CPU's four priority *bitmaps* (bit n of bitmap p = "interrupt n has priority p"): a write rebuilds the interrupt's bit in all four, a
//     read ORs the priorities of every bitmap that has the bit;
//   - NMIPENDSET / PENDSVSET / PENDSTSET also raise `interrupts_updated`; the CLR bits do not;
//   - an offset that is not a register warns (read: and reads 0xFFFFFFFF; write: and is dropped); the bus hands this block the offset inside its 4 KiB window, so the "atomic
//     area" warning of the base class cannot happen here.
//
// Failures: nothing in the block can fail except the host's logger, and a failing `warn` parks its error with the host (core_host.hpp's contract) - the block carries on and the
// embedder re-raises when the access returns.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. An alarm is a node the block holds, so the block must not move after `init()`, and `detach()` must run before it goes.
#ifndef RP2040PY_CORE_PPB_HPP
#define RP2040PY_CORE_PPB_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"
#include "cpu.hpp"
#include "timer32.hpp"

namespace rp2040core {

constexpr uint32_t kPpbWarnRead = kRegWarnRead, kPpbWarnWrite = kRegWarnWrite;
using PpbWarnFn = RegWarnFn;

struct PpbHost {
    PpbWarnFn warn = nullptr;
    void* ctx = nullptr;
};

namespace ppb_regs {
constexpr uint32_t CPUID = 0xD00, ICSR = 0xD04, VTOR = 0xD08, SHPR2 = 0xD1C, SHPR3 = 0xD20;
constexpr uint32_t SYST_CSR = 0x010, SYST_RVR = 0x014, SYST_CVR = 0x018, SYST_CALIB = 0x01C;
constexpr uint32_t NVIC_ISER = 0x100, NVIC_ICER = 0x180, NVIC_ISPR = 0x200, NVIC_ICPR = 0x280;
constexpr uint32_t NVIC_IPR0 = 0x400, NVIC_IPR_END = 0x420;  // eight words, 0x400..0x41C
constexpr uint32_t kCpuId = 0x410CC601, kSystCalib = 0x0000270F;
constexpr uint32_t NMIPENDSET = 1u << 31, PENDSVSET = 1u << 28, PENDSVCLR = 1u << 27, PENDSTSET = 1u << 26, PENDSTCLR = 1u << 25, ISRPENDING = 1u << 22;
constexpr uint32_t VECTPENDING_SHIFT = 12, VECTACTIVE_MASK = 0x1FF;
constexpr int64_t kSysTickTop = 0xFFFFFF;
// SysTick's external reference clock (SYST_CSR.CLKSOURCE = 0): 1 MHz, inferred from SYST_CALIB.TENMS = 0x270F (10000 ticks in 10 ms; pico-sdk hardware/regs/m0plus.h). See the reference.
constexpr double kSysTickRefClk = 1e6;
}  // namespace ppb_regs

class PpbBlock {
public:
    PpbBlock() = default;
    PpbBlock(const PpbBlock&) = delete;  // the alarm and the clock hold pointers into this object
    PpbBlock& operator=(const PpbBlock&) = delete;

    // SysTick's state, public for the shell (the reference's attributes are read and, by tests, written directly).
    Timer32 timer;
    Timer32PeriodicAlarm alarm;
    bool count_flag = false, clk_source = false, int_enable = false;
    uint32_t reload = 0;

    // Binds the block to the CPU whose private bus it is, the chip's clock and its clk_sys, and runs the reference's constructor: TOP, mode and alarm target, then reset().
    // `max_hardware_irq` is the CPU's (the highest IRQ a chip raises): ICPR cannot clear a pending bit at or below it.
    void init(Cpu* cpu, Clock* clock, const PpbHost& host, double clk_sys, uint32_t max_hardware_irq) noexcept {
        cpu_ = cpu;
        host_ = host;
        hardware_interrupt_mask_ = max_hardware_irq >= 32 ? 0xFFFFFFFFu : (1u << max_hardware_irq) - 1u;
        clk_sys_ = clk_sys;
        timer.init(clock, clk_sys);
        alarm.init(&timer, &PpbBlock::on_systick_alarm, this);
        timer.set_top(ppb_regs::kSysTickTop);
        timer.set_mode(TimerMode::kDecrement);
        alarm.set_target(0);
        alarm.set_enable(true);
        reset();
    }

    // Unlinks the alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept { alarm.detach(); }

    // `RESETS`-less: the PPB is part of the processor, a chip reset puts SysTick back to stopped, reload 0xFFFFFF, counter 0xFFFFFF (the reference writes through its own registers).
    void reset() noexcept {
        using namespace ppb_regs;
        write(SYST_CSR, 0);
        write(SYST_RVR, 0xFFFFFFu);
        timer.set(kSysTickTop);
    }

    // clk_sys is now `hz` (the clock tree tells the block): SysTick follows it when CLKSOURCE says processor clock.
    double clk_sys() const noexcept { return clk_sys_; }
    void clk_sys_changed(double hz) noexcept {
        clk_sys_ = hz;
        retune();
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace ppb_regs;
        Cpu& core = *cpu_;
        switch (offset) {
            case CPUID: return kCpuId;
            case ICSR: {
                const bool pending_interrupts = core.pending_interrupts != 0 || core.pending_pend_sv || core.pending_systick || core.pending_svcall;
                return (core.pending_nmi ? NMIPENDSET : 0u) | (core.pending_pend_sv ? PENDSVSET : 0u) | (core.pending_systick ? PENDSTSET : 0u) |
                       (pending_interrupts ? ISRPENDING : 0u) | (core.vect_pending() << VECTPENDING_SHIFT) | (core.ipsr & VECTACTIVE_MASK);
            }
            case VTOR: return core.vtor;
            case NVIC_ISPR:
            case NVIC_ICPR: return core.pending_interrupts;
            case NVIC_ISER:
            case NVIC_ICER: return core.enabled_interrupts;
            case SHPR2: return core.shpr2;
            case SHPR3: return core.shpr3;
            case SYST_CSR: {
                const uint32_t value = (count_flag ? 1u << 16 : 0u) | (clk_source ? 1u << 2 : 0u) | (int_enable ? 1u << 1 : 0u) | (timer.enable() ? 1u : 0u);
                count_flag = false;
                return value;
            }
            case SYST_CVR: return timer.counter();
            case SYST_RVR: return reload;
            case SYST_CALIB: return kSystCalib;
            default: break;
        }
        if (offset >= NVIC_IPR0 && offset < NVIC_IPR_END && (offset & 3) == 0) {
            const uint32_t reg_index = (offset - NVIC_IPR0) >> 2;
            uint32_t result = 0;
            for (uint32_t byte_index = 0; byte_index < 4; ++byte_index) {
                const uint32_t interrupt_number = reg_index * 4 + byte_index;
                for (uint32_t priority = 0; priority < 4; ++priority) {
                    if (core.interrupt_priorities[priority] & (1u << interrupt_number)) result |= priority << (8 * byte_index + 6);
                }
            }
            return result;
        }
        if (host_.warn) host_.warn(host_.ctx, kPpbWarnRead, offset, 0);
        return 0xFFFFFFFFu;
    }

    // The bus calls this directly (a write to the PPB window has never had an atomic alias); only the low 32 bits of `value` count.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace ppb_regs;
        Cpu& core = *cpu_;
        const uint32_t word = static_cast<uint32_t>(value);
        switch (offset) {
            case ICSR:
                if (word & NMIPENDSET) {
                    core.pending_nmi = true;
                    core.interrupts_updated = true;
                }
                if (word & PENDSVSET) {
                    core.pending_pend_sv = true;
                    core.interrupts_updated = true;
                }
                if (word & PENDSVCLR) core.pending_pend_sv = false;
                if (word & PENDSTSET) {
                    core.pending_systick = true;
                    core.interrupts_updated = true;
                }
                if (word & PENDSTCLR) core.pending_systick = false;
                return true;
            case VTOR: core.vtor = word; return true;
            case NVIC_ISPR:
                core.pending_interrupts |= word;
                core.interrupts_updated = true;
                return true;
            case NVIC_ICPR: core.pending_interrupts &= ~word | hardware_interrupt_mask_; return true;
            case NVIC_ISER:
                core.enabled_interrupts |= word;
                core.interrupts_updated = true;
                return true;
            case NVIC_ICER: core.enabled_interrupts &= ~word; return true;
            case SHPR2: core.shpr2 = word; return true;
            case SHPR3: core.shpr3 = word; return true;
            case SYST_CSR:
                clk_source = (word & (1u << 2)) != 0;
                int_enable = (word & (1u << 1)) != 0;
                retune();  // CLKSOURCE 0 is the 1 MHz reference clock, 1 the processor clock
                timer.set_enable((word & 1u) != 0);
                return true;
            case SYST_CVR: timer.set(0); return true;
            case SYST_RVR:
                // The counter is a cycle RELOAD, RELOAD-1 ... 0, RELOAD ...: a TOP of RELOAD on the timer, a period of RELOAD + 1 ticks (and the first period after a CVR write the
                // same: the counter sits at 0 and the next tick loads RELOAD). A RELOAD of 0 never fires.
                reload = word & 0xFFFFFFu;
                timer.set_top(reload);
                alarm.set_enable(reload != 0);
                return true;
            default: break;
        }
        if (offset >= NVIC_IPR0 && offset < NVIC_IPR_END && (offset & 3) == 0) {
            const uint32_t reg_index = (offset - NVIC_IPR0) >> 2;
            for (uint32_t byte_index = 0; byte_index < 4; ++byte_index) {
                const uint32_t interrupt_number = reg_index * 4 + byte_index;
                const uint32_t new_priority = (word >> (8 * byte_index + 6)) & 0x3u;
                for (uint32_t priority = 0; priority < 4; ++priority) core.interrupt_priorities[priority] &= ~(1u << interrupt_number);
                core.interrupt_priorities[new_priority] |= 1u << interrupt_number;
            }
            core.interrupts_updated = true;
            return true;
        }
        if (host_.warn) host_.warn(host_.ctx, kPpbWarnWrite, offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step - remember the raw value, decode the alias against a *read* of the register (with that read's side effects), then write.
    // The bus never sends an alias to this block; it is here because the reference inherits the method, and a host may call it.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        return write(offset, value);
    }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    WindowHandler window_handler() noexcept { return WindowHandler{&PpbBlock::window_read32, &PpbBlock::window_write32, this}; }

private:
    static uint32_t window_read32(void* ctx, uint32_t offset) noexcept { return static_cast<PpbBlock*>(ctx)->read(offset); }
    // The bus's PPB path writes the caller's value with `atomic_type` 0 and never has this block decode an alias (the reference's `RP2040.write_uint32` calls `write_uint32`).
    static void window_write32(void* ctx, uint32_t offset, int64_t raw_value, uint32_t) noexcept { (void)static_cast<PpbBlock*>(ctx)->write(offset, raw_value); }

    // The compare alarm at 0: COUNTFLAG, the SysTick exception if TICKINT, and the reload.
    static bool on_systick_alarm(void* ctx) noexcept {
        PpbBlock* self = static_cast<PpbBlock*>(ctx);
        self->count_flag = true;
        if (self->int_enable) {
            self->cpu_->pending_systick = true;
            self->cpu_->interrupts_updated = true;
        }
        return true;  // the counter is a cycle RELOAD..0 (TOP = RELOAD), so it comes round to RELOAD by itself
    }

    void retune() noexcept {
        const double frequency = clk_source ? clk_sys_ : ppb_regs::kSysTickRefClk;
        if (timer.frequency() != frequency) timer.set_frequency(frequency);
    }

    Cpu* cpu_ = nullptr;
    double clk_sys_ = 0.0;
    PpbHost host_;
    uint32_t hardware_interrupt_mask_ = 0;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PPB_HPP
