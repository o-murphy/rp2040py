// Standalone checks of src/rp2040py/native/core/adc.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_adc.py) that the lockstep differential (tests/test_adc_diff.py) also pins, against a real C++ `Clock` and a
// recording host whose device is the reference's default (the sample comes out of `channel_values` after `sample_time`), immediate (completes from inside the callback), deferred
// (the test completes later) or silent, with failure injection per host function.
#include <cstdio>

#include "adc.hpp"

using namespace rp2040core;
using namespace rp2040core::adc_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

enum Mode { kDefault = 0, kImmediate = 1, kDeferred = 2, kSilent = 3 };

static AdcBlock adc;
static Clock clk;

struct Env {
    bool irq_levels[512];
    double irq_times[512];
    int irq_n = 0;
    bool dreq_levels[512];
    int dreq_n = 0;
    uint32_t reads[256];
    int reads_n = 0;
    uint32_t warn_kind[32], warn_offset[32];
    int64_t warn_value[32];
    int warns = 0;
    int mode = kDefault;
    int64_t values[8] = {0x111, 0x222, 0x333, 0x444, 0x555, 0, 0, 0};
    int depth = 0;
    int64_t next_value = 0x100;
    bool next_error = false;
    int failed = 0;
    bool fail_irq = false, fail_dreq = false, fail_read = false;
};
static Env env;

static bool on_irq(void*, bool level) {
    if (env.irq_n < 512) {
        env.irq_levels[env.irq_n] = level;
        env.irq_times[env.irq_n] = clk.nanos();
    }
    ++env.irq_n;
    if (env.fail_irq) { env.failed = 1; return false; }
    return true;
}
static bool on_dreq(void*, bool asserted) {
    if (env.dreq_n < 512) env.dreq_levels[env.dreq_n] = asserted;
    ++env.dreq_n;
    if (env.fail_dreq) { env.failed = 1; return false; }
    return true;
}
static bool on_read(void*, uint32_t channel) {
    if (env.reads_n < 256) env.reads[env.reads_n] = channel;
    ++env.reads_n;
    if (env.fail_read) { env.failed = 1; return false; }
    switch (env.mode) {
        case kDefault: adc.default_adc_read(channel); return true;
        case kImmediate:
            if (env.depth < 3) {
                ++env.depth;
                const bool ok = adc.complete_adc_read(env.next_value++, env.next_error);
                --env.depth;
                return ok;
            }
            return true;
        default: return true;
    }
}
static bool on_channel_value(void*, uint32_t channel, int64_t* value) {
    if (channel >= 5) { env.failed = 1; return false; }  // the reference's list has 5 entries: an IndexError
    *value = env.values[channel];
    return true;
}
static void on_warn(void*, uint32_t kind, uint32_t offset, int64_t value) {
    if (env.warns < 32) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}

