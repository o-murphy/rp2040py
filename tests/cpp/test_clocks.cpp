// Standalone checks of src/rp2040py/native/core/clocks.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_clocks.py): the registers' masks and resets, the frequency of each generator, the retune and the ORDER in which the sinks hear
// of it (sys sinks only on a change, ref and rtc sinks on every retune), the CLOCKS-domain reset, the frequency counter, and the failure of a sink.
#include <cstdio>

#include "clock_sinks.hpp"
#include "clocks.hpp"

using namespace rp2040core;
using namespace rp2040core::clocks_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

constexpr double MHZ = 1e6;

// A register write that succeeds.
#define W(offset, value) CHECK(clocks.write((offset), (value)))

struct Call {
    char sink;  // 's'ys, 'p'eri, 'r'ef, 'c' (rtc)
    double hz, old;
};

struct Env {
    Call calls[64] = {};
    int n = 0;
    uint32_t warn_kind[8] = {}, warn_offset[8] = {};
    int64_t warn_value[8] = {};
    int warns = 0;
    double xosc = 12 * MHZ, rosc = 6.5 * MHZ;
    int fail_sink = 0;  // the index of the sink call that fails (1-based), 0: none
    int failed = 0;
};
static Env env;

static constexpr uint32_t kTestBlock = 42;  // the id the block reports its warnings under
static void on_warn(void*, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) {
    CHECK(block == kTestBlock);

    if (env.warns < 8) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}
static double on_xosc(void*) { return env.xosc; }
static double on_rosc(void*) { return env.rosc; }
static bool record(char sink, double hz, double old) {
    if (env.n < 64) env.calls[env.n] = Call{sink, hz, old};
    ++env.n;
    if (env.fail_sink && env.n == env.fail_sink) {
        env.failed = 1;
        return false;
    }
    return true;
}
static bool on_sys(void*, double hz, double old) { return record('s', hz, old); }
static bool on_sys2(void*, double hz, double old) { return record('S', hz, old); }
static bool on_peri(void*, double hz, double old) { return record('p', hz, old); }
static int ctx_seen = 0;
static bool on_ctx(void* ctx, double, double) {
    ctx_seen = *static_cast<int*>(ctx);
    return true;
}
static bool on_ref(void*, double hz, double old) { return record('r', hz, old); }
static bool on_rtc(void*, double hz, double old) { return record('c', hz, old); }

static Clock clk;
static PllBlock pll_sys, pll_usb;
static ClocksBlock clocks;

static double pll_ref(void*) { return env.xosc; }
static bool pll_changed(void*) { return clocks.retune(); }

static const ChipPorts& test_ports() {
    static ChipPorts ports;
    ports.warn = on_warn;
    ports.failed = &env.failed;
    return ports;
}

static void fresh() {
    env = Env();
    clk.reset();
    PllHost ph;
    ph.ref_hz = pll_ref;
    ph.changed = pll_changed;
    pll_sys.init(&test_ports(), kTestBlock, ph);
    pll_usb.init(&test_ports(), kTestBlock, ph);
    pll_sys.reset();
    pll_usb.reset();
    ClocksHost host;
    host.xosc_hz = on_xosc;
    host.rosc_hz = on_rosc;
    clocks.init(&clk, &test_ports(), kTestBlock, host, &pll_sys, &pll_usb);
    CHECK(clocks.add_sys_sink(on_sys, nullptr));
    CHECK(clocks.add_sys_sink(on_sys2, nullptr));
    CHECK(clocks.add_peri_sink(on_peri, nullptr));
    CHECK(clocks.add_ref_sink(on_ref, nullptr));
    CHECK(clocks.add_rtc_sink(on_rtc, nullptr));
}

static void sdk_pll(PllBlock& pll, uint32_t fbdiv, uint32_t pd1, uint32_t pd2) {
    pll.write(pll_regs::REG_CS, 1);
    pll.write(pll_regs::REG_FBDIV_INT, fbdiv);
    pll.write(pll_regs::REG_PWR, pll_regs::PWR_POSTDIVPD);
    pll.write(pll_regs::REG_PRIM, (pd1 << 16) | (pd2 << 12));
    pll.write(pll_regs::REG_PWR, 0);
}

// clocks_init(): clk_ref on the crystal, clk_sys on PLL_SYS (aux), clk_peri on clk_sys.
static void sdk_clocks() {
    sdk_pll(pll_sys, 125, 6, 2);
    W(REF_CTRL, REF_SRC_XOSC);
    W(SYS_CTRL, 0x1 | (SYS_AUXSRC_PLL_SYS << 5));
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_CLK_SYS << 5));
}

