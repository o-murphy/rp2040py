// Standalone checks of src/rp2040py/native/core/xosc.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_xosc.py) that the lockstep differential (tests/test_psm_xosc_resets_diff.py) also pins, against a recording host.
#include <cstdio>

#include "xosc.hpp"

using namespace rp2040core;
using namespace rp2040core::xosc_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t warn_kind[16] = {}, warn_offset[16] = {};
    int64_t warn_value[16] = {};
    int warns = 0;
    int failed = 0;
};
static Env env;

static void on_warn(void*, uint32_t kind, uint32_t offset, int64_t value) {
    if (env.warns < 16) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}

static XoscBlock block;

static void fresh() {
    env = Env();
    XoscHost host;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
    block.reset();
}

static void test_reset_values() {
    fresh();
    CHECK(block.read(REG_CTRL) == 0xAA0 && block.read(REG_STATUS) == 0 && block.read(REG_DORMANT) == WAKE_VALUE);
    CHECK(block.read(REG_STARTUP) == 0xC4 && block.read(REG_COUNT) == 0 && env.warns == 0);
}

static void test_ctrl_keeps_reserved_bits_clear_and_freq_range_fixed() {
    fresh();
    CHECK(block.write(REG_CTRL, 0xFFFFFFFF));
    CHECK((block.read(REG_CTRL) & 0xFF000000u) == 0 && (block.read(REG_CTRL) & 0xFFF) == 0xAA0);
    CHECK((block.read(REG_STATUS) & STATUS_BADWRITE) != 0);
    CHECK(env.warns >= 1 && env.warn_kind[0] == kXoscWarnInvalidFreqRange && env.warn_value[0] == 0xFFF);
}

static void test_a_wrong_freq_range_alone_sets_badwrite_and_a_status_write_of_zero_keeps_it() {
    fresh();
    CHECK(block.write(REG_CTRL, 0xAA1));  // ENABLE is 0: only FREQ_RANGE is wrong
    CHECK(block.read(REG_STATUS) == STATUS_BADWRITE && block.read(REG_CTRL) == 0xAA0);
    CHECK(block.write(REG_STATUS, 0xFEFFFFFFu) && block.read(REG_STATUS) == STATUS_BADWRITE);  // BADWRITE is bit 24 only
    CHECK(block.write(REG_STATUS, STATUS_BADWRITE) && block.read(REG_STATUS) == 0);
}

static void test_the_sdk_startup_sequence() {
    fresh();
    CHECK(block.write(REG_CTRL, 0xAA0) && block.write(REG_STARTUP, 47));
    CHECK(block.write_atomic(REG_CTRL, CTRL_ENABLE_ENABLE << 12, kAtomicSet));  // hw_set_bits()
    CHECK(block.read(REG_STATUS) == (STATUS_STABLE | STATUS_ENABLED) && env.warns == 0);
    CHECK(block.write(REG_CTRL, (CTRL_ENABLE_DISABLE << 12) | 0xAA0));
    CHECK(block.read(REG_STATUS) == 0);
}

static void test_an_invalid_enable_value_sets_badwrite_and_changes_nothing() {
    fresh();
    CHECK(block.write(REG_CTRL, (0x123u << 12) | 0xAA0));
    CHECK(block.read(REG_STATUS) == STATUS_BADWRITE && env.warns == 1 && env.warn_kind[0] == kXoscWarnInvalidEnable && env.warn_value[0] == 0x123);
    CHECK(block.write(REG_STATUS, STATUS_BADWRITE) && block.read(REG_STATUS) == 0);  // write 1 to clear
}

static void test_dormant_stops_the_oscillator_and_wake_restarts_it() {
    fresh();
    block.write(REG_CTRL, (CTRL_ENABLE_ENABLE << 12) | 0xAA0);
    CHECK(block.write(REG_DORMANT, DORMANT_VALUE) && block.read(REG_DORMANT) == DORMANT_VALUE);
    CHECK(block.read(REG_STATUS) == STATUS_ENABLED);  // enabled, not stable
    block.write(REG_CTRL, (CTRL_ENABLE_ENABLE << 12) | 0xAA0);  // an enable while dormant does not make it stable
    CHECK(block.read(REG_STATUS) == STATUS_ENABLED);
    CHECK(block.write(REG_DORMANT, WAKE_VALUE) && block.read(REG_DORMANT) == WAKE_VALUE && block.read(REG_STATUS) == (STATUS_STABLE | STATUS_ENABLED) && env.warns == 0);
}

