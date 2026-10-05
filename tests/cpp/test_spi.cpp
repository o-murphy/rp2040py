// Standalone checks of src/rp2040py/native/core/spi.hpp and fifo.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_spi.py) that the lockstep differential (tests/test_spi_diff.py) also pins, against a recording host with a
// device that can be the reference's default (completes with 0 from inside the callback), an immediate one, a deferred one (completes on a later call) or a silent one.
#include <cstdio>

#include "fifo.hpp"
#include "spi.hpp"

using namespace rp2040core;
using namespace rp2040core::spi_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

enum Mode { kDefault = 0, kImmediate = 1, kDeferred = 2, kSilent = 3 };

static SpiBlock spi;

struct Env {
    bool irq_calls[256];
    int irq_n = 0;
    uint32_t dreq_calls[256];  // (tx ? 2 : 0) | asserted
    int dreq_n = 0;
    uint32_t sent[64];
    int sent_n = 0;
    uint32_t warn_kind[32], warn_offset[32];
    int64_t warn_value[32];
    int warns = 0;
    int mode = kDefault;
    uint32_t next_rx = 0;
    int failed = 0;
    bool fail_irq = false, fail_dreq = false, fail_transmit = false;
};
static Env env;

static bool on_irq(void*, bool level) {
    if (env.irq_n < 256) env.irq_calls[env.irq_n] = level;
    ++env.irq_n;
    if (env.fail_irq) { env.failed = 1; return false; }
    return true;
}
static bool on_dreq(void*, bool tx, bool asserted) {
    if (env.dreq_n < 256) env.dreq_calls[env.dreq_n] = (tx ? 2u : 0u) | (asserted ? 1u : 0u);
    ++env.dreq_n;
    if (env.fail_dreq) { env.failed = 1; return false; }
    return true;
}
static bool on_transmit(void*, uint32_t value) {
    if (env.sent_n < 64) env.sent[env.sent_n] = value;
    ++env.sent_n;
    if (env.fail_transmit) { env.failed = 1; return false; }
    if (env.mode == kDefault) return spi.complete_transmit(0);              // the reference's default: straight back, from inside the callback
    if (env.mode == kImmediate) { env.next_rx += 37; return spi.complete_transmit(env.next_rx); }
    return true;                                                           // deferred / silent: the device holds the byte
}
static void on_warn(void*, uint32_t kind, uint32_t offset, int64_t value) {
    if (env.warns < 32) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}