static void test_defaults() {
    fresh();
    CHECK(clocks.clk_sys() == 125 * MHZ && clocks.clk_peri() == 125 * MHZ);
    CHECK(clocks.read(REF_DIV) == 0x100 && clocks.read(SYS_DIV) == 0x100 && clocks.read(RTC_DIV) == 0x100 && clocks.read(GPOUT0_DIV) == 0x100);
    CHECK(clocks.read(SYS_RESUS_CTRL) == 0xFF && clocks.read(WAKE_EN0) == 0xFFFFFFFFu && clocks.read(WAKE_EN1) == 0x7FFF);
    CHECK(clocks.read(FC0_MAX_KHZ) == 0x1FFFFFF && clocks.read(FC0_DELAY) == 1 && clocks.read(FC0_INTERVAL) == 8);
    CHECK(clocks.read(REF_SELECTED) == 1 && clocks.read(SYS_SELECTED) == 1 && clocks.read(PERI_SELECTED) == 1);
    CHECK(clocks.read(ENABLED0) == 0 && clocks.read(ENABLED1) == 0 && clocks.read(INTR) == 0 && clocks.read(SYS_RESUS_STATUS) == 0);
    CHECK(env.warns == 0 && env.n == 0);
}

static void test_register_masks() {
    fresh();
    W(REF_DIV, 0xFFFFFFFFu);
    CHECK(clocks.read(REF_DIV) == 0x300);
    W(USB_DIV, 0xFFFFFFFFu);
    CHECK(clocks.read(USB_DIV) == 0x300);
    W(ADC_DIV, 0xFFFFFFFFu);
    CHECK(clocks.read(ADC_DIV) == 0x300);
    W(SYS_DIV, 0xFFFFFFFFu);
    CHECK(clocks.read(SYS_DIV) == 0xFFFFFFFFu);
    W(REF_CTRL, 0xFFFFFFFFu);
    CHECK(clocks.read(REF_CTRL) == 0x63 && clocks.read(REF_SELECTED) == 8);
    W(SYS_CTRL, 0xFFFFFFFFu);
    CHECK(clocks.read(SYS_CTRL) == 0xE1 && clocks.read(SYS_SELECTED) == 2);
    W(PERI_CTRL, 0xFFFFFFFFu);
    CHECK(clocks.read(PERI_CTRL) == 0b110011100000);
    W(RTC_CTRL, 0xFFFFFFFFu);
    CHECK(clocks.read(RTC_CTRL) == 0b100110000110011100000);
    W(SYS_RESUS_CTRL, 0xFFFFFFFFu);
    CHECK(clocks.read(SYS_RESUS_CTRL) == 0x111FF);
    W(WAKE_EN1, 0xFFFFFFFFu);
    W(SLEEP_EN1, 0xFFFFFFFFu);
    CHECK(clocks.read(WAKE_EN1) == 0x7FFF && clocks.read(SLEEP_EN1) == 0x7FFF);
    W(INTE, 0xFFFFFFFFu);
    W(INTF, 0xFFFFFFFFu);
    CHECK(clocks.read(INTE) == 1 && clocks.read(INTF) == 1 && clocks.read(INTS) == 1);
    W(ENABLED0, 5);
    W(INTS, 5);
    W(SYS_RESUS_STATUS, 5);
    CHECK(clocks.read(ENABLED0) == 0 && clocks.read(INTS) == 1 && clocks.read(SYS_RESUS_STATUS) == 0);
    CHECK(env.warns == 0);
}

static void test_gpout_registers_are_stored() {
    fresh();
    const uint32_t ctrl[4] = {GPOUT0_CTRL, GPOUT1_CTRL, GPOUT2_CTRL, GPOUT3_CTRL}, div[4] = {GPOUT0_DIV, GPOUT1_DIV, GPOUT2_DIV, GPOUT3_DIV};
    for (int i = 0; i < 4; ++i) {
        CHECK(clocks.read(ctrl[i]) == 0 && clocks.read(div[i]) == 0x100);
        W(ctrl[i], 0xFFFFFFFFu);
        W(div[i], 0x12345678u);
        CHECK(clocks.read(ctrl[i]) == 0b100110001110111100000 && clocks.read(div[i]) == 0x12345678u);
    }
    clocks.reset();
    for (int i = 0; i < 4; ++i) CHECK(clocks.read(ctrl[i]) == 0 && clocks.read(div[i]) == 0x100);
    CHECK(env.n == 0);  // nothing runs from them
}

static void test_the_sdk_sequence_settles_at_125_mhz() {
    fresh();
    sdk_clocks();
    CHECK(clocks.clk_sys() == 125 * MHZ && clocks.clk_peri() == 125 * MHZ);
    CHECK(clocks.sys_freq() == 125 * MHZ && clocks.ref_freq() == 12 * MHZ);
}

