// Standalone checks of src/rp2040py/native/core/resets.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_reset.py) that the lockstep differential (tests/test_psm_xosc_resets_diff.py) also pins, against a recording host.
#include <cstdio>

#include "resets.hpp"

using namespace rp2040core;
using namespace rp2040core::resets_regs;

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

static ResetsBlock block;

static void fresh() {
    env = Env();
    ResetsHost host;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
    block.reset_bits = block.wdsel = 0;
}

static void test_reset_values() {
    fresh();
    CHECK(block.read(REG_RESET) == 0 && block.read(REG_WDSEL) == 0 && block.read(REG_RESET_DONE) == BITS_MASK && env.warns == 0);
}

static void test_reset_and_wdsel_keep_twenty_five_bits() {
    fresh();
    CHECK(block.write(REG_RESET, 0xFFFFFFFF) && block.read(REG_RESET) == 0x1FFFFFF);
    CHECK(block.write(REG_WDSEL, 0xFE400000u) && block.read(REG_WDSEL) == 0x400000 && block.wdsel == 0x400000);
    CHECK(env.warns == 0);
}

static void test_done_is_the_complement_of_reset() {
    fresh();
    block.write(REG_RESET, 0x00400000);  // UART0 into reset
    CHECK(block.read(REG_RESET_DONE) == (BITS_MASK & ~0x00400000u));
    block.write(REG_RESET, 0x1FFFFFF);
    CHECK(block.read(REG_RESET_DONE) == 0);
    CHECK(block.write(REG_RESET_DONE, 0xFFFFFFFF) && block.read(REG_RESET_DONE) == 0 && env.warns == 0);  // read-only: no effect, silent
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0xC) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kResetsWarnRead && env.warn_offset[0] == 0xC);
    CHECK(block.read(0x3000) == 0xFFFFFFFFu);
    CHECK(env.warns == 3 && env.warn_kind[2] == kResetsWarnReadAtomicArea);
    CHECK(block.write(0x10, 9));
    CHECK(env.warns == 4 && env.warn_kind[3] == kResetsWarnWrite && env.warn_offset[3] == 0x10 && env.warn_value[3] == 9);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    CHECK(block.write_atomic(REG_RESET, 0x0F, kAtomicSet) && block.raw_write_value() == 0x0F && block.read(REG_RESET) == 0x0F);
    CHECK(block.write_atomic(REG_RESET, 0x03, kAtomicClear) && block.read(REG_RESET) == 0x0C);
    CHECK(block.write_atomic(REG_RESET, 0xFF, kAtomicXor) && block.read(REG_RESET) == 0xF3);
}

static void test_a_new_block_and_the_window_handler() {
    ResetsBlock fresh_block;
    CHECK(fresh_block.reset_bits == 0 && fresh_block.wdsel == 0 && fresh_block.raw_write_value() == 0);
    fresh();
    WindowHandler handler = block.window_handler();
    handler.write32(handler.ctx, REG_RESET, 0x4, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, REG_RESET) == 0x4 && handler.read32(handler.ctx, REG_RESET_DONE) == (BITS_MASK & ~0x4u));
}

int main() {
    test_reset_values();
    test_reset_and_wdsel_keep_twenty_five_bits();
    test_done_is_the_complement_of_reset();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_a_new_block_and_the_window_handler();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all resets checks passed\n");
    return 0;
}
