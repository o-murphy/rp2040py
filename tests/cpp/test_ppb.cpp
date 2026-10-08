// Standalone checks of src/rp2040py/native/core/ppb.hpp (see tests/test_core_cpp.py for the flags).
//
// The end-to-end proof is tests/test_ppb_diff.py (the reference against the chip's PPB, thousands of random steps with every mutant of the reference caught); these are the directed
// checks that need no Python: the register map and its read-back, SysTick's alarm schedule against a bare `Clock` (COUNTFLAG, TICKINT, the reload, a retuned clk_sys, a stopped
// counter), the one read with a side effect (SYST_CSR), the NVIC words and the priority bitmaps, the SCB registers, the warnings, the bus window adapter and detach.
#include <cstdio>

#include "ppb.hpp"

using namespace rp2040core;
using namespace rp2040core::ppb_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Warn {
    uint32_t kind[8], offset[8];
    int64_t value[8];
    int n = 0;
};
static void on_warn(void* ctx, uint32_t kind, uint32_t offset, int64_t value) {
    Warn* w = static_cast<Warn*>(ctx);
    if (w->n < 8) {
        w->kind[w->n] = kind;
        w->offset[w->n] = offset;
        w->value[w->n] = value;
    }
    ++w->n;
}

static const double kHz = 1e6;  // one tick per microsecond: a reload of N fires every N+1 microseconds
static const double kSysTickRefClk = 1e6;  // the watchdog's tick as these checks run it: nominal 1 MHz
static const uint32_t kIrqMax = 25;

struct Rig {
    Clock clk;
    Cpu cpu;
    PpbBlock ppb;
    Warn warns;
    Rig() {
        PpbHost host;
        host.warn = on_warn;
        host.ctx = &warns;
        ppb.init(&cpu, &clk, host, kHz, kIrqMax);
        ppb.tick_changed(kSysTickRefClk);  // the watchdog's tick is running
    }
    ~Rig() { ppb.detach(); }
    void us(double n) { CHECK(clk.tick(n * 1000.0)); }
};

static void check_reset_state() {
    Rig r;
    CHECK(r.ppb.read(SYST_RVR) == 0xFFFFFF);
    CHECK(r.ppb.read(SYST_CVR) == 0xFFFFFF);
    CHECK(r.ppb.read(SYST_CSR) == 0);  // stopped, no flag
    CHECK(r.ppb.read(SYST_CALIB) == 0x270F);
    CHECK(r.ppb.read(CPUID) == 0x410CC601);
    CHECK(!r.clk.has_alarm());
    CHECK(r.warns.n == 0);
}

// The counter is a cycle RELOAD, RELOAD-1 ... 0, RELOAD ...: a period of RELOAD + 1 ticks, and the first period after a write to SYST_CVR (which clears the counter to 0; the next
// tick loads RELOAD) is the same. COUNTFLAG is set - and the exception pended - when the counter reaches 0.
static void check_systick_counts_down_and_fires() {
    Rig r;
    r.ppb.write(SYST_RVR, 9);
    r.ppb.write(SYST_CVR, 0);  // any write clears the counter
    CHECK(r.ppb.read(SYST_CVR) == 0);
    r.ppb.write(SYST_CSR, 1);  // enable only: no interrupt
    CHECK(r.clk.has_alarm());
    CHECK(r.clk.nanos_to_next_alarm() == 10000.0);  // RELOAD + 1 ticks
    r.us(1);
    CHECK(r.ppb.read(SYST_CVR) == 9);  // the next tick loaded RELOAD
    r.us(4);
    CHECK(r.ppb.read(SYST_CVR) == 5);
    r.us(4);
    CHECK(r.ppb.read(SYST_CVR) == 1);
    CHECK(!r.ppb.count_flag);
    r.us(1);  // reached 0
    CHECK(r.ppb.read(SYST_CVR) == 0);
    CHECK(r.cpu.pending_systick == false);  // TICKINT was not set
    const uint32_t csr = r.ppb.read(SYST_CSR);
    CHECK((csr & (1u << 16)) != 0);  // COUNTFLAG
    CHECK((csr & 1u) == 1u);
    CHECK((r.ppb.read(SYST_CSR) & (1u << 16)) == 0);  // the read cleared it
    CHECK(r.clk.nanos_to_next_alarm() == 10000.0);     // the next period is RELOAD + 1 as well
    r.us(10);
    CHECK((r.ppb.read(SYST_CSR) & (1u << 16)) != 0);
}