static void test_a_200_mhz_clk_sys_and_the_order_of_the_sinks() {
    fresh();
    sdk_clocks();  // 125 MHz: no change from the default
    env.n = 0;
    sdk_pll(pll_sys, 100, 6, 1);  // 12 MHz * 100 / 6 = 200 MHz (the PWR write is the one that powers the PLL up)
    // the writes before the last PWR write leave the PLL off or at 125/other values: find the settled state
    CHECK(clocks.clk_sys() == 200 * MHZ);
    int sys_calls = 0;
    for (int i = 0; i < env.n && i < 64; ++i) {
        if (env.calls[i].sink == 's') {
            ++sys_calls;
            CHECK(i + 1 < env.n && env.calls[i + 1].sink == 'S' && env.calls[i + 1].hz == env.calls[i].hz && env.calls[i + 1].old == env.calls[i].old);
        }
    }
    CHECK(sys_calls >= 1);
}

static void test_a_retune_with_no_change_tells_only_the_ref_and_rtc_sinks() {
    fresh();
    sdk_clocks();
    env.n = 0;
    W(PERI_DIV, 0x200);  // stored: no retune
    CHECK(env.n == 0);
    W(SYS_DIV, 0x100);  // same divisor: a retune that changes nothing
    CHECK(env.n == 2);
    CHECK(env.calls[0].sink == 'r' && env.calls[0].hz == 12 * MHZ);  // clk_ref is on the crystal
    CHECK(env.calls[1].sink == 'c' && env.calls[1].hz == 0.0);       // clk_rtc is stopped
}

static void test_a_bare_chips_first_write_follows_the_ring_oscillator() {
    fresh();
    W(SYS_DIV, 0x100);  // clk_sys comes from clk_ref, which is on the ring oscillator until firmware says otherwise
    CHECK(clocks.clk_sys() == 6.5 * MHZ);
    CHECK(env.n == 4 && env.calls[0].sink == 's' && env.calls[0].old == 125 * MHZ && env.calls[1].sink == 'S');
}

static void test_a_stopped_or_unmodelled_source_keeps_the_last_value() {
    fresh();
    sdk_clocks();
    env.n = 0;
    W(SYS_CTRL, 0x1 | (4u << 5));  // AUXSRC GPIN0: not modelled, 0 Hz
    CHECK(clocks.clk_sys() == 125 * MHZ);
    CHECK(env.n == 2 && env.calls[0].sink == 'r');
}

static void test_clk_sys_dividers() {
    fresh();
    sdk_clocks();
    W(SYS_DIV, (2u << 8) | 128);  // 2.5
    CHECK(clocks.clk_sys() == 50 * MHZ);
    W(SYS_DIV, 0);  // INT 0: 2**16
    CHECK(clocks.clk_sys() == 125 * MHZ / 65536.0);
}

static void test_clk_sys_straight_off_the_crystal_and_clk_ref() {
    fresh();
    W(SYS_CTRL, 0x1 | (SYS_AUXSRC_XOSC << 5));
    CHECK(clocks.clk_sys() == 12 * MHZ);
    fresh();
    W(REF_CTRL, REF_SRC_XOSC);
    W(SYS_CTRL, SYS_SRC_REF);
    CHECK(clocks.clk_sys() == 12 * MHZ);
    W(REF_DIV, 2u << 8);
    CHECK(clocks.clk_sys() == 6 * MHZ);
    fresh();
    W(REF_CTRL, REF_SRC_AUX | (REF_AUXSRC_PLL_USB << 5));
    CHECK(clocks.ref_freq() == 0.0);  // PLL_USB is off
    sdk_pll(pll_usb, 100, 5, 5);
    CHECK(clocks.ref_freq() == 48 * MHZ);
}

static void test_a_disabled_crystal_gives_no_clock() {
    fresh();
    env.xosc = 0.0;
    W(REF_CTRL, REF_SRC_XOSC);
    CHECK(clocks.ref_freq() == 0.0);
    W(SYS_CTRL, 0x1 | (SYS_AUXSRC_XOSC << 5));
    CHECK(clocks.clk_sys() == 125 * MHZ);  // 0 keeps the last value
}

static void test_clk_peri_sources() {
    fresh();
    sdk_clocks();
    env.n = 0;
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_XOSC << 5));
    CHECK(clocks.clk_peri() == 12 * MHZ);
    CHECK(env.calls[0].sink == 'p' && env.calls[0].hz == 12 * MHZ && env.calls[0].old == 125 * MHZ);
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_ROSC << 5));
    CHECK(clocks.clk_peri() == 6.5 * MHZ);
    sdk_pll(pll_usb, 100, 5, 5);
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_PLL_USB << 5));
    CHECK(clocks.clk_peri() == 48 * MHZ);
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_PLL_SYS << 5));
    CHECK(clocks.clk_peri() == 125 * MHZ);
    W(PERI_CTRL, GEN_ENABLE | GEN_KILL);  // killed: 0 keeps the last value
    CHECK(clocks.peri_freq() == 0.0 && clocks.clk_peri() == 125 * MHZ);
    W(PERI_CTRL, 0);
    CHECK(clocks.peri_freq() == 0.0);
}

