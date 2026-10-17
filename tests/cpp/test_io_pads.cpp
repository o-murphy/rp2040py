// Standalone checks of src/rp2040py/native/core/io.hpp and pads.hpp (see tests/test_core_cpp.py for the flags).
//
// The end-to-end proof is tests/test_pin_diff.py (the reference windows against the C++ ones over a shared pin bank, thousands of random steps, every mutant of the reference caught);
// these are the directed checks that need no Python: the per-pin registers as views of the bank's pins, the interrupt groups of both layouts, INTR's raw-bits write, proc 1 / dormant
// storage, PADS' masking, announcing and IE-driven edges, the QSPI pad order, unimplemented offsets, the atomic aliases and failure injection.
#include <cstdio>

#include "pads.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t sources[kPinSources] = {};
    int changes = 0;
    uint32_t last_pin = 0;
    int last_new = 0, last_old = 0;
    int io_irqs = 0;
    int fail_io_irq_on_call = -1, fail_change_on_call = -1;
    bool fail_source = false;
    int warns = 0;
    uint32_t warn_kinds[8] = {};
    uint32_t warn_offsets[8] = {};
    int failed = 0;
};
static Env env;

static bool on_source(void*, uint32_t source, uint32_t* out) {
    if (env.fail_source) {
        env.failed = 1;
        return false;
    }
    *out = env.sources[source];
    return true;
}
static bool on_change(void*, uint32_t pin, int new_state, int old_state) {
    const int call = env.changes++;
    env.last_pin = pin;
    env.last_new = new_state;
    env.last_old = old_state;
    if (call == env.fail_change_on_call) {
        env.failed = 1;
        return false;
    }
    return true;
}
static bool on_io_irq(void*) {
    if (env.io_irqs++ == env.fail_io_irq_on_call) {
        env.failed = 1;
        return false;
    }
    return true;
}
static constexpr uint32_t kTestBlock = 42;  // the id the blocks report their warnings under
static void on_warn(void*, uint32_t block, uint32_t kind, uint32_t offset, int64_t) {
    CHECK(block == kTestBlock);
    if (env.warns < 8) {
        env.warn_kinds[env.warns] = kind;
        env.warn_offsets[env.warns] = offset;
    }
    ++env.warns;
}

template <class Block>
static bool ok(Block& b, uint32_t offset, uint32_t expect) {
    uint32_t v = 0;
    return b.read(offset, &v) && v == expect;
}

struct Rig {
    ChipPorts ports;
    PinBank gpio, qspi;
    IoBlock io0, ioq;
    PadsBlock pads0, padsq;
    Rig() {
        PinHost host;
        host.source = on_source;
        host.on_change = on_change;
        host.io_interrupt = on_io_irq;
        host.ctx = nullptr;
        gpio.init(30, host);
        const bool always[6] = {true, true, true, true, true, true};
        qspi.init(6, host, always);
        ports.warn = on_warn;
        ports.failed = &env.failed;
        io0.init(&gpio, false, &ports, kTestBlock);
        ioq.init(&qspi, true, &ports, kTestBlock);
        pads0.init(&gpio, false, &ports, kTestBlock);
        padsq.init(&qspi, true, &ports, kTestBlock);
    }
    void clear() {
        env.changes = 0;
        env.io_irqs = 0;
        env.warns = 0;
        env.failed = 0;
        env.fail_io_irq_on_call = env.fail_change_on_call = -1;
        env.fail_source = false;
    }
};

