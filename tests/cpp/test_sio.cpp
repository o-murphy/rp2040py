// Standalone checks of src/rp2040py/native/core/sio.hpp and interpolator.hpp (see tests/test_core_cpp.py for the flags).
// The exhaustive comparison with the Python reference is tests/test_sio_parity.py; these are known-value checks.
#include <cstdio>

#include "sio.hpp"

using namespace rp2040core;
using namespace rp2040core::sio_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t gpio_in = 0, qspi_in = 0;
    uint32_t updates[64];
    int update_count = 0;
    uint32_t cycles = 0;
    uint32_t warn_kind[32], warn_offset[32];
    int64_t warn_value[32];
    int warns = 0;
    bool fail_updates = false;
};

static uint32_t host_gpio_in(void* c) { return static_cast<Env*>(c)->gpio_in; }
static uint32_t host_qspi_in(void* c) { return static_cast<Env*>(c)->qspi_in; }
static bool host_update_pins(void* c, uint32_t mask) {
    Env* e = static_cast<Env*>(c);
    if (e->update_count < 64) e->updates[e->update_count++] = mask;
    return !e->fail_updates;
}
static void host_add_cycles(void* c, uint32_t n) { static_cast<Env*>(c)->cycles += n; }
static void host_warn(void* c, uint32_t kind, uint32_t offset, int64_t value) {
    Env* e = static_cast<Env*>(c);
    if (e->warns < 32) {
        e->warn_kind[e->warns] = kind;
        e->warn_offset[e->warns] = offset;
        e->warn_value[e->warns] = value;
        ++e->warns;
    }
}

