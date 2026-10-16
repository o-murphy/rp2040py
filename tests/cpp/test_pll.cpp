// Standalone checks of src/rp2040py/native/core/pll.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_pll.py): the masks and resets, LOCK following the power bits and the reference, the output frequency (PD, BYPASS, VCOPD,
// POSTDIVPD, a divider of 0), and what a register write tells the host.
#include <cstdio>

#include "pll.hpp"

using namespace rp2040core;
using namespace rp2040core::pll_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t warn_kind[8] = {}, warn_offset[8] = {};
    int64_t warn_value[8] = {};
    int warns = 0;
    int changed = 0;
    double xosc = 12e6;
    int failed = 0;
    bool fail_changed = false;
};
static Env env;

static constexpr uint32_t kTestBlock = 42;  // the id the block reports its warnings under
static void on_warn(void*, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) {
    CHECK(block == kTestBlock);

    if (env.warns < 8) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}
static double on_ref(void*) { return env.xosc; }
static bool on_changed(void*) {
    ++env.changed;
    if (env.fail_changed) {
        env.failed = 1;
        return false;
    }
    return true;
}

static PllBlock block;

static const ChipPorts& test_ports() {
    static ChipPorts ports;
    ports.warn = on_warn;
    ports.failed = &env.failed;
    return ports;
}

static void fresh() {
    env = Env();
    PllHost host;
    host.ref_hz = on_ref;
    host.changed = on_changed;
    block.init(&test_ports(), kTestBlock, host);
    block.reset();
}

// pico-sdk pll_init(): 12 MHz / 1 * 125 = 1500 MHz, / (6 * 2) = 125 MHz.
static void sdk_init(uint32_t refdiv, uint32_t fbdiv, uint32_t pd1, uint32_t pd2) {
    block.write(REG_CS, refdiv);
    block.write(REG_FBDIV_INT, fbdiv);
    block.write(REG_PWR, block.pwr & ~(PWR_PD | PWR_VCOPD));
    block.write(REG_PRIM, (pd1 << 16) | (pd2 << 12));
    block.write(REG_PWR, block.pwr & ~PWR_POSTDIVPD);
}

static void test_reset_values() {
    fresh();
    CHECK(block.read(REG_CS) == 0x1);  // REFDIV = 1, no LOCK: powered down
    CHECK(block.read(REG_PWR) == 0x2D);
    CHECK(block.read(REG_FBDIV_INT) == 0);
    CHECK(block.read(REG_PRIM) == 0x77000);
    CHECK(block.frequency() == 0.0);
    CHECK(!block.locked());
}

static void test_masks() {
    fresh();
    block.write(REG_CS, 0xFFFFFFFFu);
    CHECK(block.read(REG_CS) == (CS_WRITE_MASK) );  // LOCK is read only and clear: PD / VCOPD are set
    block.write(REG_PWR, 0xFFFFFFFFu);
    CHECK(block.read(REG_PWR) == 0x2D);
    block.write(REG_FBDIV_INT, 0xFFFFFFFFu);
    CHECK(block.read(REG_FBDIV_INT) == 0xFFF);
    block.write(REG_PRIM, 0xFFFFFFFFu);
    CHECK(block.read(REG_PRIM) == 0x77000);
    CHECK(env.changed == 4);  // every register write tells the host
}

static void test_the_sdk_sequence() {
    fresh();
    block.write(REG_CS, 1);
    block.write(REG_FBDIV_INT, 125);
    CHECK((block.read(REG_CS) & CS_LOCK) == 0);
    block.write(REG_PWR, PWR_POSTDIVPD);  // PD and VCOPD cleared
    CHECK(block.read(REG_CS) == (CS_LOCK | 1));
    block.write(REG_PRIM, (6u << 16) | (2u << 12));
    CHECK(block.frequency() == 0.0);  // POSTDIVPD still set
    block.write(REG_PWR, 0);
    CHECK(block.frequency() == 125e6);
    CHECK(env.changed == 5);
}

static void test_power_bits_and_the_output() {
    fresh();
    sdk_init(1, 125, 6, 2);
    CHECK(block.frequency() == 125e6);
    block.write(REG_PWR, PWR_PD);
    CHECK(block.frequency() == 0.0 && !block.locked());
    block.write(REG_PWR, PWR_VCOPD);
    CHECK(block.frequency() == 0.0 && !block.locked());
    block.write(REG_PWR, PWR_POSTDIVPD);
    CHECK(block.frequency() == 0.0 && block.locked());
    block.write(REG_PWR, PWR_DSMPD);  // stored, no effect on the output
    CHECK(block.frequency() == 125e6);
}

static void test_bypass() {
    fresh();
    block.write(REG_FBDIV_INT, 100);
    block.write(REG_CS, CS_BYPASS | 2);  // powered down: nothing
    CHECK(block.frequency() == 0.0);
    block.write(REG_PWR, PWR_VCOPD | PWR_POSTDIVPD);  // PD clear, VCO and dividers off: the bypass still passes
    CHECK(block.frequency() == 6e6);
    block.write(REG_PWR, PWR_PD);
    CHECK(block.frequency() == 0.0);
}

