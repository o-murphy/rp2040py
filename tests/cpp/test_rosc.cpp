// Standalone checks of src/rp2040py/native/core/rosc.hpp (see tests/test_core_cpp.py for the flags).
//
// The end-to-end proof is tests/test_rosc_diff.py (the reference against the chip's ROSC, thousands of random steps with every mutant of the reference caught); these are the directed checks
// that need no Python: the power-on registers, the invalid-write rules (BADWRITE and the warning each), DIV and FREQ_RANGE deciding the frequency the host is told, COUNT counting down against a
// bare `Clock`, RANDOMBIT and the host that supplies it, the reset, the atomic aliases, failure injection per host function.
#include <cstdio>
#include <initializer_list>

#include "rosc.hpp"

using namespace rp2040core;
using namespace rp2040core::rosc_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t warn_kind[16], warn_offset[16];
    int64_t warn_value[16];
    int warns = 0;
    double freqs[16];
    int freq_calls = 0;
    int bit_calls = 0;
    uint32_t next_bit = 0xFFFFFFFEu;  // more than one bit: the block keeps bit 0 only
    int failed = 0;
    bool fail_freq = false, fail_bit = false;
};
static Env env;
static Clock clk;
static RoscBlock rosc;

static constexpr uint32_t kTestBlock = 42;  // the id the block reports its warnings under
static void on_warn(void*, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) {
    CHECK(block == kTestBlock);
    if (env.warns < 16) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}
static bool on_freq(void*, double hz) {
    if (env.freq_calls < 16) env.freqs[env.freq_calls] = hz;
    ++env.freq_calls;
    if (env.fail_freq) {
        env.failed = 1;
        return false;
    }
    return true;
}
static bool on_bit(void*, uint32_t* bit) {
    ++env.bit_calls;
    if (env.fail_bit) {
        env.failed = 1;
        return false;
    }
    *bit = env.next_bit;
    return true;
}

static const ChipPorts& test_ports() {
    static ChipPorts ports;
    ports.warn = on_warn;
    ports.failed = &env.failed;
    return ports;
}

static void fresh() {
    env = Env();
    clk.reset();
    RoscHost host;
    host.frequency_changed = on_freq;
    host.random_bit = on_bit;
    rosc.init(&clk, &test_ports(), kTestBlock, host);
    CHECK(rosc.reset());
    env.freq_calls = 0;
    env.warns = 0;
}
static uint32_t rd(uint32_t offset) { return rosc.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { return rosc.write_atomic(offset, value, atomic); }

static void test_power_on_state() {
    fresh();
    CHECK(rd(REG_CTRL) == 0x00FABAA0u);  // ENABLE "initialised to ENABLE", FREQ_RANGE 0xAA0
    CHECK(rd(REG_FREQA) == 0 && rd(REG_FREQB) == 0);
    CHECK(rd(REG_DORMANT) == WAKE_VALUE);
    CHECK(rd(REG_DIV) == 0xAB0u);  // div 16
    CHECK(rd(REG_PHASE) == 0x8u);
    CHECK(rd(REG_STATUS) == 0x80011000u);  // STABLE | DIV_RUNNING | ENABLED
    CHECK(rd(REG_COUNT) == 0);
    CHECK(rosc.frequency() == 6.5e6 && rosc.hz == 6.5e6);
    CHECK(env.warns == 0 && env.freq_calls == 0);
}

static void test_a_block_that_was_never_reset_is_at_power_on() {
    RoscBlock block;  // the shell only calls init(): the member defaults are the power-on state
    Clock c;
    RoscHost host;
    host.frequency_changed = on_freq;
    host.random_bit = on_bit;
    block.init(&c, &test_ports(), kTestBlock, host);
    CHECK(block.read(REG_CTRL) == 0x00FABAA0u && block.read(REG_DORMANT) == WAKE_VALUE && block.read(REG_DIV) == 0xAB0u && block.read(REG_PHASE) == 0x8u);
    CHECK(block.read(REG_STATUS) == 0x80011000u && block.enabled && !block.badwrite && block.hz == 6.5e6 && block.last_bit == 1);
    env.next_bit = 1;
    CHECK(block.read(REG_RANDOMBIT) == 1);
}

static void test_every_read_reports_success() {
    fresh();
    for (uint32_t offset : {REG_CTRL, REG_FREQA, REG_FREQB, REG_DORMANT, REG_DIV, REG_PHASE, REG_STATUS, REG_RANDOMBIT, REG_COUNT, 0x24u, 0x1000u}) {
        uint32_t value = 0;
        CHECK(rosc.read(offset, &value));
    }
    CHECK(env.warns == 2);  // 0x24 and 0x1000: one warning each, 0x1000 is not "in the atomic area"
    env.warns = 0;
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kRegWarnRead);
    CHECK(wr(REG_STATUS, 0));  // a write without BADWRITE clears nothing
    CHECK(wr(REG_DIV, 0xAA0u + 31) && env.warns == 1 && rd(REG_DIV) == 0xABFu);  // the largest valid div
    CHECK(wr(REG_STATUS, 0) && !(rd(REG_STATUS) & STATUS_BADWRITE));
    CHECK(wr(REG_PHASE, 0x0u));
    CHECK(env.warn_kind[1] == kRoscWarnInvalidPhasePasswd && env.warn_value[1] == 0);
    CHECK(wr(REG_PHASE, 0x55u << 4) && env.warn_value[2] == 0x55);
    CHECK(wr(REG_STATUS, 0) && (rd(REG_STATUS) & STATUS_BADWRITE));  // still set: writing 0 does not clear
}