static void fresh(int mode = kDefault) {
    adc.detach();
    env = Env();
    env.mode = mode;
    clk = Clock();
    AdcHost host;
    host.irq = on_irq;
    host.dreq = on_dreq;
    host.read = on_read;
    host.channel_value = on_channel_value;
    host.warn = on_warn;
    host.failed = &env.failed;
    adc.init(&clk, host);
    adc.num_channels = 5;
    adc.sample_time = 2;
    (void)adc.reset();
    (void)adc.write_atomic(CS, 0, kAtomicNormal);  // raw_write_value back to 0
    env.irq_n = env.dreq_n = env.reads_n = env.warns = 0;
}
static uint32_t rd(uint32_t offset) { return adc.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { return adc.write_atomic(offset, value, atomic); }
static bool tick(double ns) { return clk.tick(ns); }
static bool last_irq() { return env.irq_n > 0 && env.irq_levels[env.irq_n - 1]; }
static bool last_dreq() { return env.dreq_n > 0 && env.dreq_levels[env.dreq_n - 1]; }

static void test_a_default_constructed_block_is_at_power_on() {
    static AdcBlock block;
    CHECK(block.cs == 0 && block.fcs == 0 && block.clock_div == 0 && block.int_enable == 0 && block.int_force == 0 && block.result == 0);
    CHECK(!block.busy && !block.err && block.current_channel == 0 && block.num_channels == 5 && block.sample_time == 2 && block.fifo.empty() && block.raw_write_value() == 0);
}

static void test_a_fresh_block_is_at_power_on() {
    fresh();
    adc.busy = true;
    CHECK(rd(CS) == 0);                                              // busy: READY is clear (and the block is not enabled)
    adc.busy = false;
    CHECK(rd(CS) == CS_READY && rd(RESULT) == 0 && rd(FCS) == FCS_EMPTY && rd(DIV) == 0 && rd(INTR) == FIFO_INT && rd(INTE) == 0 && rd(INTF) == 0 && rd(INTS) == 0);
    CHECK(adc.num_channels == 5 && adc.sample_time == 2 && adc.current_channel == 0 && !adc.busy && !adc.err && adc.fifo.empty());
    CHECK(adc.divider() == 1.0 && !adc.enabled() && !adc.temperature_enable() && adc.active_channel() == 0);
    CHECK(env.warns == 0 && !adc.failed());
}

static void test_a_conversion_by_the_default_device_takes_sample_time() {
    fresh();
    wr(FCS, FCS_EN);
    env.irq_n = 0;
    wr(CS, CS_EN | CS_START_ONE);
    CHECK(env.reads_n == 1 && env.reads[0] == 0 && adc.busy && (rd(CS) & CS_READY) == 0);
    CHECK(tick(1999) && adc.busy && adc.fifo.empty());
    CHECK(tick(1) && !adc.busy && rd(RESULT) == 0x111 && adc.fifo.count() == 1 && (rd(CS) & CS_READY) != 0);
    CHECK((adc.cs & (CS_ERR | CS_ERR_STICKY)) == 0);                // a sample from the default device is not an error
    CHECK(rd(FCS) == (FCS_EN | (1u << FCS_LEVEL_SHIFT)));
    CHECK(rd(FIFO) == 0x111 && adc.fifo.empty());
    CHECK(clk.nanos() == 2000.0);
    CHECK(!clk.has_alarm());
    // the sample time is read when the conversion starts
    adc.sample_time = 1;
    wr(CS, CS_EN | CS_START_ONE);
    CHECK(clk.nanos_to_next_alarm() == 1000.0);
    adc.sample_time = 0.5;
    CHECK(tick(1000) && rd(RESULT) == 0x111);
}

static void test_the_channel_is_selected_by_ainsel_and_a_bad_one_fails_in_the_alarm() {
    fresh();
    wr(CS, CS_EN | CS_START_ONE | (3u << CS_AINSEL_SHIFT));
    CHECK(env.reads[0] == 3 && adc.current_channel == 3 && adc.active_channel() == 3);
    CHECK(tick(2000) && rd(RESULT) == 0x444);
    wr(CS, CS_EN | CS_START_ONE | (6u << CS_AINSEL_SHIFT));
    CHECK(env.reads[1] == 6 && adc.current_channel == 6);
    CHECK(!tick(2000) && adc.failed() && adc.busy);                // the list has no entry 6: the clock stops at the alarm's time
    CHECK(clk.nanos() == 4000.0);
    env.failed = 0;
}

static void test_a_start_needs_enable_and_an_idle_block() {
    fresh();
    wr(CS, CS_START_ONE);                                          // not enabled
    CHECK(env.reads_n == 0 && !adc.busy);
    wr(CS, CS_EN | CS_START_ONE);
    wr(CS, CS_EN | CS_START_ONE);                                  // busy: ignored
    CHECK(env.reads_n == 1);
    wr(CS, CS_EN | CS_START_MANY | CS_START_ONE);
    CHECK(env.reads_n == 1);
    fresh();
    CHECK(wr(CS, CS_EN | CS_TS_EN));                               // no start bit
    CHECK(env.reads_n == 0 && adc.temperature_enable() && adc.enabled());
    wr(CS, 0xFFFFFFFFu);                                           // the write mask: ERR_STICKY, ERR and READY are not CS_WRITE bits
    CHECK((adc.cs & ~CS_WRITE_MASK) == 0 && adc.enabled());
}

static void test_the_fifo_shift_error_overflow_and_underflow() {
    fresh(kDeferred);
    wr(FCS, FCS_EN);
    CHECK(adc.complete_adc_read(0xABCD, false));                   // 12 bits kept
    CHECK(rd(FIFO) == 0xBCD);
    CHECK(adc.complete_adc_read(0x1ABC, false) && rd(FIFO) == 0xABC);
    CHECK(wr(FCS, FCS_EN | FCS_SHIFT));
    CHECK(adc.complete_adc_read(0xABC, false) && rd(FIFO) == 0xAB);
    wr(FCS, FCS_EN | FCS_ERR);
    CHECK(adc.complete_adc_read(0x123, true) && rd(FIFO) == (FIFO_ERR | 0x123));
    wr(FCS, FCS_EN);                                               // an error without FCS.ERR is not recorded in the FIFO
    CHECK(adc.complete_adc_read(0x123, true) && rd(FIFO) == 0x123);
    for (uint32_t i = 0; i < 4; ++i) CHECK(adc.complete_adc_read(i + 1, false));
    CHECK((rd(FCS) & (FCS_FULL | FCS_OVER)) == FCS_FULL && (rd(FCS) >> FCS_LEVEL_SHIFT & 0xF) == 4);
    CHECK(adc.complete_adc_read(9, false) && (adc.fcs & FCS_OVER) != 0 && adc.fifo.count() == 4 && rd(RESULT) == 9);
    wr(FCS, FCS_EN | FCS_OVER);                                    // write-clear: writing 1 clears OVER and the value is not stored
    CHECK((adc.fcs & FCS_OVER) == 0);
    for (int i = 0; i < 4; ++i) (void)rd(FIFO);
    CHECK(rd(FIFO) == 0 && (adc.fcs & FCS_UNDER) != 0);
    wr(FCS, FCS_EN | FCS_UNDER);
    CHECK((adc.fcs & FCS_UNDER) == 0);
    fresh(kDeferred);                                              // no FIFO enable: nothing is stored, the result is
    CHECK(adc.complete_adc_read(0x55, false) && adc.fifo.empty() && rd(RESULT) == 0x55);
}

static void test_error_flags_in_cs_and_the_sticky_clear() {
    fresh(kDeferred);
    CHECK(adc.complete_adc_read(1, true));
    CHECK((adc.cs & (CS_ERR | CS_ERR_STICKY)) == (CS_ERR | CS_ERR_STICKY));
    CHECK(adc.complete_adc_read(1, false));
    CHECK((adc.cs & CS_ERR) == 0 && (adc.cs & CS_ERR_STICKY) != 0);   // ERR follows the last conversion, STICKY stays
    adc.fcs = FCS_UNDER | FCS_EN;
    wr(CS, CS_ERR_STICKY);                                          // write-clear: the sticky bit goes ...
    CHECK((adc.cs & CS_ERR_STICKY) == 0);
    CHECK(adc.fcs == (FCS_UNDER | FCS_EN));                         // ... and FCS (whose bit 10 is UNDER) is not touched
    CHECK(adc.complete_adc_read(1, true) && (adc.cs & CS_ERR_STICKY) != 0);
    wr(CS, 0);                                                      // a write without the bit leaves it
    CHECK((adc.cs & CS_ERR_STICKY) != 0);
    adc.err = true;
    CHECK((rd(CS) & CS_ERR) != 0);                                  // the `err` member shows in CS as well
}

static void test_interrupts_and_the_line() {
    fresh(kDeferred);
    wr(FCS, FCS_EN | (2u << FCS_THRESH_SHIFT));
    CHECK(rd(INTR) == 0 && rd(INTS) == 0);
    CHECK(wr(INTE, 0xFFFFFFFFu));
    CHECK(rd(INTE) == 1);
    CHECK(adc.complete_adc_read(1, false) && rd(INTR) == 0 && !last_irq());
    CHECK(adc.complete_adc_read(2, false) && rd(INTR) == 1 && rd(INTS) == 1 && last_irq());
    (void)rd(FIFO);
    CHECK(rd(INTR) == 0 && last_irq());                    // a FIFO read does not touch the line: it is only recomputed at the next event
    CHECK(adc.check_interrupts() && !last_irq());
    CHECK(wr(INTF, 0xFFFFFFFFu));
    CHECK(rd(INTF) == 1 && rd(INTS) == 1 && last_irq());
    wr(INTE, 0);
    CHECK(rd(INTS) == 1 && last_irq());                             // forced
    wr(INTF, 0);
    CHECK(rd(INTS) == 0 && !last_irq());
    wr(FCS, FCS_EN);                                                // a FCS write re-evaluates the line
    wr(INTE, 1);
    CHECK(rd(INTR) == 1 && last_irq());                             // threshold 0 is always raised
}

static void test_dreq_follows_the_threshold_when_enabled() {
    fresh(kDeferred);
    wr(FCS, FCS_EN | FCS_DREQ_EN | (2u << FCS_THRESH_SHIFT));
    CHECK(env.dreq_n == 0);                                         // an FCS write does not touch the DREQ
    CHECK(adc.complete_adc_read(1, false) && env.dreq_n == 1 && !last_dreq());
    CHECK(adc.complete_adc_read(2, false) && env.dreq_n == 2 && last_dreq());
    (void)rd(FIFO);
    CHECK(env.dreq_n == 3 && !last_dreq());
    (void)rd(FIFO);
    CHECK(env.dreq_n == 4 && !last_dreq());
    (void)rd(FIFO);                                                 // empty: no pull, no DREQ update
    CHECK(env.dreq_n == 4);
    wr(FCS, FCS_EN | (2u << FCS_THRESH_SHIFT));                     // DREQ_EN off: the DREQ is left alone
    CHECK(adc.complete_adc_read(1, false) && adc.complete_adc_read(2, false) && env.dreq_n == 4);
}

static void test_round_robin_with_the_references_setter_quirk() {
    fresh(kDeferred);
    wr(CS, CS_EN | (0x1Fu << CS_RROBIN_SHIFT));
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 0);      // 0 -> 1, stored as 1 & 12 = 0
    wr(CS, CS_EN | (0x10u << CS_RROBIN_SHIFT));
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 4);      // 0 -> 1 2 3 4: found, 4 & 12 = 4
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 4);      // 4 -> 5 (no bit) -> 1 2 3 4: again 4
    wr(CS, CS_EN | (0x03u << CS_RROBIN_SHIFT) | (4u << CS_AINSEL_SHIFT));
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 0);      // 4 -> 5 (no) -> 1 (bit 1): 1 & 12 = 0
    CHECK((adc.cs & (1u << 12)) == 0);
    wr(CS, CS_EN | (0x12u << CS_RROBIN_SHIFT));
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 0);      // the first candidate is active + 1 = 1, a set bit: stored as 1 & 12 = 0
    wr(CS, CS_EN | (0x11u << CS_RROBIN_SHIFT));
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 4);      // 0 -> 1 .. 4
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 4);      // 4 -> 5 (no bit) -> (5 + 1) % 5 = 1, 2, 3, 4: channel 0 is skipped
    // the setter's stray bits: 12 & channel can set bits above the field when the channel is 8..15 - unreachable with 5 channels, reachable with more
    adc.num_channels = 16;
    wr(CS, CS_EN | (0x10u << CS_RROBIN_SHIFT));
    CHECK(adc.complete_adc_read(1, false));
    // a channel the block does not have: the search gives up after a turn instead of looping for ever
    fresh(kDeferred);
    adc.num_channels = 3;
    wr(CS, CS_EN | (0x10u << CS_RROBIN_SHIFT));
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 0);
    adc.num_channels = 0;                                           // treated as 1
    CHECK(adc.complete_adc_read(1, false) && adc.active_channel() == 0);
    adc.num_channels = -4;
    CHECK(adc.complete_adc_read(1, false));
}