static void fresh(int mode = kDefault) {
    env = Env();
    env.mode = mode;
    SpiHost host;
    host.irq = on_irq;
    host.dreq = on_dreq;
    host.transmit = on_transmit;
    host.warn = on_warn;
    host.failed = &env.failed;
    spi.init(host);
    spi.control0 = spi.control1 = spi.dma_control = spi.clock_divisor = 0;
    spi.int_raw = spi.int_enable = 0;
    spi.busy = false;
    spi.rx.reset();
    spi.tx.reset();
    (void)spi.write_atomic(ICR, 0, kAtomicNormal);  // raw_write_value back to 0
    env.irq_n = env.dreq_n = env.sent_n = env.warns = 0;
}
static uint32_t rd(uint32_t offset) { return spi.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { return spi.write_atomic(offset, value, atomic); }
static bool last_irq() { return env.irq_n > 0 && env.irq_calls[env.irq_n - 1]; }
static void dr(uint32_t value) { (void)spi.write(DR, value); }

static void test_fifo_template() {
    Fifo<4> f;
    CHECK(f.size() == 4 && f.empty() && !f.full() && f.count() == 0);
    CHECK(f.pull() == 0 && f.peek() == 0);                       // empty reads 0
    for (uint32_t i = 1; i <= 6; ++i) f.push(i);                 // 5 and 6 are dropped
    CHECK(f.full() && f.count() == 4 && f.at(0) == 1 && f.at(3) == 4);
    CHECK(f.pull() == 1 && f.peek() == 2 && f.count() == 3);
    f.push(9);                                                   // wraps
    CHECK(f.at(0) == 2 && f.at(3) == 9 && f.full());
    f.push(10);
    CHECK(f.at(3) == 9);                                         // still full: dropped
    f.reset();
    CHECK(f.empty() && f.pull() == 0 && f.peek() == 0);          // the storage still holds an old value: empty must not show it
    Fifo<2> g;
    g.push(7);
    (void)g.pull();
    CHECK(g.peek() == 0);
    f.push(0xFFFFFFFFu);
    CHECK(f.pull() == 0xFFFFFFFFu);                              // whole words
}

static void test_a_default_constructed_block_is_at_power_on() {
    static SpiBlock block;
    CHECK(block.control0 == 0 && block.control1 == 0 && block.dma_control == 0 && block.clock_divisor == 0 && block.int_raw == 0 && block.int_enable == 0);
    CHECK(!block.busy && block.rx.empty() && block.tx.empty() && block.raw_write_value() == 0);
}

static void test_power_on_publishes_both_dreqs() {
    fresh();
    CHECK(spi.power_on());
    CHECK(env.dreq_n == 2 && env.dreq_calls[0] == 3 && env.dreq_calls[1] == 0);   // TX asserted (FIFO not full), RX not (FIFO empty)
    CHECK(rd(SR) == (SR_TNF | SR_TFE));
    CHECK(rd(PERIPHID0) == 0x22 && rd(PERIPHID1) == 0x10 && rd(PERIPHID2) == 0x34 && rd(PERIPHID3) == 0x00);
    CHECK(rd(PCELLID0) == 0x0D && rd(PCELLID1) == 0xF0 && rd(PCELLID2) == 0x05 && rd(PCELLID3) == 0xB1);
}

static void test_registers_are_stored_whole_except_cpsr() {
    fresh();
    wr(CR0, 0xFFFFFFF1u);
    wr(CR1, 0xFFFFFFF2u);
    wr(IMSC, 0xFFFFFFF3u);
    wr(DMACR, 0xFFFFFFF4u);
    wr(CPSR, 0xFFFFFFF5u);
    CHECK(rd(CR0) == 0xFFFFFFF1u && rd(CR1) == 0xFFFFFFF2u && rd(IMSC) == 0xFFFFFFF3u && rd(DMACR) == 0xFFFFFFF4u);
    CHECK(rd(CPSR) == 0xF4);
    wr(CR0, 0xFFFFFFFFu);
    wr(CR1, 0xFFFFFFFFu);
    CHECK(spi.enabled() && spi.data_bits() == 16);
    wr(CR1, 0);
    CHECK(!spi.enabled());
}

static void test_a_written_value_is_masked_to_dss_plus_one_bits() {
    fresh(kSilent);
    wr(CR0, 0x7);                                                // 8 bits
    dr(0x1FF);
    CHECK(env.sent_n == 1 && env.sent[0] == 0xFF);
    wr(CR0, 0x3);                                                // 4 bits
    dr(0xAB);
    CHECK(spi.tx.count() == 1 && spi.tx.at(0) == 0xB);           // queued behind the one in flight, masked now
    wr(CR0, 0x0);                                                // an invalid size: 1 bit
    dr(0xFF);
    CHECK(spi.tx.at(1) == 1);
    wr(CR0, 0xF);                                                // 16 bits
    dr(0xFFFFFu);
    CHECK(spi.tx.at(2) == 0xFFFF);
}

static void test_the_tx_fifo_drops_a_write_when_full_and_the_status_follows() {
    fresh(kSilent);
    wr(CR0, 0xF);
    for (uint32_t i = 0; i < 9; ++i) dr(i + 1);                  // 1 in flight, 8 in the FIFO
    CHECK(env.sent_n == 1 && env.sent[0] == 1 && spi.tx.full() && spi.busy);
    CHECK(rd(SR) == (SR_BSY | SR_TNF * 0 | 0));                  // busy, TX full (no TNF), not empty (no TFE), RX empty
    const int dreq_before = env.dreq_n, irq_before = env.irq_n;
    dr(0x55);                                                    // dropped silently - and nothing is published
    CHECK(spi.tx.count() == 8 && spi.tx.at(7) == 9 && env.dreq_n == dreq_before && env.irq_n == irq_before);
    CHECK(env.dreq_calls[env.dreq_n - 2] == 2);                  // the TX DREQ was withdrawn when the FIFO filled (the RX publication follows it)
}

static void test_sr_reports_busy_not_full_and_empty_separately() {
    fresh(kSilent);
    wr(CR0, 0xF);
    CHECK(rd(SR) == (SR_TNF | SR_TFE));                          // idle
    dr(1);
    CHECK(rd(SR) == (SR_BSY | SR_TNF | SR_TFE));                 // one in flight: busy, the FIFO still empty
    dr(2);
    CHECK(rd(SR) == (SR_BSY | SR_TNF));                          // one queued: not empty (no TFE), not full
    spi.busy = false;
    spi.tx.reset();
    spi.tx.push(5);                                              // a queued byte with the wire free still reads as busy
    CHECK(rd(SR) == (SR_BSY | SR_TNF));
    spi.tx.reset();
    CHECK(rd(SR) == (SR_TNF | SR_TFE));
    spi.rx.push(1);
    CHECK(rd(SR) == (SR_TNF | SR_TFE | SR_RNE));
}

static void test_the_tx_interrupt_is_raw_while_at_most_four_are_queued() {
    fresh(kSilent);
    wr(CR0, 0xF);
    wr(IMSC, INT_TX);
    for (uint32_t i = 0; i < 5; ++i) dr(i);                      // 1 in flight, 4 queued
    CHECK(spi.tx.count() == 4 && (rd(RIS) & INT_TX) != 0 && last_irq());
    dr(9);                                                       // 5 queued
    CHECK((rd(RIS) & INT_TX) == 0 && !last_irq());
}

static void test_the_default_device_completes_from_inside_the_callback() {
    fresh(kDefault);
    wr(CR0, 0xF);
    for (uint32_t i = 0; i < 5; ++i) dr(i + 1);
    CHECK(env.sent_n == 5);
    for (int i = 0; i < 5; ++i) CHECK(env.sent[i] == static_cast<uint32_t>(i + 1));
    CHECK(!spi.busy && spi.tx.empty() && spi.rx.count() == 5);   // each byte came straight back as 0
    for (uint32_t i = 0; i < 5; ++i) CHECK(rd(DR) == 0);
    CHECK(spi.rx.empty());
}

static void test_a_deferred_device_completes_later_and_the_next_byte_follows() {
    fresh(kDeferred);
    wr(CR0, 0xF);
    dr(0x11);
    dr(0x22);
    CHECK(env.sent_n == 1 && spi.busy && spi.tx.count() == 1);
    CHECK(spi.complete_transmit(0xA1));                          // the first finishes: its answer is queued and the second goes out
    CHECK(env.sent_n == 2 && env.sent[1] == 0x22 && spi.busy && spi.tx.empty() && spi.rx.count() == 1);
    CHECK(spi.complete_transmit(0xA2));
    CHECK(!spi.busy && spi.rx.count() == 2);
    CHECK(rd(DR) == 0xA1 && rd(DR) == 0xA2);                     // whole, unmasked
}

static void test_a_completion_with_nothing_sent_is_accepted() {
    fresh(kSilent);
    CHECK(spi.complete_transmit(0x1234) && spi.rx.count() == 1 && !spi.busy);
    CHECK(rd(DR) == 0x1234);
}

static void test_rx_threshold_overrun_and_icr() {
    fresh(kSilent);
    wr(IMSC, INT_RX | INT_ROR | INT_RT);
    for (uint32_t i = 0; i < 3; ++i) spi.complete_transmit(i);
    CHECK((rd(RIS) & INT_RX) == 0 && !last_irq());               // 3 < 4
    spi.complete_transmit(3);
    CHECK((rd(RIS) & INT_RX) != 0 && last_irq());                // 4 or more
    for (uint32_t i = 4; i < 8; ++i) spi.complete_transmit(i);
    CHECK(spi.rx.full() && (rd(SR) & SR_RFF) != 0);
    spi.complete_transmit(99);                                   // overrun: dropped, ROR set
    CHECK((rd(RIS) & INT_ROR) != 0 && spi.rx.count() == 8 && spi.rx.at(7) == 7);
    spi.int_raw |= INT_RT;
    CHECK((rd(RIS) & INT_TX) != 0);                              // the TX FIFO is empty: TXINTR is raw as well
    CHECK(wr(ICR, 0xFFFFFFFFu) && (rd(RIS) & (INT_RT | INT_ROR)) == 0 && (rd(RIS) & INT_RX) != 0 && (rd(RIS) & INT_TX) != 0);  // ICR clears RT and ROR only
    CHECK(wr(ICR, INT_TX | INT_RX) && (rd(RIS) & INT_RX) != 0);                                       // and nothing else
}

static void test_icr_drops_the_line_when_it_clears_the_last_enabled_interrupt() {
    fresh(kSilent);
    wr(IMSC, INT_ROR);
    for (uint32_t i = 0; i < 8; ++i) spi.complete_transmit(i);
    CHECK(!last_irq());
    spi.complete_transmit(8);                                    // the ninth overruns: ROR is raw and enabled, so the line rises at once
    CHECK((rd(RIS) & INT_ROR) != 0 && last_irq());
    CHECK(wr(ICR, INT_ROR) && !last_irq());                      // and drops with the clear
}

static void test_the_line_is_not_touched_while_the_enabled_status_does_not_change() {
    fresh(kSilent);
    wr(IMSC, 0);
    const int before = env.irq_n;
    for (uint32_t i = 0; i < 6; ++i) spi.complete_transmit(i);   // RXINTR raw flips, but nothing is enabled: the status is the same
    CHECK(env.irq_n == before);
}

static void test_the_second_fifo_update_after_a_send_flips_the_tx_threshold() {
    fresh(kDeferred);
    wr(CR0, 0xF);
    for (uint32_t i = 0; i < 6; ++i) dr(i);                      // 1 in flight, 5 queued
    spi.complete_transmit(0);                                    // 5 queued while TXINTR is evaluated (cleared), 4 after the next byte goes out (raw again)
    CHECK(spi.tx.count() == 4 && (rd(RIS) & INT_TX) != 0);
}

static void test_raw_write_value_and_failed_report_what_the_bus_and_host_did() {
    fresh();
    CHECK(wr(CR1, 0x1234) && spi.raw_write_value() == 0x1234);
    CHECK(wr(IMSC, 0x1, kAtomicSet) && spi.raw_write_value() == 0x1);
    CHECK(!spi.failed());
    env.failed = 1;
    CHECK(spi.failed());
    env.failed = 0;
}

static void test_dr_reads_drain_publish_the_rx_dreq_and_read_zero_when_empty() {
    fresh(kSilent);
    CHECK(rd(DR) == 0);
    spi.complete_transmit(5);
    CHECK(env.dreq_calls[env.dreq_n - 1] == 1);                  // RX asserted: not empty
    CHECK(rd(DR) == 5);
    CHECK(env.dreq_calls[env.dreq_n - 1] == 0);                  // withdrawn: empty again
    CHECK(rd(DR) == 0);
}

static void test_mis_and_the_line_follow_imsc() {
    fresh(kSilent);
    spi.complete_transmit(1);
    for (int i = 0; i < 3; ++i) spi.complete_transmit(i);        // 4 in the RX FIFO: RXINTR raw
    CHECK(rd(MIS) == 0 && !last_irq());
    CHECK(wr(IMSC, INT_RX) && rd(MIS) == INT_RX && last_irq());
    CHECK(wr(IMSC, 0) && rd(MIS) == 0 && !last_irq());
}

static void test_reset_clears_everything_republishes_the_dreqs_and_keeps_the_host() {
    fresh(kSilent);
    wr(CR0, 0x7);
    wr(CR1, 2);
    wr(IMSC, 0xF);
    wr(CPSR, 10);
    wr(DMACR, 3);
    for (uint32_t i = 0; i < 4; ++i) dr(i);                      // busy, 3 queued
    spi.complete_transmit(0);
    const int64_t raw = spi.raw_write_value();
    env.irq_n = env.dreq_n = 0;
    CHECK(spi.reset());
    CHECK(rd(CR0) == 0 && rd(CR1) == 0 && rd(CPSR) == 0 && rd(IMSC) == 0 && rd(DMACR) == 0 && rd(RIS) == 0);
    CHECK(spi.rx.empty() && spi.tx.empty() && !spi.busy);
    CHECK(env.dreq_n == 2 && env.dreq_calls[0] == 3 && env.dreq_calls[1] == 0);  // republished: TX asserted, RX withdrawn
    CHECK(env.irq_n == 1 && !env.irq_calls[0]);
    CHECK(spi.raw_write_value() == raw);
    dr(7);                                                       // the host is still wired: the byte goes out
    CHECK(env.sent[env.sent_n - 1] == 1);                        // (CR0 is 0 again: a 1-bit mask, 7 & 1)
}

static void test_unimplemented_offsets_warn_and_read_all_ones() {
    fresh();
    CHECK(rd(0x28) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kSpiWarnRead && env.warn_offset[0] == 0x28);
    env.warns = 0;
    CHECK(rd(0x1004) == 0xFFFFFFFFu && env.warns == 2 && env.warn_kind[1] == kSpiWarnReadAtomicArea);
    env.warns = 0;
    CHECK(rd(0x1001) == 0xFFFFFFFFu && env.warns == 2);          // above 0x1000, aligned or not
    env.warns = 0;
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1);
    env.warns = 0;
    CHECK(wr(0x30, 0x77) && env.warns == 1 && env.warn_kind[0] == kSpiWarnWrite && env.warn_offset[0] == 0x30 && env.warn_value[0] == 0x77);
    env.warns = 0;
    CHECK(wr(SR, 1) && wr(RIS, 1) && wr(MIS, 1) && env.warns == 3);  // read-only registers
}

