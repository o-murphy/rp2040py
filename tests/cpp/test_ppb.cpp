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

// A quirk of the reference, kept: a write to SYST_CVR sets the counter to 0, where the alarm *is* (target 0) - and a counter sitting on its target is reached one full wrap later, so
// the first period after CVR is written is 2^24 ticks whatever the reload; the periods after that are `reload` ticks (the reload is copied into the counter by the alarm).
static const double kFirstPeriodUs = 16777216.0;

static void check_systick_counts_down_and_fires() {
    Rig r;
    r.ppb.write(SYST_RVR, 9);
    r.ppb.write(SYST_CVR, 0);  // any write clears the counter
    CHECK(r.ppb.read(SYST_CVR) == 0);
    r.ppb.write(SYST_CSR, 1);  // enable only: no interrupt
    CHECK(r.clk.has_alarm());
    CHECK(r.clk.nanos_to_next_alarm() == kFirstPeriodUs * 1000.0);
    r.us(4);
    CHECK(r.ppb.read(SYST_CVR) == 0x1000000u - 4);  // a down counter that started from 0 = TOP + 1
    r.us(kFirstPeriodUs - 4 - 1);
    CHECK(!r.ppb.count_flag);
    r.us(1);  // the alarm at 0
    CHECK(r.cpu.pending_systick == false);  // TICKINT was not set
    CHECK(r.ppb.read(SYST_CVR) == 9);       // reloaded
    CHECK(r.clk.nanos_to_next_alarm() == 9000.0);
    const uint32_t csr = r.ppb.read(SYST_CSR);
    CHECK((csr & (1u << 16)) != 0);  // COUNTFLAG
    CHECK((csr & 1u) == 1u);
    CHECK((r.ppb.read(SYST_CSR) & (1u << 16)) == 0);  // the read cleared it
    r.us(5);
    CHECK(r.ppb.read(SYST_CVR) == 4);
    r.us(4);  // the second period is the reload
    CHECK((r.ppb.read(SYST_CSR) & (1u << 16)) != 0);
}

static void check_tickint_pends_the_exception() {
    Rig r;
    r.ppb.write(SYST_RVR, 4);
    r.ppb.write(SYST_CVR, 0);
    r.ppb.write(SYST_CSR, 3);  // enable + TICKINT
    r.cpu.interrupts_updated = false;
    r.us(kFirstPeriodUs - 1);
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
    r.us(kFirstPeriodUs + 30);
    CHECK(!r.cpu.pending_systick);
    CHECK((r.ppb.read(SYST_CSR) & (1u << 16)) != 0);
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
    r.ppb.set_frequency(2 * kHz);
    CHECK(r.ppb.clk_sys() == 2 * kHz);
    CHECK(r.clk.has_alarm());
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
    check_stopping_and_frequency();
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