static void test_a_free_running_capture_and_its_divider() {
    fresh();
    // divider 1 (DIV = 0) <= 96 ticks: the next conversion starts at once (from the sample alarm: every 2000 ns)
    wr(FCS, FCS_EN);
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(tick(6000) && adc.fifo.count() == 3 && env.reads_n == 4);
    CHECK(clk.has_alarm());
    // the divider exactly equal to the sample time (96 ticks: DIV = 0x5F00) restarts at once, too
    fresh();
    wr(DIV, 0x5F00);
    CHECK(adc.divider() == 96.0);
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(tick(4000) && env.reads_n == 3);
    fresh(kDeferred);                                                // at once means inside the completion, not from an alarm
    wr(DIV, 0x5F00);
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(env.reads_n == 1 && adc.complete_adc_read(1, false) && env.reads_n == 2 && !clk.has_alarm());
    // above it: the capture waits the difference (97.5 - 96) / 48 us = 31.25 ns
    fresh();
    wr(DIV, 0x6080);
    CHECK(adc.divider() == 97.5);
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(tick(2000) && env.reads_n == 1 && clk.has_alarm() && clk.nanos_to_next_alarm() == 31.25);
    CHECK(tick(31.25) && env.reads_n == 2);
    // clearing START_MANY while the gap runs: the alarm fires and does nothing
    fresh();
    wr(DIV, 0x6080);
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(tick(2000) && clk.has_alarm());
    wr(CS, CS_EN);
    CHECK(tick(100) && env.reads_n == 1 && !clk.has_alarm());
    // a larger sample time moves the threshold
    fresh();
    adc.sample_time = 3;
    wr(DIV, 0x6080);                                                 // 97.5 <= 144: restarts at once
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(tick(6000) && env.reads_n == 3);
    // the integer part is 16 bits and the fraction 8
    wr(DIV, 0xFFFFFF00u);
    CHECK(adc.divider() == 1.0 + 0xFFFF);
    wr(DIV, 0x000000FFu);
    CHECK(adc.divider() == 1.0 + 255.0 / 256.0);
}