static void test_alias_writes_decode_against_a_read_with_its_side_effects() {
    fresh(kSilent);
    wr(CR0, 0xF);
    spi.complete_transmit(0x5A);
    spi.complete_transmit(0x6B);
    CHECK(wr(DR, 0x0F, kAtomicSet));                             // the decode READS DR: 0x5A leaves the FIFO ...
    CHECK(spi.rx.count() == 1 && spi.rx.at(0) == 0x6B);
    CHECK(env.sent_n == 1 && env.sent[0] == (0x5Au | 0x0F));     // ... and what goes out is read | raw
    wr(IMSC, 0x3, kAtomicSet);
    wr(IMSC, 0x1, kAtomicXor);
    wr(IMSC, 0x2, kAtomicClear);
    CHECK(rd(IMSC) == 0);
}

static void test_icr_clears_the_raw_value_whatever_the_alias_like_the_uart() {
    fresh(kSilent);
    spi.int_raw = INT_RT | INT_ROR;
    env.warns = 0;
    CHECK(wr(ICR, INT_ROR, kAtomicClear));                       // the alias decode reads ICR first (unimplemented: a warning, all ones) - but ICR clears the RAW bits
    CHECK(env.warns >= 1 && env.warn_offset[0] == ICR);
    CHECK(rd(RIS) == INT_RT);
    CHECK(wr(ICR, INT_RT, kAtomicXor) && rd(RIS) == 0);
    spi.int_raw = INT_RT | INT_ROR;
    CHECK(spi.write(ICR, 0xFFFFFFFFu));                          // a direct write keeps the raw value the last atomic write left (INT_RT)
    CHECK(rd(RIS) == INT_ROR);
}

