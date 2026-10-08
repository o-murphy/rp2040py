// Standalone checks of src/rp2040py/native/core/psm.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_psm.py) that the lockstep differential (tests/test_psm_xosc_resets_diff.py) also pins, against a recording host.
#include <cstdio>

#include "psm.hpp"

using namespace rp2040core;
using namespace rp2040core::psm_regs;

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

static PsmBlock block;

static void fresh() {
    env = Env();
    PsmHost host;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
    block.frce_on = block.frce_off = block.wdsel = 0;
}

static void test_reset_values() {
    fresh();
    CHECK(block.read(FRCE_ON) == 0 && block.read(FRCE_OFF) == 0 && block.read(REG_WDSEL) == 0);
    CHECK(block.read(REG_DONE) == BITS_MASK && env.warns == 0);  // every domain is ready until it is forced off
}

static void test_the_three_registers_keep_seventeen_bits() {
    fresh();
    CHECK(block.write(FRCE_ON, 0xFFFFFFFF) && block.read(FRCE_ON) == 0x1FFFF);
    CHECK(block.write(FRCE_OFF, 0xFFFFFFFF) && block.read(FRCE_OFF) == 0x1FFFF);
    CHECK(block.write(REG_WDSEL, 0xFFFE0004u) && block.read(REG_WDSEL) == 0x4 && block.wdsel == 0x4);
    CHECK(env.warns == 0);
}

static void test_done_follows_force_off_and_force_on_overrides_it() {
    fresh();
    block.write(FRCE_OFF, 0x0006);
    CHECK(block.read(REG_DONE) == (BITS_MASK & ~0x6u));
    block.write(FRCE_ON, 0x0002);
    CHECK(block.read(REG_DONE) == (BITS_MASK & ~0x6u) + 0x2u);  // domain 1 is forced off AND on: on wins
    CHECK(block.write(REG_DONE, 0) && block.read(REG_DONE) == (BITS_MASK & ~0x6u) + 0x2u && env.warns == 0);  // read-only: no effect, silent
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0x10) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kPsmWarnRead && env.warn_offset[0] == 0x10);
    CHECK(block.read(0x3000) == 0xFFFFFFFFu);
    CHECK(env.warns == 3 && env.warn_kind[2] == kPsmWarnReadAtomicArea);
    CHECK(block.write(0x14, 9));
    CHECK(env.warns == 4 && env.warn_kind[3] == kPsmWarnWrite && env.warn_offset[3] == 0x14 && env.warn_value[3] == 9);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    CHECK(block.write_atomic(REG_WDSEL, 0x0F, kAtomicSet) && block.raw_write_value() == 0x0F && block.read(REG_WDSEL) == 0x0F);
    CHECK(block.write_atomic(REG_WDSEL, 0x03, kAtomicClear) && block.read(REG_WDSEL) == 0x0C);
    CHECK(block.write_atomic(REG_WDSEL, 0xFF, kAtomicXor) && block.read(REG_WDSEL) == 0xF3);
}

static void test_a_new_block_and_the_window_handler() {
    PsmBlock fresh_block;
    CHECK(fresh_block.frce_on == 0 && fresh_block.frce_off == 0 && fresh_block.wdsel == 0 && fresh_block.raw_write_value() == 0);
    fresh();
    WindowHandler handler = block.window_handler();
    handler.write32(handler.ctx, REG_WDSEL, 0x1FFFC, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, REG_WDSEL) == 0x1FFFC);
}

int main() {
    test_reset_values();
    test_the_three_registers_keep_seventeen_bits();
    test_done_follows_force_off_and_force_on_overrides_it();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_a_new_block_and_the_window_handler();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all psm checks passed\n");
    return 0;
}