static void test_the_per_pin_registers_are_views_of_the_pins() {
    Rig r;
    CHECK(r.io0.read(4 + 8 * 3) == 0x1F);  // GPIO3_CTRL resets to FUNCSEL = NULL
    r.io0.write(4 + 8 * 3, 0xFFFFFFFFu);
    CHECK(r.gpio.pin(3).ctrl == io_regs::CTRL_MASK);  // the reserved bits are dropped
    CHECK(r.io0.read(4 + 8 * 3) == io_regs::CTRL_MASK);
    uint32_t status = 0;
    CHECK(r.gpio.status(3, &status) && ok(r.io0, 8 * 3, status));
    CHECK(status != io_regs::CTRL_MASK);
    CHECK(r.io0.write(8 * 3, 0x5));  // STATUS is read only: nothing reaches CTRL
    CHECK(r.gpio.pin(3).ctrl == io_regs::CTRL_MASK);
    CHECK(ok(r.io0, 8 * 3, status));
    CHECK(ok(r.io0, 4 + 8 * 3, io_regs::CTRL_MASK));
    CHECK(r.io0.write(4 + 8 * 3, 0x1F));
    CHECK(r.pads0.write(0x00, 0) && r.pads0.write(0x7C, 0xDA) && r.padsq.write(0x00, 0) && r.ioq.write(0x34, 0) && r.io0.write(0x100, 0));
    CHECK(r.pads0.read(4 + 4 * 3) == 0x56);  // PADS_BANK0.GPIO3 resets to 0x56
    CHECK(r.padsq.read(0x4) == 0x5A - 0x5A + r.qspi.pin(0).pad_value);
}

static void test_a_ctrl_write_announces_the_change_of_state() {
    Rig r;
    env.sources[kSrcSioOe] = 1u << 5;
    env.sources[kSrcSioValue] = 1u << 5;
    r.clear();
    r.io0.write(4 + 8 * 5, 5);  // FUNCSEL = SIO: the pin is an output and drives high
    CHECK(env.changes == 1);
    CHECK(env.last_pin == 5 && env.last_new == kPinHigh && env.last_old == kPinInput);  // a pin's first announcement is from the state it was built in (core/pin.hpp's init)
    r.io0.write(4 + 8 * 5, 5);  // nothing changed: nothing announced
    CHECK(env.changes == 1);
}

static void test_the_interrupt_groups_of_bank0() {
    Rig r;
    r.clear();
    r.io0.write(0x100, 0x00000F00u);  // proc 0 INTE: pin 2
    CHECK(r.gpio.pin(2).irq_enable_mask == 0xF);
    CHECK(env.io_irqs == 1);
    r.io0.write(0x100, 0x00000F00u);  // unchanged: no interrupt re-evaluation
    CHECK(env.io_irqs == 1);
    CHECK(r.io0.read(0x100) == 0x00000F00u);
    r.io0.write(0x110, 0x10u);  // proc 0 INTF: pin 1, nibble 1
    CHECK(r.gpio.pin(1).irq_force_mask == 1);
    CHECK(env.io_irqs == 2);
    CHECK(r.io0.read(0x110) == 0x10u);
    CHECK(r.io0.read(0x120) == 0x110u);  // INTS = (INTR & INTE) | INTF: pin 1's force, and pin 2's LEVEL_LOW (the pad reads low) that is enabled
    const uint32_t intr = r.io0.read(0x0F0);
    CHECK((intr & 0x00000F00u) == (r.gpio.pin(2).irq_status << 8));
    r.io0.write(0x120, 0xFFFFFFFFu);  // INTS is read only
    CHECK(r.io0.read(0x120) == 0x110u);
    // the second and last word of a group
    r.io0.write(0x100 + 12, 0x1u << 4);  // pin 25
    CHECK(r.gpio.pin(25).irq_enable_mask == 1);
    CHECK(r.io0.read(0x100 + 12) == 0x10u);
    // the bank has 30 pins: the last word's two missing slots are not read or written
    r.gpio.pin(30).irq_status = 0xF;
    r.gpio.pin(30).irq_enable_mask = 0x1;
    CHECK(ok(r.io0, 0x100 + 12, 0x10u));
    CHECK(r.io0.write(0x100 + 12, 0xFFFFFFFFu));
    CHECK(r.gpio.pin(30).irq_enable_mask == 0x1 && r.gpio.pin(31).irq_enable_mask == 0);
    CHECK(r.gpio.pin(29).irq_enable_mask == 0xF);
    CHECK(r.io0.write(0x100 + 12, 0x10u));
    // eight pins to a word: pin 8 is the first of the next one
    r.gpio.pin(8).irq_enable_mask = 0x6;
    CHECK(ok(r.io0, 0x100, 0x00000F00u));
    CHECK(ok(r.io0, 0x104, 0x6u));
    CHECK(r.io0.write(0x100, 0x00000F01u));
    CHECK(r.gpio.pin(8).irq_enable_mask == 0x6 && r.gpio.pin(0).irq_enable_mask == 1);
    CHECK(r.io0.write(0x100, 0x00000F00u));
    r.gpio.pin(8).irq_enable_mask = 0;
    // proc 1 and dormant wake: stored, no interrupt line
    r.clear();
    r.io0.write(0x130, 0x3u);
    r.io0.write(0x140, 0x5u);
    r.io0.write(0x160, 0x7u << 4);
    r.io0.write(0x170, 0x9u << 8);
    CHECK(env.io_irqs == 0);
    CHECK(r.io0.read(0x130) == 0x3u && r.io0.read(0x140) == 0x5u);
    CHECK(r.io0.read(0x160) == (0x7u << 4) && r.io0.read(0x170) == (0x9u << 8));
    CHECK(r.io0.extra(1, false, 0) == 3 && r.io0.extra(1, true, 0) == 5 && r.io0.extra(2, false, 1) == 7 && r.io0.extra(2, true, 2) == 9);
    CHECK(r.io0.read(0x150) == ((0u & 3u) | 5u));  // proc 1 INTS: (INTR & INTE) | INTF for pin 0 (INTR bits of an idle pin: level low only)
    r.io0.reset();
    CHECK(r.io0.read(0x130) == 0 && r.io0.read(0x140) == 0 && r.io0.read(0x160) == 0);
}