static void test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised() {
    fresh(kSilent);
    env.fail_transmit = true;
    CHECK(!spi.write(DR, 0x42));                                 // the callback raised: the byte left the FIFO, the block is busy, the FIFOs were not updated
    CHECK(spi.busy && spi.tx.empty() && env.sent_n == 1);
    CHECK((spi.int_raw & INT_TX) == 0);
    env.fail_transmit = false;
    env.failed = 0;

    fresh(kSilent);
    env.fail_irq = true;
    CHECK(!wr(IMSC, INT_TX) && rd(IMSC) == INT_TX);              // stored, then the line update raised
    env.fail_irq = false;
    env.failed = 0;

    fresh(kSilent);
    env.fail_dreq = true;
    CHECK(!spi.power_on());
    CHECK(!spi.complete_transmit(1) && spi.rx.count() == 1);     // the value was pushed, then the DREQ publication raised
    env.fail_dreq = false;
    env.failed = 0;

    fresh(kDeferred);
    wr(CR0, 0xF);
    wr(IMSC, INT_RX);
    dr(1);
    dr(2);                                                       // 1 in flight, 1 queued
    for (uint32_t i = 0; i < 3; ++i) spi.rx.push(i);             // three received, the RX interrupt not yet raw
    env.fail_irq = true;
    CHECK(!spi.complete_transmit(9));                            // the fourth makes RXINTR raw: the line changes, the call fails, do_tx is not reached
    CHECK(spi.rx.count() == 4 && spi.tx.count() == 1 && env.sent_n == 1);   // the queued byte did not go out
    env.fail_irq = false;
    env.failed = 0;
    CHECK(!spi.failed());
}