static void test_the_aux_generators() {
    fresh();
    CHECK(clocks.rtc_freq() == 0.0 && clocks.usb_freq() == 0.0 && clocks.adc_freq() == 0.0);  // ENABLE is clear at reset
    W(RTC_CTRL, GEN_ENABLE | (AUX_XOSC << 5));
    W(RTC_DIV, 256u << 8);
    CHECK(clocks.rtc_freq() == 12 * MHZ / 256.0);
    W(RTC_CTRL, GEN_ENABLE | (AUX_ROSC << 5));
    CHECK(clocks.rtc_freq() == 6.5 * MHZ / 256.0);
    W(RTC_CTRL, GEN_ENABLE | GEN_KILL | (AUX_XOSC << 5));
    CHECK(clocks.rtc_freq() == 0.0);
    W(USB_CTRL, GEN_ENABLE | (AUX_XOSC << 5));
    W(USB_DIV, 2u << 8);
    CHECK(clocks.usb_freq() == 6 * MHZ);
    W(ADC_CTRL, GEN_ENABLE | (4u << 5));  // GPIN0
    CHECK(clocks.adc_freq() == 0.0);
    sdk_pll(pll_usb, 100, 5, 5);
    W(ADC_CTRL, GEN_ENABLE | (AUX_PLL_USB << 5));
    CHECK(clocks.adc_freq() == 48 * MHZ);
    W(ADC_CTRL, GEN_ENABLE | (AUX_PLL_SYS << 5));
    CHECK(clocks.adc_freq() == 0.0);  // PLL_SYS is off
    // clk_rtc moves: the rtc sink hears of it with the new rate
    env.n = 0;
    W(RTC_CTRL, GEN_ENABLE | (AUX_XOSC << 5));
    CHECK(env.n == 2 && env.calls[1].sink == 'c' && env.calls[1].hz == 12 * MHZ / 256.0);
    // usb / adc register writes and CLK_PERI_DIV do not retune
    env.n = 0;
    W(USB_CTRL, 0);
    W(ADC_DIV, 0x100);
    CHECK(env.n == 0);
}

static void test_reset_and_the_tree_reset() {
    fresh();
    sdk_clocks();
    sdk_pll(pll_usb, 100, 5, 5);
    W(RTC_CTRL, GEN_ENABLE | (AUX_XOSC << 5));
    W(SYS_CTRL, 0x1 | (SYS_AUXSRC_XOSC << 5));
    CHECK(clocks.clk_sys() == 12 * MHZ);
    W(WAKE_EN0, 0);
    W(WAKE_EN1, 0);
    W(SLEEP_EN0, 0);
    W(SLEEP_EN1, 0);
    W(SYS_RESUS_CTRL, 0);
    W(INTE, 1);
    W(INTF, 1);
    W(FC0_MIN_KHZ, 5);
    W(FC0_MAX_KHZ, 5);
    W(FC0_DELAY, 5);
    W(FC0_INTERVAL, 5);
    clocks.reset();
    CHECK(clocks.read(WAKE_EN0) == 0xFFFFFFFFu && clocks.read(WAKE_EN1) == 0x7FFF && clocks.read(SLEEP_EN0) == 0xFFFFFFFFu && clocks.read(SLEEP_EN1) == 0x7FFF);
    CHECK(clocks.read(SYS_RESUS_CTRL) == 0xFF && clocks.read(INTE) == 0 && clocks.read(INTF) == 0);
    CHECK(clocks.read(FC0_MIN_KHZ) == 0 && clocks.read(FC0_MAX_KHZ) == 0x1FFFFFF && clocks.read(FC0_DELAY) == 1 && clocks.read(FC0_INTERVAL) == 8);
    CHECK(clocks.read(SYS_CTRL) == 0 && clocks.read(RTC_DIV) == 0x100);
    CHECK(clocks.clk_sys() == 12 * MHZ);  // reset() alone is registers only
    env.n = 0;
    CHECK(clocks.reset_tree());
    CHECK(clocks.clk_sys() == 125 * MHZ && clocks.clk_peri() == 125 * MHZ);
    CHECK(pll_usb.read(pll_regs::REG_PWR) == 0x2D);
    // order: sys sinks (s, S), peri sinks (p) only if clk_peri differed, then the ref sink and the rtc sink once
    CHECK(env.calls[0].sink == 's' && env.calls[0].hz == 125 * MHZ && env.calls[0].old == 12 * MHZ);
    CHECK(env.calls[1].sink == 'S');
    CHECK(env.calls[env.n - 2].sink == 'r' && env.calls[env.n - 1].sink == 'c');
    env.n = 0;
    CHECK(clocks.reset_tree());  // already at the default: only the ref and rtc sinks
    CHECK(env.n == 2 && env.calls[0].sink == 'r' && env.calls[1].sink == 'c');
}