static void test_intr_clears_edges_by_the_raw_bits_the_bus_passed() {
    Rig r;
    r.gpio.pin(0).irq_status |= pin_bits::kIrqEdgeHigh | pin_bits::kIrqEdgeLow;
    r.clear();
    // an XOR alias write: the window decodes the value against a read, but INTR is written by the raw bits
    r.io0.write_atomic(0x0F0, pin_bits::kIrqEdgeHigh, kAtomicXor);
    CHECK((r.gpio.pin(0).irq_status & pin_bits::kIrqEdgeHigh) == 0);
    CHECK((r.gpio.pin(0).irq_status & pin_bits::kIrqEdgeLow) != 0);
    CHECK(env.io_irqs == 1);
    r.io0.write_atomic(0x0F0, pin_bits::kIrqEdgeLow, kAtomicNormal);
    CHECK((r.gpio.pin(0).irq_status & pin_bits::kIrqEdgeLow) == 0);
    // the level bits are not write-1-to-clear
    const uint32_t levels = r.gpio.pin(0).irq_status & (pin_bits::kIrqLevelHigh | pin_bits::kIrqLevelLow);
    r.io0.write_atomic(0x0F0, pin_bits::kIrqLevelHigh | pin_bits::kIrqLevelLow, kAtomicNormal);
    CHECK((r.gpio.pin(0).irq_status & (pin_bits::kIrqLevelHigh | pin_bits::kIrqLevelLow)) == levels);
    CHECK(r.io0.raw_write_value() == (pin_bits::kIrqLevelHigh | pin_bits::kIrqLevelLow));
}

static void test_atomic_aliases_decode_against_a_read() {
    Rig r;
    r.io0.write_atomic(0x100, 0x3u, kAtomicSet);
    CHECK(r.io0.read(0x100) == 0x3u);
    r.io0.write_atomic(0x100, 0x1u, kAtomicClear);
    CHECK(r.io0.read(0x100) == 0x2u);
    r.io0.write_atomic(0x100, 0xFu, kAtomicXor);
    CHECK(r.io0.read(0x100) == 0xDu);
}