static void test_ctrl_enable_rules() {
    fresh();
    CHECK(wr(REG_CTRL, (0xD1Eu << 12) | 0xAA0u));  // DISABLE
    CHECK(!rosc.enabled && rd(REG_STATUS) == 0 && env.warns == 0);
    CHECK(wr(REG_CTRL, (0xFABu << 12) | 0xAA0u));
    CHECK(rosc.enabled && rd(REG_STATUS) == 0x80011000u);
    CHECK(wr(REG_CTRL, (0xD1Eu << 12) | 0xAA0u));
    CHECK(wr(REG_CTRL, 0xAA0u));  // ENABLE 0: invalid - "An invalid setting will enable the oscillator"
    CHECK(rosc.enabled && (rd(REG_STATUS) & STATUS_BADWRITE) && env.warns == 1 && env.warn_kind[0] == kRoscWarnInvalidEnable && env.warn_value[0] == 0);
    CHECK(wr(REG_STATUS, STATUS_BADWRITE));  // write 1 to clear
    CHECK(!(rd(REG_STATUS) & STATUS_BADWRITE));
    CHECK(wr(REG_STATUS, 0x80011000u & ~STATUS_BADWRITE));  // other bits are read only
    CHECK(rd(REG_STATUS) == 0x80011000u);
    CHECK(wr(REG_CTRL, 0xFFFFFFFFu));  // reserved bits do not stick; 0xFFF is neither enable code nor a range
    CHECK(rd(REG_CTRL) == ((0xFFFu << 12) | 0xAA0u) && rosc.enabled);
}

static void test_freq_range_and_div_set_the_frequency_told_to_the_host() {
    fresh();
    CHECK(wr(REG_CTRL, (0xFABu << 12) | FREQ_RANGE_MEDIUM));
    CHECK(env.freq_calls == 1 && env.freqs[0] == 6.5e6 * 16.0 / 16.0 * 1.33);
    CHECK(wr(REG_CTRL, (0xFABu << 12) | FREQ_RANGE_HIGH));
    CHECK(env.freqs[1] == 6.5e6 * 2.0);
    CHECK(wr(REG_CTRL, (0xFABu << 12) | FREQ_RANGE_TOOHIGH));
    CHECK(env.freqs[2] == 6.5e6 * 4.0);
    CHECK(wr(REG_CTRL, (0xFABu << 12) | FREQ_RANGE_LOW));
    CHECK(env.freqs[3] == 6.5e6);
    CHECK(wr(REG_CTRL, (0xFABu << 12) | FREQ_RANGE_LOW) && env.freq_calls == 4);  // unchanged: nobody is told
    CHECK(wr(REG_DIV, 0xAA0u + 8));  // divide by 8: twice as fast
    CHECK(env.freqs[4] == 13e6 && rd(REG_DIV) == 0xAA8u);
    CHECK(wr(REG_DIV, 0xAA0u));  // div = 0 divides by 32
    CHECK(env.freqs[5] == 6.5e6 * 16.0 / 32.0);
    CHECK(wr(REG_DIV, 0xAA0u + 1));
    CHECK(env.freqs[6] == 6.5e6 * 16.0);
    CHECK(env.warns == 0);
    // an invalid range leaves the previous one (the model's choice) and warns
    CHECK(wr(REG_CTRL, (0xFABu << 12) | 0x123u));
    CHECK(env.warns == 1 && env.warn_kind[0] == kRoscWarnInvalidFreqRange && env.warn_value[0] == 0x123 && (rd(REG_CTRL) & 0xFFFu) == FREQ_RANGE_LOW);
    // an invalid DIV sets div = 31
    CHECK(wr(REG_DIV, 0x123u));
    CHECK(env.warns == 2 && env.warn_kind[1] == kRoscWarnInvalidDiv && rd(REG_DIV) == 0xAA0u + 31 && (rd(REG_STATUS) & STATUS_BADWRITE));
    CHECK(wr(REG_DIV, 0xAA0u + 32));  // 32 is not a div value either
    CHECK(env.warns == 3 && rd(REG_DIV) == 0xAA0u + 31);
}