static void test_an_immediate_device_completes_from_inside_the_callback() {
    fresh(kImmediate);
    wr(FCS, FCS_EN);
    wr(CS, CS_EN | CS_START_ONE);
    CHECK(env.reads_n == 1 && !adc.busy && adc.fifo.count() == 1 && rd(RESULT) == 0x100);
    wr(CS, CS_EN | CS_START_MANY);                                   // divider <= ticks and an immediate device: the chain runs (depth-limited by the host)
    CHECK(env.reads_n == 5 && adc.fifo.count() == 4 && (adc.fcs & FCS_OVER) == 0);
    fresh(kImmediate);
    env.next_error = true;
    wr(CS, CS_EN | CS_START_ONE);
    CHECK((adc.cs & CS_ERR_STICKY) != 0);
}

static void test_a_silent_device_leaves_the_block_busy_until_a_completion() {
    fresh(kSilent);
    wr(CS, CS_EN | CS_START_ONE);
    CHECK(adc.busy && !clk.has_alarm());
    CHECK(adc.complete_adc_read(7, false) && !adc.busy && rd(RESULT) == 7);
    CHECK(adc.complete_adc_read(8, false) && rd(RESULT) == 8);       // a spurious completion is accepted
}

static void test_reset_clears_the_state_the_alarms_and_republishes() {
    fresh();
    wr(FCS, FCS_EN | FCS_DREQ_EN | (3u << FCS_THRESH_SHIFT));
    wr(DIV, 0x1234);
    wr(INTE, 1);
    wr(INTF, 1);
    wr(CS, CS_EN | CS_START_ONE | (2u << CS_AINSEL_SHIFT));
    adc.err = true;
    adc.result = 5;
    adc.fifo.push(1);
    CHECK(clk.has_alarm());
    env.irq_n = env.dreq_n = 0;
    CHECK(adc.reset());
    CHECK(rd(CS) == CS_READY && rd(FCS) == FCS_EMPTY && rd(DIV) == 0 && rd(INTE) == 0 && rd(INTF) == 0 && rd(RESULT) == 0 && adc.current_channel == 0 && !adc.err && !adc.busy);
    CHECK(!clk.has_alarm());                                         // the pending sample is gone
    CHECK(env.irq_n == 1 && !env.irq_levels[0] && env.dreq_n == 0);
    CHECK(tick(5000) && adc.fifo.empty());
    // a free-running capture's gap alarm is cancelled too
    fresh();
    wr(DIV, 0x6080);
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(tick(2000) && clk.has_alarm());
    CHECK(adc.reset() && !clk.has_alarm());
    // the host's wiring survives (it is not the block's)
    wr(CS, CS_EN | CS_START_ONE);
    CHECK(env.reads_n > 0);
}