static void test_atomic_failures() {
    Rig r;
    r.clear();
    r.io0.write(4 + 8 * 4, 5);  // pin 4: FUNCSEL = SIO, so its STATUS asks SIO
    env.fail_source = true;
    uint32_t v = 0;
    CHECK(!r.io0.read(8 * 4, &v));
    r.clear();
    env.fail_source = true;
    CHECK(!r.io0.write_atomic(8 * 4, 1, kAtomicSet));  // the read that decodes the alias fails: nothing is written
    CHECK(r.gpio.pin(4).ctrl == 5);
    r.clear();
    env.failed = 1;  // an earlier host call parked a failure
    CHECK(!r.io0.write_atomic(0x100, 2, kAtomicSet));
    CHECK(r.gpio.pin(0).irq_enable_mask == 0);
    CHECK(!r.io0.write_atomic(0x100, 2, kAtomicNormal) || r.gpio.pin(0).irq_enable_mask == 2);
    // INTR: the interrupt line failing stops the write
    r.clear();
    r.gpio.pin(0).irq_status |= pin_bits::kIrqEdgeHigh;
    env.fail_io_irq_on_call = 0;
    CHECK(!r.io0.write_atomic(0x0F0, pin_bits::kIrqEdgeHigh, kAtomicNormal));
}

static void test_a_block_with_no_logger_stays_silent() {
    PinBank bank;
    PinHost host;
    bank.init(30, host);
    IoBlock io;
    PadsBlock pads;
    io.init(&bank, false, nullptr, kTestBlock);  // no ports: nothing is said, nothing can fail
    pads.init(&bank, false, nullptr, kTestBlock);
    CHECK(ok(io, 0x300, 0xFFFFFFFFu) && ok(io, 0x1100, 0xFFFFFFFFu) && io.write(0x300, 1));
    CHECK(ok(pads, 0x300, 0xFFFFFFFFu) && ok(pads, 0x1300, 0xFFFFFFFFu) && pads.write(0x300, 1));
}

static void test_the_atomic_area_warning_starts_above_0x1000() {
    Rig r;
    r.clear();
    CHECK(r.io0.read(0x1000) == 0xFFFFFFFFu);
    CHECK(env.warns == 1);
    r.clear();
    CHECK(r.pads0.read(0x1000) == 0xFFFFFFFFu);
    CHECK(env.warns == 1);
}

static void test_the_qspi_layout() {
    Rig r;
    r.clear();
    r.ioq.write(0x34, 0x1u | (0x2u << 4));  // proc 0 INTE: pins 0 and 1
    CHECK(r.qspi.pin(0).irq_enable_mask == 1 && r.qspi.pin(1).irq_enable_mask == 2);
    CHECK(env.io_irqs == 2);  // one per pin whose mask changed
    CHECK(r.ioq.read(0x34) == (0x1u | (0x2u << 4)));
    r.ioq.write(0x44, 0x8u << 20);  // proc 1 INTF: pin 5
    CHECK(r.ioq.read(0x44) == (0x8u << 20));
    CHECK(r.ioq.read(0x30) == (r.qspi.pin(0).irq_status | (r.qspi.pin(1).irq_status << 4) | (r.qspi.pin(2).irq_status << 8) | (r.qspi.pin(3).irq_status << 12) |
                               (r.qspi.pin(4).irq_status << 16) | (r.qspi.pin(5).irq_status << 20)));
    CHECK(r.ioq.read(4 + 8 * 5) == 0x1F);
    r.clear();
    CHECK(r.ioq.read(0x58) == 0xFFFFFFFFu);  // past the last group
    CHECK(env.warns == 1 && env.warn_kinds[0] == kRegWarnRead && env.warn_offsets[0] == 0x58);
}

