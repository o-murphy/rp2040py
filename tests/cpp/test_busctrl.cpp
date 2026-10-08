// Standalone checks of src/rp2040py/native/core/busctrl.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_busctrl.py) that the lockstep differential (tests/test_small_blocks_diff.py) also pins, against a recording host.
#include <cstdio>

#include "busctrl.hpp"

using namespace rp2040core;
using namespace rp2040core::busctrl_regs;

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

static BusctrlBlock block;

static void fresh() {
    env = Env();
    BusctrlHost host;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
    block.reset();
}

static void test_reset_state() {
    fresh();
    CHECK(block.read(BUS_PRIORITY) == 0 && block.read(BUS_PRIORITY_ACK) == 1);
    for (uint32_t i = 0; i < 4; ++i) {
        CHECK(block.read(PERFCTR0 + 8 * i) == 0);
        CHECK(block.read(PERFSEL0 + 8 * i) == 0x1F);  // datasheet table 8 and the next: reset 0x1f
    }
    CHECK(env.warns == 0);
}

static void test_bus_priority_keeps_four_bits() {
    fresh();
    CHECK(block.write(BUS_PRIORITY, 0xFFFFFFFF) && block.read(BUS_PRIORITY) == 0x1111);  // PROC0 0, PROC1 4, DMA_R 8, DMA_W 12
    CHECK(block.write(BUS_PRIORITY, 0x0100) && block.read(BUS_PRIORITY) == 0x0100);
}

static void test_ack_is_read_only() {
    fresh();
    CHECK(block.write(BUS_PRIORITY_ACK, 0) && block.read(BUS_PRIORITY_ACK) == 1 && env.warns == 0);
}

static void test_a_counter_clears_on_any_write_and_a_selector_keeps_five_bits() {
    fresh();
    for (uint32_t i = 0; i < 4; ++i) {
        block.perf_ctr[i] = 123 + i;
        CHECK(block.read(PERFCTR0 + 8 * i) == 123 + i);
        CHECK(block.write(PERFCTR0 + 8 * i, 0xFFFFFFFF) && block.read(PERFCTR0 + 8 * i) == 0);
        CHECK(block.write(PERFSEL0 + 8 * i, 0xFFFFFFE0u | i) && block.read(PERFSEL0 + 8 * i) == i);
    }
    CHECK(block.perf_sel[3] == 3 && env.warns == 0);
}

static void test_reset_puts_everything_back() {
    fresh();
    block.write(BUS_PRIORITY, 0x1111);
    for (uint32_t i = 0; i < 4; ++i) {
        block.perf_ctr[i] = 9 + i;
        block.write(PERFSEL0 + 8 * i, i);
    }
    CHECK(block.reset());
    CHECK(block.bus_priority == 0);
    for (uint32_t i = 0; i < 4; ++i) CHECK(block.perf_ctr[i] == 0 && block.perf_sel[i] == 0x1F);
}

static void test_a_new_block_is_in_its_reset_state() {
    BusctrlBlock fresh_block;
    CHECK(fresh_block.bus_priority == 0 && fresh_block.raw_write_value() == 0);
    for (uint32_t i = 0; i < 4; ++i) CHECK(fresh_block.perf_ctr[i] == 0 && fresh_block.perf_sel[i] == 0x1F);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0x28) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kBusctrlWarnRead && env.warn_offset[0] == 0x28);
    CHECK(block.read(0x3000) == 0xFFFFFFFFu);
    CHECK(env.warns == 3 && env.warn_kind[2] == kBusctrlWarnReadAtomicArea);
    CHECK(block.write(0x2C, 9));
    CHECK(env.warns == 4 && env.warn_kind[3] == kBusctrlWarnWrite && env.warn_offset[3] == 0x2C && env.warn_value[3] == 9);
    CHECK(block.read(0x02) == 0xFFFFFFFFu);  // not word aligned: no register
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    CHECK(block.write_atomic(BUS_PRIORITY, 0x0011, kAtomicSet) && block.raw_write_value() == 0x11 && block.read(BUS_PRIORITY) == 0x11);
    CHECK(block.write_atomic(BUS_PRIORITY, 0x0001, kAtomicClear) && block.read(BUS_PRIORITY) == 0x10);
    CHECK(block.write_atomic(BUS_PRIORITY, 0x1110, kAtomicXor) && block.read(BUS_PRIORITY) == 0x1100);
}

static void test_the_window_handler() {
    fresh();
    WindowHandler handler = block.window_handler();
    handler.write32(handler.ctx, BUS_PRIORITY, 0x1111, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, BUS_PRIORITY) == 0x1111);
}

int main() {
    test_reset_state();
    test_bus_priority_keeps_four_bits();
    test_ack_is_read_only();
    test_a_counter_clears_on_any_write_and_a_selector_keeps_five_bits();
    test_reset_puts_everything_back();
    test_a_new_block_is_in_its_reset_state();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_the_window_handler();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all busctrl checks passed\n");
    return 0;
}