static void test_freqa_freqb_dormant_phase() {
    fresh();
    CHECK(wr(REG_FREQA, 0x96960000u | 0x7777u | 0x8888u));  // reserved bits 15, 11, 7, 3 do not stick
    CHECK(rd(REG_FREQA) == 0x96967777u && env.warns == 0);
    CHECK(wr(REG_FREQB, 0x12340000u | 0x0777u));  // wrong password: all drive strengths 0
    CHECK(rd(REG_FREQB) == 0x12340000u && env.warns == 1 && env.warn_kind[0] == kRoscWarnInvalidFreqPasswd && env.warn_value[0] == 0x1234);
    CHECK(wr(REG_DORMANT, DORMANT_VALUE) && rd(REG_DORMANT) == DORMANT_VALUE && env.warns == 1);
    CHECK(rd(REG_STATUS) == (0x80011000u | STATUS_BADWRITE));  // stored only: the oscillator keeps running
    CHECK(wr(REG_DORMANT, 0x1234u) && rd(REG_DORMANT) == WAKE_VALUE && env.warns == 2 && env.warn_kind[1] == kRoscWarnInvalidDormant);
    CHECK(wr(REG_PHASE, (0xAAu << 4) | 0x6u));  // passwd ok, ENABLE 0, FLIP 1, SHIFT 2
    CHECK(rd(REG_PHASE) == 0xAA6u && env.warns == 2);
    CHECK(wr(REG_PHASE, (0x55u << 4) | 0x7u));  // wrong password: enabled with shift 0
    CHECK(rd(REG_PHASE) == 0x55Cu && env.warns == 3 && env.warn_kind[2] == kRoscWarnInvalidPhasePasswd);
    CHECK(wr(REG_PHASE, 0xFFFFFFFFu) && (rd(REG_PHASE) & ~0xFFFu) == 0);
}

static void test_the_counter_counts_down_at_the_rosc_frequency() {
    fresh();
    CHECK(wr(REG_COUNT, 0x1FFu));  // 8 bits
    CHECK(rd(REG_COUNT) == 0xFF);
    CHECK(clk.tick(1000.0));  // 1 us at 6.5 MHz: 6 cycles (6.5 floored)
    CHECK(rd(REG_COUNT) == 0xFF - 6);
    CHECK(clk.tick(1e9));
    CHECK(rd(REG_COUNT) == 0);  // counts to zero and stops
    CHECK(wr(REG_COUNT, 100));
    CHECK(clk.tick(1000.0));
    CHECK(rd(REG_COUNT) == 94);
    CHECK(wr(REG_DIV, 0xAA0u + 8));  // twice the frequency: the time that passed is folded in at the old rate
    CHECK(clk.tick(1000.0));
    CHECK(rd(REG_COUNT) == 94 - 13);
    CHECK(wr(REG_CTRL, (0xD1Eu << 12) | 0xAA0u));  // disabled: it holds
    CHECK(rd(REG_COUNT) == 81);
    CHECK(clk.tick(1e6) && rd(REG_COUNT) == 81);
    CHECK(wr(REG_CTRL, (0xFABu << 12) | 0xAA0u));
    CHECK(clk.tick(1000.0) && rd(REG_COUNT) == 81 - 13);
    CHECK(wr(REG_COUNT, 0));
    CHECK(rd(REG_COUNT) == 0);
}

static void test_randombit() {
    fresh();
    CHECK(rd(REG_RANDOMBIT) == 0 && env.bit_calls == 1);  // the host's bit, masked to bit 0
    env.next_bit = 1;
    CHECK(rd(REG_RANDOMBIT) == 1 && env.bit_calls == 2);
    CHECK(wr(REG_CTRL, (0xD1Eu << 12) | 0xAA0u));
    env.next_bit = 0;
    CHECK(rd(REG_RANDOMBIT) == 1 && env.bit_calls == 2);  // disabled: holds the last value, does not ask
    CHECK(wr(REG_RANDOMBIT, 0) && rd(REG_RANDOMBIT) == 1 && env.warns == 0);  // read only
    CHECK(rosc.reset() && rd(REG_RANDOMBIT) == 0 && env.bit_calls == 3);  // enabled again
    CHECK(wr(REG_CTRL, (0xD1Eu << 12) | 0xAA0u) && rosc.last_bit == 0);
    CHECK(wr(REG_DORMANT, DORMANT_VALUE) && wr(REG_PHASE, 0xAA0u) && wr(REG_CTRL, (0xFABu << 12) | 0xAA0u));
    env.next_bit = 0;
    CHECK(rd(REG_RANDOMBIT) == 0 && rosc.last_bit == 0);
    CHECK(rosc.reset() && rosc.last_bit == 1 && rd(REG_DORMANT) == WAKE_VALUE && rd(REG_PHASE) == 0x8u);  // the reset puts the held bit, DORMANT and PHASE back
}