static void test_a_failing_sink_stops_the_retune() {
    fresh();
    sdk_clocks();
    env.n = 0;
    env.fail_sink = 1;  // the first sys sink fails
    CHECK(!clocks.write(SYS_CTRL, 0x1 | (SYS_AUXSRC_XOSC << 5)));
    CHECK(env.failed == 1 && env.n == 1);
    CHECK(clocks.clk_sys() == 12 * MHZ);  // the value was already taken
}

static void test_a_sink_gets_its_own_context() {
    fresh();
    static int token = 42;
    ctx_seen = 0;
    CHECK(clocks.add_rtc_sink(on_ctx, &token));
    W(SYS_DIV, 0x100);
    CHECK(ctx_seen == 42);
}

static void test_a_failing_peri_ref_or_rtc_sink_stops_the_retune() {
    fresh();
    sdk_clocks();
    env.n = 0;
    env.fail_sink = 3;  // s, S, then the peri sink
    clocks.write(SYS_CTRL, 0x1 | (SYS_AUXSRC_XOSC << 5));
    fresh();
    sdk_clocks();
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_XOSC << 5));  // clk_peri 12 MHz
    env.n = 0;
    env.fail_sink = 1;  // the peri sink is the first call of this retune
    CHECK(!clocks.write(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_CLK_SYS << 5)));
    CHECK(env.n == 1 && env.calls[0].sink == 'p');
    fresh();
    sdk_clocks();
    env.n = 0;
    env.fail_sink = 1;  // no clock changes: the ref sink is the first call
    CHECK(!clocks.write(SYS_DIV, 0x100));
    CHECK(env.n == 1 && env.calls[0].sink == 'r');
    fresh();
    sdk_clocks();
    env.n = 0;
    env.fail_sink = 2;
    CHECK(!clocks.write(SYS_DIV, 0x100));
    CHECK(env.n == 2 && env.calls[1].sink == 'c');
}

static void test_reset_tree_stops_at_a_failing_sink() {
    fresh();
    sdk_clocks();
    W(SYS_CTRL, 0x1 | (SYS_AUXSRC_XOSC << 5));
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_ROSC << 5));
    clocks.reset();
    env.n = 0;
    env.fail_sink = 1;  // the first sys sink
    CHECK(!clocks.reset_tree());
    CHECK(env.n == 1);
    fresh();
    sdk_clocks();
    W(PERI_CTRL, GEN_ENABLE | (PERI_AUXSRC_ROSC << 5));  // clk_peri 6.5 MHz, clk_sys stays 125 MHz
    clocks.reset();
    env.n = 0;
    env.fail_sink = 1;  // the peri sink is the first call: clk_sys is already at the default
    CHECK(!clocks.reset_tree());
    CHECK(env.n == 1 && env.calls[0].sink == 'p');
    CHECK(clocks.clk_peri() == 125 * MHZ);
}

static void test_a_parked_failure_stops_an_alias_write() {
    fresh();
    env.failed = 1;
    const uint32_t before = clocks.read(SYS_DIV);
    CHECK(!clocks.write_atomic(SYS_DIV, 0x1, kAtomicSet));
    CHECK(clocks.read(SYS_DIV) == before);
}

static void test_sink_lists_are_bounded() {
    fresh();
    for (uint32_t i = 0; i < kClockSinkCapacity - 2; ++i) CHECK(clocks.add_sys_sink(on_sys, nullptr));  // two are there already
    CHECK(clocks.add_sys_sink(on_sys, nullptr) == false);
}

// ---- the frequency counter ----
static void fc_run(uint32_t src, uint32_t ref_khz = 12000) {
    W(FC0_REF_KHZ, ref_khz);
    W(FC0_SRC, src);
}

static void test_a_count_runs_for_the_interval() {
    fresh();
    fc_run(FC0_SRC_XOSC);  // the default interval 8: 250.88 us, after one reference period of delay
    CHECK(clk.tick(1e6 / 12000 + 250880 - 1));
    CHECK(clocks.read(FC0_STATUS) == FC0_STATUS_RUNNING);
    CHECK(clk.tick(2));  // the alarm lets time go on
    CHECK(clocks.read(FC0_STATUS) == (FC0_STATUS_DONE | FC0_STATUS_PASS));
    CHECK(clocks.read(FC0_RESULT) == 12000u << 5);
    fc_run(FC0_SRC_ROSC);
    clk.tick(400000);
    CHECK(clocks.read(FC0_RESULT) >> 5 == 6500 && (clocks.read(FC0_RESULT) & 31) == 0);
}