static void test_a_failing_line_update_on_an_overrun_stops_the_completion() {
    fresh(kDeferred);
    wr(CR0, 0xF);
    wr(IMSC, INT_ROR);
    dr(1);
    dr(2);                                                       // 1 in flight, 1 queued
    for (uint32_t i = 0; i < 8; ++i) spi.rx.push(i);             // the RX FIFO is full
    env.fail_irq = true;
    CHECK(!spi.complete_transmit(9));                            // the overrun's line update raised: ROR is set, the value dropped, the queued byte not sent
    CHECK((spi.int_raw & INT_ROR) != 0 && spi.rx.count() == 8 && spi.tx.count() == 1 && env.sent_n == 1);
    env.fail_irq = false;
    env.failed = 0;
}

static void test_the_window_handler_is_the_bus_entry_point() {
    fresh(kSilent);
    WindowHandler h = spi.window_handler();
    h.write32(h.ctx, IMSC, 5, kAtomicNormal);
    h.write32(h.ctx, IMSC, 2, kAtomicSet);
    CHECK(h.read32(h.ctx, IMSC) == 7);
    spi.complete_transmit(0x77);
    CHECK(h.read32(h.ctx, SR) == (SR_TNF | SR_TFE | SR_RNE));
    CHECK(h.read32(h.ctx, DR) == 0x77);
}