static void check_tickint_pends_the_exception() {
    Rig r;
    r.ppb.write(SYST_RVR, 4);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 3);  // enable + TICKINT
    r.cpu.interrupts_updated = false;
    r.us(4);
    CHECK(!r.cpu.pending_systick && !r.cpu.interrupts_updated);
    r.us(1);
    CHECK(r.cpu.pending_systick);
    CHECK(r.cpu.interrupts_updated);
    CHECK((r.ppb.read(ICSR) & PENDSTSET) != 0);
    r.ppb.write(ICSR, PENDSTCLR);
    CHECK(!r.cpu.pending_systick);
    CHECK((r.ppb.read(ICSR) & PENDSTSET) == 0);
}

static void check_no_tickint_no_exception() {
    Rig r;
    r.ppb.write(SYST_RVR, 4);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 1);
    r.us(30);
    CHECK(!r.cpu.pending_systick);
    CHECK((r.ppb.read(SYST_CSR) & (1u << 16)) != 0);
}

static void check_a_reload_of_zero_never_fires() {
    Rig r;
    r.ppb.write(SYST_RVR, 0);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 3);
    CHECK(!r.clk.has_alarm());
    r.us(1000);
    CHECK(!r.cpu.pending_systick && (r.ppb.read(SYST_CSR) & (1u << 16)) == 0);
    r.ppb.write(SYST_RVR, 3);  // a reload written later arms it again
    CHECK(r.clk.has_alarm());
    r.us(5);
    CHECK(r.cpu.pending_systick);
    r.ppb.write(SYST_RVR, 0x1000000u);  // bit 24 is not RELOAD: the low 24 bits are 0
    CHECK(!r.clk.has_alarm());
}

static void check_stopping_and_frequency() {
    Rig r;
    r.ppb.write(SYST_RVR, 99);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 1);
    r.ppb.write(SYST_CSR, 0);  // stopped
    CHECK(!r.clk.has_alarm());
    const uint32_t frozen = r.ppb.read(SYST_CVR);
    r.us(500);
    CHECK(r.ppb.read(SYST_CVR) == frozen);
    r.ppb.write(SYST_CSR, 1);
    CHECK(r.clk.has_alarm());
}

// CLKSOURCE 0 (the reset state) is the reference clock - the watchdog's tick - whatever clk_sys is. The tick stopped stops the counter (CSR.ENABLE still reads as written), a different rate retunes it, and
// CLKSOURCE 1 does not care about the tick at all.
static void check_the_reference_clock_is_the_watchdogs_tick() {
    Rig r;
    r.ppb.write(SYST_RVR, 9);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 1);
    CHECK(r.clk.has_alarm() && r.clk.nanos_to_next_alarm() == 10000.0);
    r.ppb.tick_changed(0.0);  // the tick stops
    CHECK(r.ppb.timer.frequency() == kSysTickRefClk);  // the timer keeps its last rate: a rate of 0 is never applied
    CHECK(!r.clk.has_alarm() && !r.ppb.timer.enable() && (r.ppb.read(SYST_CSR) & 1u) == 1u && r.ppb.systick_enable);
    r.ppb.tick_changed(2e6);  // it runs at twice the rate
    CHECK(r.ppb.timer.frequency() == 2e6 && r.ppb.timer.enable() && r.clk.nanos_to_next_alarm() == 5000.0);
    r.ppb.write(SYST_CSR, 5);  // processor clock: the tick does not matter
    r.ppb.tick_changed(0.0);
    CHECK(r.ppb.timer.enable() && r.ppb.timer.frequency() == kHz && r.clk.has_alarm());
    r.ppb.write(SYST_CSR, 1);  // back to the reference clock, which is stopped
    CHECK(!r.ppb.timer.enable() && !r.clk.has_alarm() && r.ppb.tick_hz() == 0.0);
    r.ppb.tick_changed(kSysTickRefClk);
    CHECK(r.ppb.timer.enable() && r.clk.has_alarm());
    r.ppb.reset();  // a reset stops SysTick but not the tick
    CHECK(!r.ppb.systick_enable && !r.ppb.timer.enable() && r.ppb.tick_hz() == kSysTickRefClk);
}

// CLKSOURCE 0 (the reset state) is the 1 MHz reference clock whatever clk_sys is; CLKSOURCE 1 is clk_sys, and follows it.
static void check_the_clock_source() {
    Rig r;  // clk_sys = 1 MHz here
    r.ppb.clk_sys_changed(125e6);
    CHECK(r.ppb.clk_sys() == 125e6);
    CHECK(r.ppb.timer.frequency() == kSysTickRefClk);  // CLKSOURCE 0: not clk_sys
    r.ppb.write(SYST_RVR, 9);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 1);
    CHECK(r.clk.nanos_to_next_alarm() == 10000.0);  // 10 reference ticks of 1 us
    r.ppb.write(SYST_CSR, 5);                       // the processor clock
    CHECK(r.ppb.timer.frequency() == 125e6);
    CHECK(r.clk.nanos_to_next_alarm() > 0.0 && r.clk.nanos_to_next_alarm() <= 80.0);
    r.ppb.clk_sys_changed(2 * kHz);
    CHECK(r.ppb.timer.frequency() == 2 * kHz);
    r.ppb.write(SYST_CSR, 1);  // back to the reference clock
    CHECK(r.ppb.timer.frequency() == kSysTickRefClk);
    r.ppb.clk_sys_changed(48e6);  // clk_sys moves, the reference does not
    CHECK(r.ppb.timer.frequency() == kSysTickRefClk);
    r.ppb.reset();
    CHECK(r.ppb.timer.frequency() == kSysTickRefClk && !r.ppb.clk_source);
}