static void test_every_source_of_the_counter() {
    fresh();
    sdk_clocks();
    sdk_pll(pll_usb, 100, 5, 5);
    const struct {
        uint32_t src;
        uint32_t khz;
    } cases[] = {{FC0_SRC_PLL_SYS, 125000}, {FC0_SRC_PLL_USB, 48000}, {FC0_SRC_ROSC, 6500}, {FC0_SRC_ROSC_PH, 6500}, {FC0_SRC_XOSC, 12000}, {FC0_SRC_CLK_REF, 12000},
                 {FC0_SRC_CLK_SYS, 125000}, {FC0_SRC_CLK_PERI, 125000}};
    for (const auto& c : cases) {
        fc_run(c.src);
        clk.tick(400000);
        CHECK(clocks.read(FC0_STATUS) == (FC0_STATUS_DONE | FC0_STATUS_PASS));
        CHECK(clocks.read(FC0_RESULT) >> 5 == c.khz);
    }
    W(USB_CTRL, GEN_ENABLE | (AUX_PLL_USB << 5));
    fc_run(FC0_SRC_CLK_USB);
    clk.tick(400000);
    CHECK(clocks.read(FC0_RESULT) >> 5 == 48000);
    W(ADC_CTRL, GEN_ENABLE | (AUX_XOSC << 5));
    fc_run(FC0_SRC_CLK_ADC);
    clk.tick(400000);
    CHECK(clocks.read(FC0_RESULT) >> 5 == 12000);
    W(RTC_CTRL, GEN_ENABLE | (AUX_XOSC << 5));
    W(RTC_DIV, 256u << 8);
    fc_run(FC0_SRC_CLK_RTC);
    clk.tick(400000);
    CHECK(clocks.read(FC0_RESULT) >> 5 == 46 && (clocks.read(FC0_RESULT) & 31) == 28);  // 46.875 kHz
}

static void test_a_stopped_clock_died() {
    fresh();
    const uint32_t srcs[] = {0, 0x06, 0x7F, FC0_SRC_PLL_SYS};  // NULL, GPIN0, nothing, a PLL that is off
    for (uint32_t src : srcs) {
        fc_run(src);
        clk.tick(400000);
        CHECK(clocks.read(FC0_STATUS) == (FC0_STATUS_DONE | FC0_STATUS_DIED | FC0_STATUS_FAIL) && clocks.read(FC0_RESULT) == 0);
    }
}

static void test_min_and_max() {
    fresh();
    W(FC0_REF_KHZ, 12000);
    auto run = [](uint32_t mn, uint32_t mx) {
        W(FC0_MIN_KHZ, mn);
        W(FC0_MAX_KHZ, mx);
        W(FC0_SRC, FC0_SRC_XOSC);
        clk.tick(300000);
        return clocks.read(FC0_STATUS);
    };
    CHECK(run(11000, 13000) == (FC0_STATUS_DONE | FC0_STATUS_PASS));
    CHECK(run(13000, 20000) == (FC0_STATUS_DONE | FC0_STATUS_SLOW | FC0_STATUS_FAIL));
    CHECK(run(0, 11000) == (FC0_STATUS_DONE | FC0_STATUS_FAST | FC0_STATUS_FAIL));
    CHECK(run(12000, 12000) == (FC0_STATUS_DONE | FC0_STATUS_PASS));  // the limits are inclusive
}

static void test_a_clock_that_starts_or_ends_stopped_died() {
    fresh();
    W(FC0_REF_KHZ, 12000);
    W(RTC_CTRL, GEN_ENABLE | (AUX_XOSC << 5));
    W(FC0_SRC, FC0_SRC_CLK_RTC);
    W(RTC_CTRL, GEN_ENABLE | GEN_KILL);  // running at the start, stopped by the end
    clk.tick(400000);
    CHECK(clocks.read(FC0_STATUS) == (FC0_STATUS_DONE | FC0_STATUS_DIED | FC0_STATUS_FAIL));
    W(RTC_CTRL, 0);
    W(FC0_SRC, FC0_SRC_CLK_RTC);  // stopped at the start
    W(RTC_CTRL, GEN_ENABLE | (AUX_XOSC << 5));  // running at the end
    clk.tick(400000);
    CHECK(clocks.read(FC0_STATUS) == (FC0_STATUS_DONE | FC0_STATUS_DIED | FC0_STATUS_FAIL));
    // a start clears the previous result
    fc_run(FC0_SRC_XOSC);
    clk.tick(400000);
    CHECK(clocks.read(FC0_RESULT) == 12000u << 5);
    W(FC0_SRC, FC0_SRC_XOSC);
    CHECK(clocks.read(FC0_STATUS) == FC0_STATUS_RUNNING && clocks.read(FC0_RESULT) == 0);
}