int main() {
    Env env;
    SioHost host;
    host.gpio_in = host_gpio_in;
    host.qspi_in = host_qspi_in;
    host.update_pins = host_update_pins;
    host.add_cycles = host_add_cycles;
    host.warn = host_warn;
    host.ctx = &env;
    SioBlock sio;
    sio.init(host);

    // Spinlocks: reading acquires (returns the bit, or 0 if held), any write releases; SPINLOCK_ST shows the state.
    CHECK(sio.read32(SPINLOCK0 + 4 * 3) == 8u);
    CHECK(sio.read32(SPINLOCK0 + 4 * 3) == 0u);
    CHECK(sio.read32(SPINLOCK_ST) == 8u);
    CHECK(sio.read32(SPINLOCK0 + 4 * 31) == 0x80000000u);
    sio.write32(SPINLOCK0 + 4 * 3, 0);
    CHECK(sio.read32(SPINLOCK_ST) == 0x80000000u && env.update_count == 0);  // a spinlock write never re-evaluates pins

    // Pin inputs come from the host; CPUID is core 0.
    env.gpio_in = 0x1234;
    env.qspi_in = 0x21;
    CHECK(sio.read32(GPIO_IN) == 0x1234u && sio.read32(GPIO_HI_IN) == 0x21u && sio.read32(CPUID) == 0);

    // GPIO outputs: masked to 30 bits, SET/CLR/XOR aliases, and only the pins that changed are re-evaluated.
    sio.write32(GPIO_OE, 0xFFFFFFFF);
    CHECK(sio.read32(GPIO_OE) == 0x3FFFFFFFu && env.update_count == 1 && env.updates[0] == 0x3FFFFFFFu);
    env.update_count = 0;
    sio.write32(GPIO_OUT_SET, 0x5);
    sio.write32(GPIO_OUT_XOR, 0x1);
    sio.write32(GPIO_OUT_CLR, 0x4);
    CHECK(sio.read32(GPIO_OUT) == 0u && env.update_count == 3);
    CHECK(env.updates[0] == 0x5u && env.updates[1] == 0x1u && env.updates[2] == 0x4u);
    env.update_count = 0;
    sio.write32(GPIO_OUT, 0);  // unchanged: no re-evaluation
    CHECK(env.update_count == 0);
    sio.write32(GPIO_HI_OUT, 0x3F);  // QSPI pins never trigger one
    sio.write32(GPIO_HI_OE_SET, 0x2);
    CHECK(sio.read32(GPIO_HI_OUT) == 0x3Fu && sio.read32(GPIO_HI_OE) == 0x2u && env.update_count == 0);
    CHECK(sio.read32(GPIO_OUT_SET) == 0 && sio.read32(GPIO_HI_OE_XOR) == 0);  // the write-only aliases read as 0
    sio.write32(GPIO_OUT_CLR, -1);  // a "clear everything" mask given as a negative number
    sio.write32(GPIO_OUT_SET, 0x3FFFFFFFFLL);  // a set mask wider than 30 bits is masked
    CHECK(sio.read32(GPIO_OUT) == 0x3FFFFFFFu);
    env.update_count = 0;

    // The divider: quotient/remainder, 8 cycles per recompute, CSR ready+dirty, quotient read clears "dirty".
    env.cycles = 0;
    sio.write32(DIV_UDIVIDEND, 100);
    sio.write32(DIV_UDIVISOR, 7);
    CHECK(sio.read32(DIV_CSR) == 0b11u && env.cycles == 16);
    CHECK(sio.read32(DIV_QUOTIENT) == 14u && sio.read32(DIV_CSR) == 0b01u && sio.read32(DIV_REMAINDER) == 2u);
    CHECK(sio.read_wide(DIV_QUOTIENT) > 14.28 && sio.read_wide(DIV_QUOTIENT) < 14.29);  // genuinely fractional, as upstream
    sio.write32(DIV_SDIVIDEND, 0xFFFFFFF0LL);  // -16 as an unsigned 32-bit value
    sio.write32(DIV_SDIVISOR, 3);
    CHECK(sio.read32(DIV_QUOTIENT) == 0xFFFFFFFBu);  // int(-5.33) = -5
    CHECK(sio.read32(DIV_REMAINDER) == 0xFFFFFFFFu);  // -16 - 3*(-5) = -1 (truncating remainder)
    sio.write32(DIV_UDIVISOR, 0);  // divide by zero: quotient is -1 if the dividend is > 0 as written, else 1
    CHECK(sio.read32(DIV_QUOTIENT) == 0xFFFFFFFFu && sio.read32(DIV_REMAINDER) == 0xFFFFFFF0u);
    sio.write32(DIV_SDIVIDEND, 0);
    CHECK(sio.read32(DIV_QUOTIENT) == 1u);
    CHECK(env.warns == 0);

    // A divisor that is non-zero as written but zero as a 32-bit value cannot divide: the failure is reported, state untouched.
    sio.write32(DIV_UDIVIDEND, 50);
    sio.write32(DIV_UDIVISOR, 5);
    const uint32_t quotient_before = sio.read32(DIV_QUOTIENT);
    const uint32_t cycles_before = env.cycles;
    CHECK(!sio.write32(DIV_UDIVISOR, 0x100000000LL));
    CHECK(env.warns == 1 && env.warn_kind[0] == kSioFailDivideByZero);
    CHECK(env.cycles == cycles_before && sio.read32(DIV_QUOTIENT) == quotient_before);
    env.warns = 0;

    // Registers the block does not know warn with the unmasked value, and the FIFO warns as "not implemented".
    CHECK(sio.read32(0x200) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kSioWarnReadInvalid);
    sio.write32(0x200, -7);
    CHECK(env.warns == 2 && env.warn_kind[1] == kSioWarnWriteInvalid && env.warn_value[1] == -7);
    CHECK(sio.read32(FIFO_RD) == 0xFFFFFFFFu && env.warns == 3 && env.warn_kind[2] == kSioWarnFifo);
    CHECK(sio.read32(INTERP0 + I_BASE_1AND0) == 0xFFFFFFFFu && env.warns == 4);  // write-only: invalid to read
    sio.write32(INTERP0 + I_POP_FULL, 1);                                           // read-only: invalid to write
    CHECK(env.warns == 5);
    env.warns = 0;

    // Interpolator 0, plain mode: result = (accum0 >> shift) & mask; POP writes the lane results back to the accumulators.
    sio.write32(INTERP0 + I_CTRL_LANE0, (4u << 0) | (0u << 5) | (7u << 10));  // shift 4, mask bits 0..7
    sio.write32(INTERP0 + I_ACCUM0, 0xABCD);
    CHECK(sio.read32(INTERP0 + I_PEEK_LANE0) == 0xBCu);
    CHECK(sio.read32(INTERP0 + I_ACCUM0) == 0xABCDu);  // peek changes nothing
    sio.write32(INTERP0 + I_BASE0, 0x1000);
    CHECK(sio.read32(INTERP0 + I_PEEK_LANE0) == 0x10BCu);  // the lane result is BASE0 + the masked shift (values from interpolator.py)
    CHECK(sio.read32(INTERP0 + I_POP_LANE0) == 0x10BCu && sio.read32(INTERP0 + I_ACCUM0) == 0x10BCu);  // POP writes it back
    // Interpolator 1 is independent.
    CHECK(sio.read32(INTERP1 + I_ACCUM0) == 0u);

    // reset() puts everything back, including the interpolators.
    sio.reset();
    CHECK(sio.read32(GPIO_OE) == 0 && sio.read32(INTERP0 + I_ACCUM0) == 0 && sio.read32(SPINLOCK_ST) == 0);
    CHECK(sio.read32(DIV_CSR) == 1 && sio.div_divisor == 0);   // datasheet: DIV_CSR.READY resets to 1, DIV_xDIVISOR to 0

    // Datasheet audit: the QSPI output registers are 6 bits, the interpolators' ACCUMn_ADD 24 bits, FORCE_MSB a field of each lane's own CTRL and not part of what POP writes back.
    sio.write32(GPIO_HI_OUT, 0xFFFFFFFFu);
    sio.write32(GPIO_HI_OE, 0xFFFFFFFFu);
    CHECK(sio.read32(GPIO_HI_OUT) == 0x3F && sio.read32(GPIO_HI_OE) == 0x3F);
    sio.write32(GPIO_HI_OUT_CLR, 0xFFFFFFFFu);
    sio.write32(GPIO_HI_OUT_XOR, 0xFFFFFFFFu);
    CHECK(sio.read32(GPIO_HI_OUT) == 0x3F);
    sio.reset();
    sio.write32(INTERP0 + I_ACCUM0_ADD, 0xFF000005u);          // bits 31:24 are reserved: not added
    CHECK(sio.read32(INTERP0 + I_ACCUM0) == 5u);
    sio.write32(INTERP0 + I_ACCUM1_ADD, 0x1000003u);
    CHECK(sio.read32(INTERP0 + I_ACCUM1) == 3u);
    sio.reset();
    sio.write32(INTERP0 + I_CTRL_LANE0, 0x1Fu << 10);          // lane 0: mask 0..31, no FORCE_MSB
    sio.write32(INTERP0 + I_CTRL_LANE1, (0x1Fu << 10) | (3u << 19));   // lane 1: FORCE_MSB = 3
    sio.write32(INTERP0 + I_ACCUM1, 7);
    CHECK(sio.read32(INTERP0 + I_PEEK_LANE1) == (7u | (3u << 28)) && sio.read32(INTERP0 + I_PEEK_LANE0) == 0u);   // lane 1's own field, lane 0 unaffected
    CHECK(sio.read32(INTERP0 + I_POP_LANE1) == (7u | (3u << 28)));
    CHECK(sio.read32(INTERP0 + I_ACCUM1) == 7u);               // the accumulator took the datapath value, without the forced bits

    // A failing pin update is reported and the rest of the write's work (nothing here) is skipped.
    env.fail_updates = true;
    CHECK(!sio.write32(GPIO_OUT, 1));
    env.fail_updates = false;

    // The bus entry points are the same protocol.
    WindowHandler h = sio.window_handler();
    h.write32(h.ctx, GPIO_OUT, 0x8, kAtomicNormal);
    CHECK(h.read32(h.ctx, GPIO_OUT) == 0x8u);

    std::printf(failures ? "FAILED: %d\n" : "sio: all checks passed\n", failures);
    return failures ? 1 : 0;
}