static void test_an_invalid_dormant_write_selects_wake() {
    fresh();
    block.write(REG_CTRL, (CTRL_ENABLE_ENABLE << 12) | 0xAA0);
    block.write(REG_DORMANT, DORMANT_VALUE);
    CHECK(block.write(REG_DORMANT, 0x12345678));
    CHECK(block.read(REG_DORMANT) == WAKE_VALUE && (block.read(REG_STATUS) & STATUS_BADWRITE) && (block.read(REG_STATUS) & STATUS_STABLE));
    CHECK(env.warns == 1 && env.warn_kind[0] == kXoscWarnInvalidDormant && env.warn_value[0] == 0x12345678);
}

static void test_startup_and_count_widths() {
    fresh();
    CHECK(block.write(REG_STARTUP, 0xFFFFFFFF) && block.read(REG_STARTUP) == (STARTUP_X4 | STARTUP_DELAY_BITS));
    CHECK(block.write(REG_COUNT, 0xFFFFFFFF) && block.read(REG_COUNT) == 0xFF && env.warns == 0);
}

static void test_reset_puts_everything_back() {
    fresh();
    block.write(REG_CTRL, (CTRL_ENABLE_ENABLE << 12) | 0xAA0);
    block.write(REG_STARTUP, 0x1);
    block.write(REG_COUNT, 0x5);
    block.write(REG_DORMANT, DORMANT_VALUE);
    block.write(REG_CTRL, 0xFFFFFF);
    CHECK(block.reset());
    CHECK(block.read(REG_CTRL) == 0xAA0 && block.read(REG_STATUS) == 0 && block.read(REG_DORMANT) == WAKE_VALUE && block.read(REG_STARTUP) == 0xC4 && block.read(REG_COUNT) == 0);
    CHECK(!block.enabled && !block.stable && !block.is_dormant);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0x10) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kXoscWarnRead && env.warn_offset[0] == 0x10);
    CHECK(block.read(0x3000) == 0xFFFFFFFFu);
    CHECK(env.warns == 3 && env.warn_kind[2] == kXoscWarnReadAtomicArea);
    CHECK(block.write(0x14, 9));
    CHECK(env.warns == 4 && env.warn_kind[3] == kXoscWarnWrite && env.warn_offset[3] == 0x14 && env.warn_value[3] == 9);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    CHECK(block.write_atomic(REG_COUNT, 0x0F, kAtomicSet) && block.raw_write_value() == 0x0F && block.read(REG_COUNT) == 0x0F);
    CHECK(block.write_atomic(REG_COUNT, 0x03, kAtomicClear) && block.read(REG_COUNT) == 0x0C);
    CHECK(block.write_atomic(REG_COUNT, 0xFF, kAtomicXor) && block.read(REG_COUNT) == 0xF3);
}

static void test_a_new_block_and_the_window_handler() {
    XoscBlock fresh_block;
    CHECK(fresh_block.status == 0 && fresh_block.count == 0 && fresh_block.ctrl == 0xAA0 && fresh_block.dormant == WAKE_VALUE && fresh_block.startup == 0xC4 && fresh_block.raw_write_value() == 0);
    fresh();
    WindowHandler handler = block.window_handler();
    handler.write32(handler.ctx, REG_COUNT, 7, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, REG_COUNT) == 7);
}

int main() {
    test_reset_values();
    test_ctrl_keeps_reserved_bits_clear_and_freq_range_fixed();
    test_a_wrong_freq_range_alone_sets_badwrite_and_a_status_write_of_zero_keeps_it();
    test_the_sdk_startup_sequence();
    test_an_invalid_enable_value_sets_badwrite_and_changes_nothing();
    test_dormant_stops_the_oscillator_and_wake_restarts_it();
    test_an_invalid_dormant_write_selects_wake();
    test_startup_and_count_widths();
    test_reset_puts_everything_back();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_a_new_block_and_the_window_handler();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all xosc checks passed\n");
    return 0;
}
