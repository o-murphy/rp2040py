// Standalone checks of src/rp2040py/native/core/tbman.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_tbman.py) that the lockstep differential (tests/test_ident_diff.py) also pins, against a recording host.
#include <cstdio>

#include "tbman.hpp"

using namespace rp2040core;
using namespace rp2040core::tbman_regs;

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

static TbmanBlock block;

static void fresh() {
    env = Env();
    TbmanHost host;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
}

static void test_platform_reads_asic() {
    fresh();
    CHECK(block.read(REG_PLATFORM) == 1u);  // datasheet 2.22: ASIC (bit 0) resets to 1, FPGA (bit 1) to 0
    CHECK(env.warns == 0);
}

static void test_a_write_to_platform_is_ignored_silently() {
    fresh();
    CHECK(block.write(REG_PLATFORM, 0xFFFFFFFFu) && block.read(REG_PLATFORM) == 1u && env.warns == 0);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0x4) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kTbmanWarnRead && env.warn_offset[0] == 0x4);
    CHECK(block.read(0x3000) == 0xFFFFFFFFu);
    CHECK(env.warns == 3 && env.warn_kind[2] == kTbmanWarnReadAtomicArea);
    CHECK(block.write(0x4, 9));
    CHECK(env.warns == 4 && env.warn_kind[3] == kTbmanWarnWrite && env.warn_offset[3] == 0x4 && env.warn_value[3] == 9);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    CHECK(block.write_atomic(REG_PLATFORM, 0x2, kAtomicSet) && block.raw_write_value() == 2 && env.warns == 0);
    CHECK(block.write_atomic(0x4, 0x1, kAtomicClear));
    CHECK(env.warns == 2 && env.warn_kind[0] == kTbmanWarnRead && env.warn_kind[1] == kTbmanWarnWrite);
    CHECK(block.read(REG_PLATFORM) == 1u);
}

static void test_reset_and_the_window_handler() {
    fresh();
    CHECK(block.reset());
    WindowHandler handler = block.window_handler();
    CHECK(handler.read32(handler.ctx, REG_PLATFORM) == 1u);
    handler.write32(handler.ctx, REG_PLATFORM, 0, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, REG_PLATFORM) == 1u && env.warns == 0);
}

int main() {
    test_platform_reads_asic();
    test_a_write_to_platform_is_ignored_silently();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_reset_and_the_window_handler();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all tbman checks passed\n");
    return 0;
}
