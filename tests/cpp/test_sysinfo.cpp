// Standalone checks of src/rp2040py/native/core/sysinfo.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_sysinfo.py) that the lockstep differential (tests/test_ident_diff.py) also pins, against a recording host.
#include <cstdio>

#include "sysinfo.hpp"

using namespace rp2040core;
using namespace rp2040core::sysinfo_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t rom_version = 0;
    bool fail_rom = false;
    int failed = 0;
    int rom_calls = 0;
    uint32_t warn_kind[16] = {}, warn_offset[16] = {};
    int64_t warn_value[16] = {};
    int warns = 0;
};
static Env env;

static bool on_rom_version(void*, uint32_t* version) {
    ++env.rom_calls;
    if (env.fail_rom) { env.failed = 1; return false; }
    *version = env.rom_version;
    return true;
}
static void on_warn(void*, uint32_t kind, uint32_t offset, int64_t value) {
    if (env.warns < 16) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}

static SysInfoBlock block;

static void fresh() {
    env = Env();
    SysInfoHost host;
    host.rom_version = on_rom_version;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
}

// pico-sdk platform.c: manufacturer 0x927, part 2; platform.h: revision "1 for B0/B1, 2 for B2" (the ROM's version byte: 1 B0, 2 B1, 3 B2)
static void test_chip_id_follows_the_bootrom_version() {
    fresh();
    env.rom_version = 0;  // no bootrom loaded
    CHECK(block.read(CHIP_ID) == 0x10002927u);
    env.rom_version = 1;
    CHECK(block.read(CHIP_ID) == 0x10002927u);  // B0
    env.rom_version = 2;
    CHECK(block.read(CHIP_ID) == 0x10002927u);  // B1
    env.rom_version = 3;
    CHECK(block.read(CHIP_ID) == 0x20002927u);  // B2
    env.rom_version = 4;
    CHECK(block.read(CHIP_ID) == 0x10002927u);  // only 3 is B2
    CHECK(((block.read(CHIP_ID) >> 12) & 0xFFFFu) == PART && (block.read(CHIP_ID) & 0xFFFu) == MANUFACTURER);
    CHECK(env.rom_calls == 7);  // asked on every read: the ROM is loaded after the chip is built
}

static void test_the_other_registers_are_constants() {
    fresh();
    CHECK(block.read(PLATFORM) == 0x2u);
    CHECK(block.read(GITREF_RP2040) == 0xE0C912E8u);
    CHECK(env.rom_calls == 0 && env.warns == 0);
}

static void test_writes_to_the_registers_are_ignored_silently() {
    fresh();
    env.rom_version = 3;
    CHECK(block.write(CHIP_ID, 0xFFFFFFFFu));
    CHECK(block.write(PLATFORM, 0xFFFFFFFFu));
    CHECK(block.write(GITREF_RP2040, 0xFFFFFFFFu));
    CHECK(env.warns == 0);
    CHECK(block.read(CHIP_ID) == 0x20002927u && block.read(PLATFORM) == 2u && block.read(GITREF_RP2040) == 0xE0C912E8u);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0x8) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kSysInfoWarnRead && env.warn_offset[0] == 0x8);
    CHECK(block.read(0x2004) == 0xFFFFFFFFu);  // above 0x1000: the atomic area warns a second time
    CHECK(env.warns == 3 && env.warn_kind[1] == kSysInfoWarnRead && env.warn_kind[2] == kSysInfoWarnReadAtomicArea);
    CHECK(block.write(0x8, 0x1234));
    CHECK(env.warns == 4 && env.warn_kind[3] == kSysInfoWarnWrite && env.warn_offset[3] == 0x8 && env.warn_value[3] == 0x1234);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    fresh();
    env.rom_version = 3;
    CHECK(block.write_atomic(CHIP_ID, 0xFF, kAtomicSet) && block.raw_write_value() == 0xFF);
    CHECK(env.rom_calls == 1 && env.warns == 0);  // the decode read CHIP_ID; the write was ignored
    CHECK(block.write_atomic(0x8, 0x1, kAtomicXor));
    CHECK(env.warns == 2 && env.warn_kind[0] == kSysInfoWarnRead && env.warn_kind[1] == kSysInfoWarnWrite);  // read, then write, of an unimplemented offset
    CHECK(block.write_atomic(PLATFORM, 0x5, kAtomicNormal) && block.raw_write_value() == 0x5);
}

static void test_a_failing_rom_version_read_stops_the_atomic_write() {
    fresh();
    env.fail_rom = true;
    CHECK(block.read(CHIP_ID) == 0 && block.failed());
    env = Env();
    env.fail_rom = true;
    CHECK(!block.write_atomic(CHIP_ID, 1, kAtomicSet));  // the decode read failed: nothing is written
    CHECK(env.warns == 0);
}

static void test_reset_has_nothing_to_put_back() {
    fresh();
    CHECK(block.reset());
    env.rom_version = 3;
    CHECK(block.reset() && block.read(CHIP_ID) == 0x20002927u);
}

static void test_the_window_handler_is_the_bus_entry_point() {
    fresh();
    env.rom_version = 3;
    WindowHandler handler = block.window_handler();
    CHECK(handler.read32(handler.ctx, CHIP_ID) == 0x20002927u);
    handler.write32(handler.ctx, PLATFORM, 7, kAtomicNormal);
    CHECK(handler.read32(handler.ctx, PLATFORM) == 2u && env.warns == 0);
}

int main() {
    test_chip_id_follows_the_bootrom_version();
    test_the_other_registers_are_constants();
    test_writes_to_the_registers_are_ignored_silently();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_a_failing_rom_version_read_stops_the_atomic_write();
    test_reset_has_nothing_to_put_back();
    test_the_window_handler_is_the_bus_entry_point();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all sysinfo checks passed\n");
    return 0;
}