static void test_aliases_decode_against_a_read_with_its_side_effects() {
    fresh(kSilent);
    CHECK(wr(DIV, 0x00F0));
    CHECK(wr(DIV, 0x0F, kAtomicSet) && rd(DIV) == 0xFF);
    CHECK(wr(DIV, 0x03, kAtomicXor) && rd(DIV) == 0xFC);
    CHECK(wr(DIV, 0xF0, kAtomicClear) && rd(DIV) == 0x0C);
    CHECK(adc.raw_write_value() == 0xF0);
    adc.fifo.push(0x11);
    adc.fifo.push(0x22);
    wr(FCS, FCS_EN);
    CHECK(wr(INTE, 1, kAtomicSet));                                  // plain read, no side effect
    CHECK(wr(0x0C, 5, kAtomicSet) && adc.fifo.count() == 1);        // FIFO has no write handler, but the decode READ pulled 0x11; the write warns
    CHECK(env.warns == 1 && env.warn_kind[0] == kAdcWarnWrite && env.warn_offset[0] == FIFO && env.warn_value[0] == (0x11 | 5));
}

static void test_unimplemented_offsets_warn_and_read_all_ones() {
    fresh();
    CHECK(rd(0x24) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kAdcWarnRead && env.warn_offset[0] == 0x24);
    env.warns = 0;
    CHECK(rd(0x1004) == 0xFFFFFFFFu && env.warns == 2 && env.warn_kind[1] == kAdcWarnReadAtomicArea);
    env.warns = 0;
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1);
    env.warns = 0;
    CHECK(wr(0x04, 0x77) && env.warns == 1 && env.warn_kind[0] == kAdcWarnWrite && env.warn_offset[0] == 0x04 && env.warn_value[0] == 0x77);   // RESULT is read-only
    env.warns = 0;
    CHECK(wr(INTR, 1) && wr(INTS, 1) && wr(FIFO, 1) && env.warns == 3);
}