static void test_a_new_count_replaces_the_one_in_progress_and_reset_clears_it() {
    fresh();
    fc_run(FC0_SRC_XOSC);
    clk.tick(100000);
    CHECK(clocks.read(FC0_STATUS) == FC0_STATUS_RUNNING);
    W(FC0_SRC, FC0_SRC_ROSC);  // restarts: the first alarm is gone
    clk.tick(200000);
    CHECK(clocks.read(FC0_STATUS) == FC0_STATUS_RUNNING);
    clk.tick(100000);
    CHECK(clocks.read(FC0_STATUS) == (FC0_STATUS_DONE | FC0_STATUS_PASS));
    fc_run(FC0_SRC_XOSC);
    clocks.reset();
    CHECK(clocks.read(FC0_STATUS) == 0 && clocks.read(FC0_SRC) == 0 && clocks.read(FC0_REF_KHZ) == 0);
    clk.tick(1000000);
    CHECK(clocks.read(FC0_STATUS) == 0);  // the cancelled alarm never fired
    W(FC0_STATUS, 0xFFFFFFFFu);
    W(FC0_RESULT, 0xFFFFFFFFu);
    CHECK(clocks.read(FC0_STATUS) == 0 && clocks.read(FC0_RESULT) == 0);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(clocks.read(0xC8) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kClocksWarnRead && env.warn_offset[0] == 0xC8);
    W(0xCC, 7);
    CHECK(env.warns == 2 && env.warn_kind[1] == kClocksWarnWrite && env.warn_value[1] == 7);
    fresh();
    CHECK(clocks.read(0x1008) == 0xFFFFFFFFu);
    CHECK(env.warns == 2 && env.warn_kind[1] == kClocksWarnReadAtomicArea);
    fresh();
    CHECK(clocks.read(0x1000) == 0xFFFFFFFFu);  // the atomic area starts above 0x1000
    CHECK(env.warns == 1);
}

static void test_atomic_aliases_decode_against_a_read() {
    fresh();
    clocks.write_atomic(SYS_DIV, 0x300, kAtomicNormal);
    clocks.write_atomic(SYS_DIV, 0x0F, kAtomicSet);
    CHECK(clocks.read(SYS_DIV) == 0x30F);
    clocks.write_atomic(SYS_DIV, 0x100, kAtomicClear);
    CHECK(clocks.read(SYS_DIV) == 0x20F);
    clocks.write_atomic(SYS_DIV, 0x1, kAtomicXor);
    CHECK(clocks.read(SYS_DIV) == 0x20E);
    CHECK(clocks.raw_write_value() == 1);
}

// ---- the oscillators as blocks ----
static bool rosc_freq_host(void*, double) { return true; }
static bool rosc_bit_host(void*, uint32_t* bit) {
    *bit = 0;
    return true;
}

static void test_the_tree_reads_the_oscillators_from_their_blocks() {
    fresh();
    XoscBlock xosc;
    XoscHost xhost;
    xosc.init(&test_ports(), kTestBlock, xhost);
    RoscBlock rosc;
    RoscHost rhost;
    rhost.frequency_changed = rosc_freq_host;
    rhost.random_bit = rosc_bit_host;
    rosc.init(&clk, &test_ports(), kTestBlock, rhost);

    CHECK(clocks.xosc_hz() == 12 * MHZ && clocks.rosc_hz() == 6.5 * MHZ);  // (the host's answers)
    clocks.bind_xosc(&xosc);
    clocks.bind_rosc(&rosc);
    CHECK(clocks.xosc_hz() == 0.0);  // a crystal that has not been enabled delivers nothing
    CHECK(xosc.write(xosc_regs::REG_CTRL, (xosc_regs::CTRL_ENABLE_ENABLE << xosc_regs::CTRL_ENABLE_LSB) | xosc_regs::CTRL_FREQ_RANGE_1_15MHZ));
    CHECK(clocks.xosc_hz() == 12 * MHZ && clocks.crystal_hz() == 12 * MHZ);
    clocks.set_crystal_hz(24 * MHZ);  // the board's crystal
    CHECK(clocks.xosc_hz() == 24 * MHZ);
    CHECK(xosc.write(xosc_regs::REG_DORMANT, xosc_regs::DORMANT_VALUE));
    CHECK(clocks.xosc_hz() == 0.0);  // dormant: stopped
    CHECK(xosc.write(xosc_regs::REG_DORMANT, xosc_regs::WAKE_VALUE));
    CHECK(clocks.xosc_hz() == 24 * MHZ);

    CHECK(clocks.rosc_hz() == rosc.hz && rosc.hz == 6.5 * MHZ);
    CHECK(rosc.write(rosc_regs::REG_DIV, rosc_regs::DIV_PASS + 8));  // div 8: twice as fast
    CHECK(clocks.rosc_hz() == rosc.hz && rosc.hz == 13 * MHZ);

    // the frequency counter and the generators see the blocks' values
    W(FC0_REF_KHZ, 12000);
    W(FC0_SRC, FC0_SRC_ROSC);
    clk.tick(400000);
    CHECK((clocks.read(FC0_RESULT) >> 5) == 13000);
    W(FC0_SRC, FC0_SRC_XOSC);
    clk.tick(400000);
    CHECK((clocks.read(FC0_RESULT) >> 5) == 24000);

    // taken back: the host answers again
    clocks.bind_xosc(nullptr);
    clocks.bind_rosc(nullptr);
    CHECK(clocks.xosc_hz() == 12 * MHZ && clocks.rosc_hz() == 6.5 * MHZ);
    rosc.detach();
}