// A write to SYST_CVR clears the counter and COUNTFLAG (the datasheet: "Clearing this register also clears the COUNTFLAG bit").
static void check_a_cvr_write_clears_countflag() {
    Rig r;
    r.ppb.write(SYST_RVR, 3);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 1);
    r.us(4);
    CHECK(r.ppb.count_flag);
    r.ppb.write(SYST_CVR, 0x1234);
    CHECK(!r.ppb.count_flag);
    CHECK((r.ppb.read(SYST_CSR) & (1u << 16)) == 0);
    CHECK(r.ppb.read(SYST_CVR) == 0);
}

static void check_csr_bits_and_readback() {
    Rig r;
    r.ppb.write(SYST_CSR, 7);
    CHECK((r.ppb.read(SYST_CSR) & 7u) == 7u);
    r.ppb.write(SYST_CSR, 4);  // clk_source alone is stored
    CHECK((r.ppb.read(SYST_CSR) & 7u) == 4u);
    CHECK(r.ppb.clk_source && !r.ppb.int_enable && !r.ppb.timer.enable());
    r.ppb.write(SYST_RVR, 0xFFFFFFFFu);  // RELOAD is bits 23:0
    CHECK(r.ppb.read(SYST_RVR) == 0xFFFFFFu);
}

static void check_icsr() {
    Rig r;
    r.cpu.interrupts_updated = false;
    r.ppb.write(ICSR, PENDSVSET);
    CHECK(r.cpu.pending_pend_sv && r.cpu.interrupts_updated);
    uint32_t icsr = r.ppb.read(ICSR);
    CHECK((icsr & PENDSVSET) != 0 && (icsr & ISRPENDING) != 0);
    r.ppb.write(ICSR, PENDSVCLR);
    CHECK(!r.cpu.pending_pend_sv);
    CHECK((r.ppb.read(ICSR) & ISRPENDING) == 0);
    r.ppb.write(ICSR, NMIPENDSET);
    CHECK(r.cpu.pending_nmi);
    CHECK((r.ppb.read(ICSR) & NMIPENDSET) != 0);
    CHECK(((r.ppb.read(ICSR) >> VECTPENDING_SHIFT) & 0x1FF) == 2);  // the NMI is the vector pending
    r.cpu.pending_nmi = false;
    r.cpu.ipsr = 0x2F;
    CHECK((r.ppb.read(ICSR) & VECTACTIVE_MASK) == 0x2F);
}

static void check_nvic_words() {
    Rig r;
    r.cpu.interrupts_updated = false;
    r.ppb.write(NVIC_ISER, 0x30);
    CHECK(r.cpu.enabled_interrupts == 0x30 && r.cpu.interrupts_updated);
    r.ppb.write(NVIC_ISER, 0x01);
    CHECK(r.ppb.read(NVIC_ISER) == 0x31 && r.ppb.read(NVIC_ICER) == 0x31);
    r.ppb.write(NVIC_ICER, 0x10);
    CHECK(r.ppb.read(NVIC_ISER) == 0x21);
    r.ppb.write(NVIC_ISPR, 0xF0000000u);
    CHECK(r.ppb.read(NVIC_ISPR) == 0xF0000000u && r.ppb.read(NVIC_ICPR) == 0xF0000000u);
    r.ppb.write(NVIC_ICPR, 0xFFFFFFFFu);  // a software-pended line goes, the hardware lines (0..kIrqMax-1) stay as they were
    CHECK(r.ppb.read(NVIC_ISPR) == 0);
    r.cpu.pending_interrupts = 0xFFFFFFFFu;
    r.ppb.write(NVIC_ICPR, 0xFFFFFFFFu);
    CHECK(r.cpu.pending_interrupts == (1u << kIrqMax) - 1u);
}