static void test_unimplemented_offsets_warn() {
    Rig r;
    r.clear();
    CHECK(r.io0.read(0x300) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kinds[0] == kRegWarnRead);
    CHECK(r.io0.read(0x1100) == 0xFFFFFFFFu);  // in the atomic alias region
    CHECK(env.warns == 3 && env.warn_kinds[2] == kRegWarnReadAtomicArea);
    CHECK(r.io0.read(0x101) == 0xFFFFFFFFu);  // unaligned inside a group
    r.clear();
    r.io0.write(0x300, 1);
    CHECK(env.warns == 1 && env.warn_kinds[0] == kRegWarnWrite && env.warn_offsets[0] == 0x300);
    r.io0.write(0x102, 1);
    CHECK(env.warns == 2);
    r.clear();
    CHECK(r.pads0.read(0x300) == 0xFFFFFFFFu);
    CHECK(env.warns == 1 && env.warn_kinds[0] == kRegWarnRead);
    CHECK(r.pads0.read(0x1300) == 0xFFFFFFFFu);
    CHECK(env.warns == 3);
    r.clear();
    r.pads0.write(0x300, 1);
    CHECK(env.warns == 1 && env.warn_kinds[0] == kRegWarnWrite);
    r.padsq.write(0x7C, 1);  // SWCLK exists in PADS_BANK0 only
    CHECK(env.warns == 2);
    CHECK(r.padsq.read(0x7C) == 0xFFFFFFFFu);
}

static void test_a_pad_write_masks_announces_and_moves_the_level() {
    Rig r;
    r.clear();
    r.pads0.write(0x4 + 4 * 7, 0xFFFFFF00u | 0x56);
    CHECK(r.gpio.pin(7).pad_value == 0x56);  // 31:8 are reserved
    r.pads0.write(0x4 + 4 * 7, 0x1D);  // PUE | PDE... pulls cancel; IE off; the state code changes
    CHECK(r.gpio.pin(7).pad_value == 0x1D);
    r.clear();
    // a pull-up on a floating pin with IE: the level the interrupt logic sees rises, so an edge is latched
    r.pads0.write(0x4 + 4 * 8, 0x48);  // IE | PUE
    CHECK(r.gpio.pin(8).seen);
    CHECK((r.gpio.pin(8).irq_status & pin_bits::kIrqEdgeHigh) != 0);
    CHECK((r.gpio.pin(8).irq_status & pin_bits::kIrqLevelHigh) != 0);
    r.pads0.write(0x4 + 4 * 8, 0x08);  // IE off: it falls again
    CHECK(!r.gpio.pin(8).seen);
    CHECK((r.gpio.pin(8).irq_status & pin_bits::kIrqEdgeLow) != 0);
    // unaligned offsets in the range reach a pad, as in the reference
    CHECK(r.pads0.read(0x5) == r.gpio.pin(0).pad_value);
    CHECK(r.pads0.read(0x78) == r.gpio.pin(29).pad_value);
}

static void test_the_qspi_pads_are_listed_in_their_own_order() {
    Rig r;
    // PADS_QSPI: SCLK, SD0, SD1, SD2, SD3, SS - the bank: SCLK, SS, SD0..SD3
    const uint32_t pin_of_pad[6] = {0, 2, 3, 4, 5, 1};
    for (uint32_t pad = 0; pad < 6; ++pad) r.padsq.write(0x4 + 4 * pad, 0x10 + pad);
    for (uint32_t pad = 0; pad < 6; ++pad) {
        CHECK(r.qspi.pin(pin_of_pad[pad]).pad_value == 0x10 + pad);
        CHECK(r.padsq.read(0x4 + 4 * pad) == 0x10 + pad);
    }
}