static void test_dividers_of_zero_count_as_one() {
    fresh();
    block.write(REG_CS, 0);  // REFDIV 0
    block.write(REG_FBDIV_INT, 100);
    block.write(REG_PRIM, 0);
    block.write(REG_PWR, 0);
    CHECK(block.refdiv() == 0 && block.reference() == 12e6);
    CHECK(block.frequency() == 1200e6);
    block.write(REG_PRIM, 3u << 16);
    CHECK(block.frequency() == 400e6);
}

static void test_no_crystal_no_reference() {
    fresh();
    env.xosc = 0.0;
    sdk_init(1, 125, 6, 2);
    CHECK(block.frequency() == 0.0 && !block.locked());
    CHECK((block.read(REG_CS) & CS_LOCK) == 0);
    block.write(REG_CS, CS_BYPASS | 1);
    CHECK(block.frequency() == 0.0);
    env.xosc = 12e6;  // the crystal starts: the next read sees it
    CHECK(block.frequency() == 12e6 && block.locked());
}

static void test_reset_announces_nothing() {
    fresh();
    sdk_init(2, 125, 6, 2);
    block.write(REG_CS, CS_BYPASS | 2);
    const int before = env.changed;
    block.reset();
    CHECK(env.changed == before);
    CHECK(block.read(REG_PWR) == 0x2D && block.read(REG_PRIM) == 0x77000 && block.read(REG_CS) == 1 && block.read(REG_FBDIV_INT) == 0);
}

static void test_unimplemented_offsets_warn() {
    fresh();
    CHECK(block.read(0x10) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kind[0] == kPllWarnRead && env.warn_offset[0] == 0x10);
    CHECK(block.write(0x14, 5));
    CHECK(env.warns == 2 && env.warn_kind[1] == kPllWarnWrite && env.warn_value[1] == 5);
    CHECK(env.changed == 0);
    fresh();
    CHECK(block.read(0x1004) == 0xFFFFFFFFu);
    CHECK(env.warns == 2 && env.warn_kind[1] == kPllWarnReadAtomicArea);
    fresh();
    CHECK(block.read(0x1000) == 0xFFFFFFFFu);  // the atomic area starts above 0x1000 (the reference's `offset > 0x1000`)
    CHECK(env.warns == 1);
}

static void test_a_block_with_no_host_function_still_writes() {
    fresh();
    PllHost bare;
    block.init(&test_ports(), kTestBlock, bare);
    CHECK(block.write(REG_FBDIV_INT, 7));  // no `changed`, no `ref_hz`, no `warn`
    CHECK(block.read(REG_FBDIV_INT) == 7 && block.frequency() == 0.0 && block.read(0x10) == 0xFFFFFFFFu);
}

static void test_a_failing_host_is_reported() {
    fresh();
    env.fail_changed = true;
    CHECK(!block.write(REG_FBDIV_INT, 5));
    CHECK(block.read(REG_FBDIV_INT) == 5);  // the write itself had happened
    CHECK(!block.write_atomic(REG_FBDIV_INT, 1, kAtomicSet));  // the notification fails again
    env.fail_changed = false;
    env.failed = 1;  // a failure parked earlier: an alias write stops after decoding, before writing
    const uint32_t before = block.read(REG_FBDIV_INT);
    CHECK(!block.write_atomic(REG_FBDIV_INT, 0x10, kAtomicSet));
    CHECK(block.read(REG_FBDIV_INT) == before);
}

static void test_atomic_aliases_decode_against_a_read() {
    fresh();
    block.write_atomic(REG_FBDIV_INT, 0x0F0, kAtomicNormal);
    block.write_atomic(REG_FBDIV_INT, 0x00F, kAtomicSet);
    CHECK(block.read(REG_FBDIV_INT) == 0x0FF);
    block.write_atomic(REG_FBDIV_INT, 0x0F0, kAtomicClear);
    CHECK(block.read(REG_FBDIV_INT) == 0x00F);
    block.write_atomic(REG_FBDIV_INT, 0x003, kAtomicXor);
    CHECK(block.read(REG_FBDIV_INT) == 0x00C);
    // an alias write decodes against the read value, which includes LOCK for CS: clear of it never sticks
    block.write_atomic(REG_PWR, PWR_PD | PWR_VCOPD, kAtomicClear);
    CHECK(block.read(REG_CS) == (CS_LOCK | 1));
    CHECK(block.raw_write_value() == (PWR_PD | PWR_VCOPD));
}

int main() {
    test_reset_values();
    test_masks();
    test_the_sdk_sequence();
    test_power_bits_and_the_output();
    test_bypass();
    test_dividers_of_zero_count_as_one();
    test_no_crystal_no_reference();
    test_reset_announces_nothing();
    test_unimplemented_offsets_warn();
    test_atomic_aliases_decode_against_a_read();
    test_a_block_with_no_host_function_still_writes();
    test_a_failing_host_is_reported();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("pll: all checks passed\n");
    return 0;
}