static void test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised() {
    fresh(kDeferred);
    wr(FCS, FCS_EN | FCS_DREQ_EN | (1u << FCS_THRESH_SHIFT));
    const int irq_before = env.irq_n;
    env.fail_dreq = true;
    CHECK(!adc.complete_adc_read(5, false));                         // the value is in the FIFO, the line was not updated
    CHECK(adc.fifo.count() == 1 && rd(RESULT) == 5 && env.irq_n == irq_before);
    env.fail_dreq = false;
    env.failed = 0;
    fresh(kDeferred);
    wr(FCS, FCS_EN);
    wr(INTE, 1);
    env.fail_irq = true;
    CHECK(!adc.complete_adc_read(5, false) && adc.fifo.count() == 1);
    env.fail_irq = false;
    env.failed = 0;
    fresh(kDeferred);
    wr(CS, CS_EN | (0x1Fu << CS_RROBIN_SHIFT));
    wr(FCS, FCS_EN);
    wr(INTE, 1);
    const int irq_base = env.irq_n;
    env.fail_irq = true;
    CHECK(!adc.complete_adc_read(5, false) && adc.active_channel() == 0 && env.irq_n == irq_base + 1);   // the round-robin step is not taken
    env.fail_irq = false;
    env.failed = 0;
    fresh(kDefault);
    env.fail_read = true;
    CHECK(!wr(CS, CS_EN | CS_START_ONE) && adc.busy);                // busy was set before the device was asked
    env.fail_read = false;
    env.failed = 0;
    fresh(kDefault);
    wr(DIV, 0);
    wr(CS, CS_EN | CS_START_MANY);
    env.fail_read = true;
    env.reads_n = 0;
    CHECK(!tick(2000) && env.reads_n == 1);                          // the restart from the sample alarm fails: the clock stops
    env.fail_read = false;
    env.failed = 0;
    fresh(kDeferred);
    wr(DIV, 0x6080);
    wr(CS, CS_EN | CS_START_MANY);
    CHECK(adc.complete_adc_read(1, false) && clk.has_alarm());
    env.fail_read = true;
    CHECK(!tick(40) && env.reads_n == 2);                             // the multi-shot alarm's start fails
    env.fail_read = false;
    env.failed = 0;
    fresh(kDefault);
    wr(FCS, FCS_EN);
    wr(CS, CS_EN | CS_START_ONE);
    env.fail_irq = true;
    wr(INTE, 1);                                                     // the write fails at the line: the register is already stored
    CHECK(adc.int_enable == 1 && adc.failed());
    env.fail_irq = false;
    env.failed = 0;
    fresh(kDeferred);
    adc.fcs = FCS_DREQ_EN;
    adc.fifo.push(3);
    env.fail_dreq = true;
    CHECK(rd(FIFO) == 3 && adc.failed());                            // a FIFO read computes its value, the failure is parked
    env.fail_dreq = false;
    env.failed = 0;
    fresh(kDefault);
    adc.fcs = FCS_DREQ_EN;
    env.fail_dreq = true;
    CHECK(adc.reset());                                              // FCS is cleared first: the DREQ is not touched
    env.fail_dreq = false;
    env.failed = 0;
    adc.fcs = 0;
    env.fail_irq = true;
    CHECK(!adc.reset());
    env.fail_irq = false;
    env.failed = 0;
}