int main() {
    test_fifo_template();
    test_a_default_constructed_block_is_at_power_on();
    test_power_on_publishes_both_dreqs();
    test_registers_are_stored_whole_except_cpsr();
    test_a_written_value_is_masked_to_dss_plus_one_bits();
    test_the_tx_fifo_drops_a_write_when_full_and_the_status_follows();
    test_sr_reports_busy_not_full_and_empty_separately();
    test_the_tx_interrupt_is_raw_while_at_most_four_are_queued();
    test_the_default_device_completes_from_inside_the_callback();
    test_a_deferred_device_completes_later_and_the_next_byte_follows();
    test_a_completion_with_nothing_sent_is_accepted();
    test_rx_threshold_overrun_and_icr();
    test_icr_drops_the_line_when_it_clears_the_last_enabled_interrupt();
    test_the_line_is_not_touched_while_the_enabled_status_does_not_change();
    test_the_second_fifo_update_after_a_send_flips_the_tx_threshold();
    test_raw_write_value_and_failed_report_what_the_bus_and_host_did();
    test_dr_reads_drain_publish_the_rx_dreq_and_read_zero_when_empty();
    test_mis_and_the_line_follow_imsc();
    test_reset_clears_everything_republishes_the_dreqs_and_keeps_the_host();
    test_unimplemented_offsets_warn_and_read_all_ones();
    test_alias_writes_decode_against_a_read_with_its_side_effects();
    test_icr_clears_the_raw_value_whatever_the_alias_like_the_uart();
    test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised();
    test_a_failing_line_update_on_an_overrun_stops_the_completion();
    test_the_window_handler_is_the_bus_entry_point();
    if (failures == 0) std::printf("test_spi: ok\n");
    return failures == 0 ? 0 : 1;
}