static void test_the_oscillators_tell_the_tree_directly() {
    fresh();
    sdk_clocks();  // clk_ref on the crystal, clk_sys on PLL_SYS, clk_peri on clk_sys
    XoscBlock xosc;
    XoscHost xhost;
    xosc.init(&test_ports(), kTestBlock, xhost);
    RoscBlock rosc;
    RoscHost rhost;
    rhost.frequency_changed = rosc_freq_host;
    rhost.random_bit = rosc_bit_host;
    rosc.init(&clk, &test_ports(), kTestBlock, rhost);
    clocks.bind_xosc(&xosc);
    clocks.bind_rosc(&rosc);
    xosc.bind_stable_listener(clocks_retune, &clocks);
    rosc.bind_frequency_listener(clocks_retune_hz, &clocks);
    pll_sys.bind_reference(clocks_xosc_hz, &clocks);

    // the PLL was set up before there was a block: its reference is the tree's crystal, which is not running
    CHECK(pll_sys.reference() == 0.0 && pll_sys.frequency() == 0.0);
    env.n = 0;
    CHECK(xosc.write(xosc_regs::REG_CTRL, (xosc_regs::CTRL_ENABLE_ENABLE << xosc_regs::CTRL_ENABLE_LSB) | xosc_regs::CTRL_FREQ_RANGE_1_15MHZ));
    CHECK(pll_sys.frequency() == 125 * MHZ && clocks.ref_freq() == 12 * MHZ);  // the crystal started: the PLL and clk_ref have a reference
    CHECK(env.n >= 2 && env.calls[env.n - 2].sink == 'r' && env.calls[env.n - 1].sink == 'c');  // and the tree was re-derived (the ref and rtc sinks hear of every retune)

    // the ring oscillator changing moves what runs from it
    W(SYS_CTRL, 0);  // clk_sys from clk_ref ... which is on the crystal
    W(REF_CTRL, REF_SRC_ROSC);
    CHECK(clocks.clk_sys() == 6.5 * MHZ);
    env.n = 0;
    CHECK(rosc.write(rosc_regs::REG_DIV, rosc_regs::DIV_PASS + 8));
    CHECK(clocks.clk_sys() == 13 * MHZ);
    CHECK(env.n >= 3 && env.calls[0].sink == 's');

    // a failing sink is reported through the oscillator's write
    env.fail_sink = env.n + 1;
    CHECK(!rosc.write(rosc_regs::REG_DIV, rosc_regs::DIV_PASS + 16));
    env.fail_sink = 0;
    env.failed = 0;

    xosc.bind_stable_listener(nullptr, nullptr);
    rosc.bind_frequency_listener(nullptr, nullptr);
    pll_sys.bind_reference(nullptr, nullptr);
    clocks.bind_xosc(nullptr);
    clocks.bind_rosc(nullptr);
    rosc.detach();
}

int main() {
    test_defaults();
    test_register_masks();
    test_gpout_registers_are_stored();
    test_the_tree_reads_the_oscillators_from_their_blocks();
    test_the_oscillators_tell_the_tree_directly();
    test_the_sdk_sequence_settles_at_125_mhz();
    test_a_200_mhz_clk_sys_and_the_order_of_the_sinks();
    test_a_retune_with_no_change_tells_only_the_ref_and_rtc_sinks();
    test_a_bare_chips_first_write_follows_the_ring_oscillator();
    test_a_stopped_or_unmodelled_source_keeps_the_last_value();
    test_clk_sys_dividers();
    test_clk_sys_straight_off_the_crystal_and_clk_ref();
    test_a_disabled_crystal_gives_no_clock();
    test_clk_peri_sources();
    test_the_aux_generators();
    test_reset_and_the_tree_reset();
    test_a_failing_sink_stops_the_retune();
    test_a_sink_gets_its_own_context();
    test_a_failing_peri_ref_or_rtc_sink_stops_the_retune();
    test_reset_tree_stops_at_a_failing_sink();
    test_a_parked_failure_stops_an_alias_write();
    test_sink_lists_are_bounded();
    test_a_clock_that_starts_or_ends_stopped_died();
    test_a_count_runs_for_the_interval();
    test_every_source_of_the_counter();
    test_a_stopped_clock_died();
    test_min_and_max();
    test_a_new_count_replaces_the_one_in_progress_and_reset_clears_it();
    test_unimplemented_offsets_warn();
    test_atomic_aliases_decode_against_a_read();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("clocks: all checks passed\n");
    return 0;
}