static void test_voltage_select_and_the_debug_pads() {
    Rig r;
    CHECK(ok(r.pads0, 0x7C, 0xDA) && ok(r.pads0, 0x80, 0x5A) && ok(r.pads0, 0x00, 0) && ok(r.pads0, 0x04, 0x56));
    CHECK(r.pads0.write(0x00, 0) && r.pads0.write(0x7C, 0xDA) && r.pads0.write(0x80, 0x5A));
    r.pads0.write(0x00, 0xFFFFFFFFu);
    CHECK(r.pads0.read(0x00) == 1);
    r.pads0.write(0x7C, 0xFFFFFFFFu);
    r.pads0.write(0x80, 0x1234);
    CHECK(r.pads0.read(0x7C) == 0xFF && r.pads0.read(0x80) == 0x34);
    r.padsq.write(0x00, 3);
    CHECK(r.padsq.read(0x00) == 1);
    r.pads0.reset();
    CHECK(r.pads0.read(0x00) == 0 && r.pads0.read(0x7C) == 0xDA && r.pads0.read(0x80) == 0x5A);
    CHECK(r.padsq.read(0x00) == 1);  // another block
    r.pads0.write_atomic(0x00, 1, kAtomicSet);
    CHECK(r.pads0.read(0x00) == 1);
    r.pads0.write_atomic(0x00, 1, kAtomicXor);
    CHECK(r.pads0.read(0x00) == 0);
    CHECK(r.pads0.raw_write_value() == 1);
    r.clear();
    env.failed = 1;  // an earlier host call parked a failure
    CHECK(!r.pads0.write_atomic(0x00, 1, kAtomicSet));
    CHECK(r.pads0.read(0x00) == 0);
}

static void test_failures_stop_at_once() {
    Rig r;
    r.clear();
    env.fail_io_irq_on_call = 0;
    CHECK(!r.io0.write(0x100, 1));
    CHECK(r.gpio.pin(0).irq_enable_mask == 1);  // what was done stays done
    r.clear();
    env.fail_io_irq_on_call = 0;
    CHECK(!r.io0.write(0x110, 0x11u));  // pin 0 fails: pin 1 is never reached
    CHECK(r.gpio.pin(0).irq_force_mask == 1 && r.gpio.pin(1).irq_force_mask == 0);
    r.clear();
    env.sources[kSrcSioOe] = 1u << 2;
    env.sources[kSrcSioValue] = 1u << 2;
    env.fail_change_on_call = 0;
    CHECK(!r.io0.write(4 + 8 * 2, 5));
    CHECK(r.gpio.pin(2).ctrl == 5);
    r.clear();
    env.fail_change_on_call = 0;
    env.fail_io_irq_on_call = -1;
    env.fail_change_on_call = 0;
    CHECK(!r.pads0.write(0x4 + 4 * 12, 0x04));  // PDE only: the state code changes, the listener fails
    CHECK(r.gpio.pin(12).pad_value == 0x04);
    r.clear();
    r.pads0.write(0x4 + 4 * 9, 0x40);  // IE, no pull
    r.gpio.pin(9).irq_enable_mask = pin_bits::kIrqEdgeHigh;
    r.clear();
    env.fail_io_irq_on_call = 0;
    CHECK(!r.pads0.write(0x4 + 4 * 9, 0x48));  // IE | PUE: the level rises, the edge is enabled, the interrupt line is told, it fails
    CHECK(r.gpio.pin(9).seen);
    r.clear();
    env.fail_io_irq_on_call = 0;
    CHECK(!r.io0.write_atomic(0x100, 2, kAtomicSet));  // the failure reaches the atomic path as well
}

int main() {
    test_the_per_pin_registers_are_views_of_the_pins();
    test_a_ctrl_write_announces_the_change_of_state();
    test_the_interrupt_groups_of_bank0();
    test_intr_clears_edges_by_the_raw_bits_the_bus_passed();
    test_atomic_aliases_decode_against_a_read();
    test_atomic_failures();
    test_a_block_with_no_logger_stays_silent();
    test_the_atomic_area_warning_starts_above_0x1000();
    test_the_qspi_layout();
    test_unimplemented_offsets_warn();
    test_a_pad_write_masks_announces_and_moves_the_level();
    test_the_qspi_pads_are_listed_in_their_own_order();
    test_voltage_select_and_the_debug_pads();
    test_failures_stop_at_once();
    if (failures == 0) std::printf("test_io_pads: ok\n");
    return failures == 0 ? 0 : 1;
}
