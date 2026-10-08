// Standalone checks of src/rp2040py/native/core/vreg.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_vreg_and_chip_reset.py) that the lockstep differential (tests/test_small_blocks_diff.py) also pins, against a recording host.
#include <cstdio>

#include "vreg.hpp"

using namespace rp2040core;
using namespace rp2040core::vreg_regs;

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

static VregBlock block;

static void fresh() {
    env = Env();
    VregHost host;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
    block.vreg = VREG_RESET;
    block.bod = BOD_RESET;
    block.chip_reset = HAD_POR;
}

static void test_reset_values() {
    fresh();
    CHECK(block.read(REG_VREG) == 0xB1 && block.read(REG_BOD) == 0x91 && block.read(REG_CHIP_RESET) == HAD_POR && env.warns == 0);
}

static void test_a_new_block_is_in_its_reset_state() {
    VregBlock fresh_block;
    CHECK(fresh_block.vreg == 0xB1 && fresh_block.bod == 0x91 && fresh_block.chip_reset == HAD_POR && fresh_block.raw_write_value() == 0);
}

static void test_vreg_and_bod_keep_their_reserved_bits() {
    fresh();
    CHECK(block.write(REG_VREG, 0xFFFFFFFF) && block.read(REG_VREG) == 0xF3);  // VSEL 7:4, HIZ 1, EN 0; ROK (bit 12) is read-only
    CHECK(block.write(REG_BOD, 0xFFFFFFFF) && block.read(REG_BOD) == 0xF1);
    CHECK(block.write(REG_VREG, 0) && block.read(REG_VREG) == 0);
    CHECK(block.write(REG_BOD, 0x10) && block.read(REG_BOD) == 0x10);
}

static void test_chip_reset_only_clears_the_psm_restart_flag() {
    fresh();
    block.chip_reset |= PSM_RESTART_FLAG;
    CHECK(block.write(REG_CHIP_RESET, HAD_POR | HAD_RUN | HAD_PSM_RESTART) && block.read(REG_CHIP_RESET) == (HAD_POR | PSM_RESTART_FLAG));
    CHECK(block.write(REG_CHIP_RESET, PSM_RESTART_FLAG) && block.read(REG_CHIP_RESET) == HAD_POR);
    CHECK(block.write(REG_CHIP_RESET, PSM_RESTART_FLAG) && block.read(REG_CHIP_RESET) == HAD_POR);  // a write of 1 never sets it
}

static void test_a_recorded_cause_replaces_the_others_and_keeps_the_psm_flag() {
    fresh();
    block.chip_reset |= PSM_RESTART_FLAG;
    block.record_reset_cause(HAD_RUN);
    CHECK(block.read(REG_CHIP_RESET) == (HAD_RUN | PSM_RESTART_FLAG));
    block.record_reset_cause(HAD_PSM_RESTART);
    CHECK(block.read(REG_CHIP_RESET) == (HAD_PSM_RESTART | PSM_RESTART_FLAG));
    block.write(REG_CHIP_RESET, PSM_RESTART_FLAG);
    block.record_reset_cause(HAD_POR);
    CHECK(block.read(REG_CHIP_RESET) == HAD_POR);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0xC) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kVregWarnRead && env.warn_offset[0] == 0xC);
    CHECK(block.read(0x3000) == 0xFFFFFFFFu && env.warns == 3 && env.warn_kind[2] == kVregWarnReadAtomicArea);
    CHECK(block.write(0x10, 9) && env.warns == 4 && env.warn_kind[3] == kVregWarnWrite && env.warn_value[3] == 9);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    CHECK(block.write_atomic(REG_VREG, 0x40, kAtomicSet) && block.raw_write_value() == 0x40 && block.read(REG_VREG) == 0xF1);
    CHECK(block.write_atomic(REG_VREG, 0x01, kAtomicClear) && block.read(REG_VREG) == 0xF0);
    CHECK(block.write_atomic(REG_BOD, 0xF0, kAtomicXor) && block.read(REG_BOD) == 0x61);
}

static void test_the_window_handler() {
    fresh();
    WindowHandler handler = block.window_handler();
    handler.write32(handler.ctx, REG_BOD, 0, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, REG_BOD) == 0x00);
}

int main() {
    test_reset_values();
    test_a_new_block_is_in_its_reset_state();
    test_vreg_and_bod_keep_their_reserved_bits();
    test_chip_reset_only_clears_the_psm_restart_flag();
    test_a_recorded_cause_replaces_the_others_and_keeps_the_psm_flag();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_the_window_handler();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all vreg checks passed\n");
    return 0;
}