static void test_the_window_handler_and_detach() {
    fresh(kSilent);
    WindowHandler h = adc.window_handler();
    h.write32(h.ctx, DIV, 0x12, kAtomicNormal);
    h.write32(h.ctx, DIV, 0x01, kAtomicSet);
    CHECK(h.read32(h.ctx, DIV) == 0x13);
    CHECK(h.read32(h.ctx, CS) == CS_READY);
    fresh();
    wr(CS, CS_EN | CS_START_ONE);
    CHECK(clk.has_alarm());
    adc.detach();
    CHECK(!clk.has_alarm());
    fresh();
    adc.multi_shot_alarm.fire = [](void*) { return true; };
    clk.schedule(&adc.multi_shot_alarm, 50);
    CHECK(clk.has_alarm());
    adc.detach();
    CHECK(!clk.has_alarm());
}

int main() {
    test_a_default_constructed_block_is_at_power_on();
    test_a_fresh_block_is_at_power_on();
    test_a_conversion_by_the_default_device_takes_sample_time();
    test_the_channel_is_selected_by_ainsel_and_a_bad_one_fails_in_the_alarm();
    test_a_start_needs_enable_and_an_idle_block();
    test_the_fifo_shift_error_overflow_and_underflow();
    test_error_flags_in_cs_and_the_sticky_clear();
    test_interrupts_and_the_line();
    test_dreq_follows_the_threshold_when_enabled();
    test_round_robin_with_the_references_setter_quirk();
    test_a_free_running_capture_and_its_divider();
    test_an_immediate_device_completes_from_inside_the_callback();
    test_a_silent_device_leaves_the_block_busy_until_a_completion();
    test_reset_clears_the_state_the_alarms_and_republishes();
    test_aliases_decode_against_a_read_with_its_side_effects();
    test_unimplemented_offsets_warn_and_read_all_ones();
    test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised();
    test_the_window_handler_and_detach();
    if (failures == 0) std::printf("test_adc: ok\n");
    return failures == 0 ? 0 : 1;
}
