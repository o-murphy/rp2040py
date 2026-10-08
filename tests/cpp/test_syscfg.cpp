// Standalone checks of src/rp2040py/native/core/syscfg.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_syscfg.py) that the lockstep differential (tests/test_small_blocks_diff.py) also pins, against a recording host.
#include <cstdio>

#include "syscfg.hpp"

using namespace rp2040core;
using namespace rp2040core::syscfg_regs;

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

static SyscfgBlock block;
static Cpu cpu;

static void fresh() {
    env = Env();
    SyscfgHost host;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(&cpu, host);
    block.reset();
    cpu.interrupt_nmi_mask = 0;
}

static void test_reset_values() {
    fresh();
    const uint32_t expected[7] = {0, 0, 0x10000000, 0, 0, 0x66, 0};
    for (uint32_t i = 0; i < 7; ++i) CHECK(block.read(4 * i) == expected[i]);
    CHECK(env.warns == 0);
}

static void test_proc0_nmi_mask_is_the_cores() {
    fresh();
    CHECK(block.write(PROC0_NMI_MASK, 0xA5A5A5A5u) && cpu.interrupt_nmi_mask == 0xA5A5A5A5u);
    cpu.interrupt_nmi_mask = 0x1234;
    CHECK(block.read(PROC0_NMI_MASK) == 0x1234);
}

static void test_the_other_registers_keep_the_datasheets_widths() {
    fresh();
    for (uint32_t offset = 0; offset <= 0x18; offset += 4) block.write(offset, 0xFFFFFFFFu);
    CHECK(block.read(PROC1_NMI_MASK) == 0xFFFFFFFFu);
    CHECK(block.read(PROC_CONFIG) == 0xFF000000u);  // the DAP instance ids; HALTED 1:0 are read-only
    CHECK(block.read(PROC_IN_SYNC_BYPASS) == 0x3FFFFFFFu);  // GPIO 0..29
    CHECK(block.read(PROC_IN_SYNC_BYPASS_HI) == 0x3Fu);  // GPIO 30..35
    CHECK(block.read(DBGFORCE) == (0xEEu | 0x66u));  // SWDO (bits 4 and 0) are read-only
    CHECK(block.read(MEMPOWERDOWN) == 0xFFu);
    CHECK(env.warns == 0);
    block.write(PROC_CONFIG, 0x00FFFFFFu);
    CHECK(block.read(PROC_CONFIG) == 0x00000000u);  // a write of zeros to the writable field clears it, the rest cannot be set
}

static void test_reset_puts_everything_back_the_nmi_mask_included() {
    fresh();
    for (uint32_t offset = 0; offset <= 0x18; offset += 4) block.write(offset, 0xFFFFFFFFu);
    CHECK(block.reset());
    const uint32_t expected[7] = {0, 0, 0x10000000, 0, 0, 0x66, 0};
    for (uint32_t i = 0; i < 7; ++i) CHECK(block.read(4 * i) == expected[i]);
    CHECK(cpu.interrupt_nmi_mask == 0);
}

static void test_a_new_block_is_in_its_reset_state() {
    Cpu other;
    SyscfgBlock fresh_block;
    SyscfgHost host;
    host.warn = on_warn;
    fresh_block.init(&other, host);
    const uint32_t expected[7] = {0, 0, 0x10000000, 0, 0, 0x66, 0};
    for (uint32_t i = 0; i < 7; ++i) CHECK(fresh_block.read(4 * i) == expected[i]);
    CHECK(fresh_block.raw_write_value() == 0);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0x1C) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kSyscfgWarnRead && env.warn_offset[0] == 0x1C);
    CHECK(block.read(0x3000) == 0xFFFFFFFFu && env.warns == 3 && env.warn_kind[2] == kSyscfgWarnReadAtomicArea);
    CHECK(block.write(0x20, 9) && env.warns == 4 && env.warn_kind[3] == kSyscfgWarnWrite && env.warn_value[3] == 9);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    CHECK(block.write_atomic(MEMPOWERDOWN, 0x0F, kAtomicSet) && block.raw_write_value() == 0x0F && block.read(MEMPOWERDOWN) == 0x0F);
    CHECK(block.write_atomic(MEMPOWERDOWN, 0x03, kAtomicClear) && block.read(MEMPOWERDOWN) == 0x0C);
    CHECK(block.write_atomic(PROC0_NMI_MASK, 0xF0, kAtomicXor) && cpu.interrupt_nmi_mask == 0xF0);
}

static void test_the_window_handler() {
    fresh();
    WindowHandler handler = block.window_handler();
    handler.write32(handler.ctx, PROC1_NMI_MASK, 7, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, PROC1_NMI_MASK) == 7);
}

int main() {
    test_reset_values();
    test_proc0_nmi_mask_is_the_cores();
    test_the_other_registers_keep_the_datasheets_widths();
    test_reset_puts_everything_back_the_nmi_mask_included();
    test_a_new_block_is_in_its_reset_state();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_the_window_handler();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all syscfg checks passed\n");
    return 0;
}