static void check_priorities() {
    Rig r;
    CHECK(r.ppb.read(NVIC_IPR0) == 0);  // every interrupt starts at priority 0
    r.ppb.write(NVIC_IPR0 + 4, 0xC0804000u);  // interrupts 4..7 -> priorities 0, 1, 2, 3
    CHECK(r.ppb.read(NVIC_IPR0 + 4) == 0xC0804000u);
    CHECK((r.cpu.interrupt_priorities[0] & 0xF0u) == 0x10u);
    CHECK((r.cpu.interrupt_priorities[1] & 0xF0u) == 0x20u);
    CHECK((r.cpu.interrupt_priorities[2] & 0xF0u) == 0x40u);
    CHECK((r.cpu.interrupt_priorities[3] & 0xF0u) == 0x80u);
    r.ppb.write(NVIC_IPR0 + 4, 0);  // back to 0 in every bitmap
    CHECK((r.cpu.interrupt_priorities[1] | r.cpu.interrupt_priorities[2] | r.cpu.interrupt_priorities[3]) == 0);
    r.ppb.write(NVIC_IPR0 + 0x1C, 0xFFFFFFFFu);  // the last word: interrupts 28..31
    CHECK(r.ppb.read(NVIC_IPR0 + 0x1C) == 0xC0C0C0C0u);
    r.ppb.write(NVIC_IPR0 + 0x20, 0xFFFFFFFFu);  // not a priority register
    CHECK(r.warns.n == 1 && r.warns.kind[0] == kPpbWarnWrite && r.warns.offset[0] == 0x420);
}

static void check_scb_registers() {
    Rig r;
    r.ppb.write(VTOR, 0x20000100u);
    CHECK(r.cpu.vtor == 0x20000100u && r.ppb.read(VTOR) == 0x20000100u);
    r.ppb.write(SHPR2, 0x40000000u);
    r.ppb.write(SHPR3, 0xC0400000u);
    CHECK(r.cpu.shpr2 == 0x40000000u && r.ppb.read(SHPR3) == 0xC0400000u && r.cpu.shpr3 == 0xC0400000u);
}

static void check_warnings() {
    Rig r;
    CHECK(r.ppb.read(0x004) == 0xFFFFFFFFu);
    CHECK(r.warns.n == 1 && r.warns.kind[0] == kPpbWarnRead && r.warns.offset[0] == 0x004);
    r.ppb.write(0xD0C, 0x5FA0004u);
    CHECK(r.warns.n == 2 && r.warns.kind[1] == kPpbWarnWrite && r.warns.offset[1] == 0xD0C && r.warns.value[1] == 0x5FA0004);
}

static void check_reset_restores_systick() {
    Rig r;
    r.ppb.write(SYST_RVR, 3);
    r.ppb.write(SYST_CSR, 7);
    r.us(100);
    r.ppb.reset();
    CHECK(r.ppb.read(SYST_RVR) == 0xFFFFFF && r.ppb.read(SYST_CVR) == 0xFFFFFF);
    CHECK((r.ppb.read(SYST_CSR) & 7u) == 0 && !r.clk.has_alarm());
}

static void check_window_and_atomic() {
    Rig r;
    WindowHandler w = r.ppb.window_handler();
    w.write32(w.ctx, NVIC_ISER, 0x0F, kAtomicNormal);
    CHECK(w.read32(w.ctx, NVIC_ISER) == 0x0F);
    // the bus never asks this block to decode an alias, but the reference inherits the method
    CHECK(r.ppb.write_atomic(NVIC_ISER, 0xF0, kAtomicXor));
    CHECK(r.ppb.read(NVIC_ISER) == 0xFF && r.ppb.raw_write_value() == 0xF0);
    CHECK(r.ppb.write_atomic(NVIC_ICER, 0x0F, kAtomicClear));
    CHECK(r.ppb.read(NVIC_ICER) == 0x0F);  // CLEAR against a *read* of ICER (the enabled word) writes 0xF0 to a write-1-to-clear register
}

static void check_detach_and_clock_destroyed_first() {
    Cpu cpu;
    PpbBlock ppb;
    {
        Clock clk;
        PpbHost host;
        ppb.init(&cpu, &clk, host, kHz, kIrqMax);
        ppb.tick_changed(kSysTickRefClk);
        ppb.write(SYST_CSR, 1);
        CHECK(clk.has_alarm());
        // the clock goes first, with the alarm still armed
    }
    ppb.detach();  // must not touch the dead clock
}

int main() {
    check_reset_state();
    check_systick_counts_down_and_fires();
    check_tickint_pends_the_exception();
    check_no_tickint_no_exception();
    check_a_reload_of_zero_never_fires();
    check_stopping_and_frequency();
    check_the_clock_source();
    check_the_reference_clock_is_the_watchdogs_tick();
    check_a_cvr_write_clears_countflag();
    check_csr_bits_and_readback();
    check_icsr();
    check_nvic_words();
    check_priorities();
    check_scb_registers();
    check_warnings();
    check_reset_restores_systick();
    check_window_and_atomic();
    check_detach_and_clock_destroyed_first();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("ok\n");
    return 0;
}