static void test_reset_and_the_frequency() {
    fresh();
    CHECK(wr(REG_DIV, 0xAA0u + 4) && wr(REG_COUNT, 50) && wr(REG_FREQA, 0x96960001u) && wr(REG_CTRL, 0xFFFu));
    env.freq_calls = 0;
    CHECK(rosc.reset());
    CHECK(env.freq_calls == 1 && env.freqs[0] == 6.5e6);  // the chip is told it is back to nominal
    CHECK(rd(REG_CTRL) == 0x00FABAA0u && rd(REG_DIV) == 0xAB0u && rd(REG_FREQA) == 0 && rd(REG_COUNT) == 0 && !(rd(REG_STATUS) & STATUS_BADWRITE));
    CHECK(rosc.reset() && env.freq_calls == 1);  // already nominal: nobody is told
}

static void test_aliases_unimplemented_offsets_and_the_window() {
    fresh();
    CHECK(wr(REG_DIV, 0xAA0u + 4));
    CHECK(wr(REG_DIV, 0x3, kAtomicXor));  // 0xAA4 ^ 3 = 0xAA7
    CHECK(rd(REG_DIV) == 0xAA7u && rosc.raw_write_value() == 3);
    CHECK(wr(REG_DIV, 0x1, kAtomicClear) && rd(REG_DIV) == 0xAA6u);
    CHECK(wr(REG_DIV, 0x1, kAtomicSet) && rd(REG_DIV) == 0xAA7u);
    CHECK(rd(0x24) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kRegWarnRead && env.warn_offset[0] == 0x24);
    CHECK(rd(0x1024) == 0xFFFFFFFFu && env.warns == 3 && env.warn_kind[2] == kRegWarnReadAtomicArea);
    CHECK(wr(0x28, 5) && env.warns == 4 && env.warn_kind[3] == kRegWarnWrite && env.warn_value[3] == 5);
    WindowHandler w = rosc.window_handler();
    w.write32(w.ctx, REG_COUNT, 9, kAtomicNormal);
    CHECK(w.read32(w.ctx, REG_COUNT) == 9);
}

static void test_failures_stop_at_once() {
    fresh();
    env.fail_freq = true;
    CHECK(!wr(REG_DIV, 0xAA0u + 2));  // the host failed to retune: the register is written, the frequency is what the block gave the chip
    CHECK(rd(REG_DIV) == 0xAA2u && rosc.hz == 6.5e6 * 16.0 / 2.0 && env.failed == 1);
    fresh();
    env.fail_bit = true;
    uint32_t value = 123;
    CHECK(!rosc.read(REG_RANDOMBIT, &value) && env.failed == 1);
    fresh();
    env.fail_bit = true;
    CHECK(!wr(REG_RANDOMBIT, 1, kAtomicXor));  // the read an alias decodes against asks the host for a bit, and fails
    fresh();
    env.failed = 1;
    CHECK(!wr(REG_COUNT, 5, kAtomicXor) && rd(REG_COUNT) == 0);  // a failure already pending: an alias write stops after its decoding read
    CHECK(wr(REG_COUNT, 5) && rd(REG_COUNT) == 5);  // ...a plain write is not refused for it (the shared rule of RegisterBlock)
    env.failed = 0;
    CHECK(wr(REG_COUNT, 7) && rd(REG_COUNT) == 7);
    fresh();
    CHECK(wr(REG_DIV, 0xAA0u + 2));
    env.fail_freq = true;
    CHECK(!rosc.reset());  // the chip could not be told it is back to nominal
    CHECK(rd(REG_DIV) == 0xAB0u);
}

int main() {
    test_power_on_state();
    test_a_block_that_was_never_reset_is_at_power_on();
    test_every_read_reports_success();
    test_ctrl_enable_rules();
    test_freq_range_and_div_set_the_frequency_told_to_the_host();
    test_freqa_freqb_dormant_phase();
    test_the_counter_counts_down_at_the_rosc_frequency();
    test_randombit();
    test_reset_and_the_frequency();
    test_aliases_unimplemented_offsets_and_the_window();
    test_failures_stop_at_once();
    if (failures == 0) std::printf("test_rosc: ok\n");
    return failures == 0 ? 0 : 1;
}
