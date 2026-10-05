// Standalone checks of src/rp2040py/native/core/uart.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_uart.py) that the lockstep differential (tests/test_uart_diff.py) also pins, against a recording host,
// so the header is exercised - and mutation-tested - without Cython.
#include <cstdio>

#include "uart.hpp"

using namespace rp2040core;
using namespace rp2040core::uart_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    // the interrupt line: every call, in order
    bool irq_calls[256];
    int irq_n = 0;
    // the TX DREQ
    bool dreq_calls[64];
    int dreq_n = 0;
    // transmitted bytes
    uint32_t bytes[64];
    int bytes_n = 0;
    int baud_calls = 0;
    // warnings
    uint32_t warn_kind[32], warn_offset[32];
    int64_t warn_value[32];
    int warns = 0;
    // failure injection: the host function that fails (its error is "parked" by raising `failed`)
    int failed = 0;
    bool fail_irq = false, fail_dreq = false, fail_byte = false, fail_baud = false;
};
static Env env;

static bool on_irq(void*, bool level) {
    if (env.irq_n < 256) env.irq_calls[env.irq_n] = level;
    ++env.irq_n;
    if (env.fail_irq) { env.failed = 1; return false; }
    return true;
}
static bool on_dreq(void*, bool asserted) {
    if (env.dreq_n < 64) env.dreq_calls[env.dreq_n] = asserted;
    ++env.dreq_n;
    if (env.fail_dreq) { env.failed = 1; return false; }
    return true;
}
static bool on_byte(void*, uint32_t byte) {
    if (env.bytes_n < 64) env.bytes[env.bytes_n] = byte;
    ++env.bytes_n;
    if (env.fail_byte) { env.failed = 1; return false; }
    return true;
}
static bool on_baud(void*) {
    ++env.baud_calls;
    if (env.fail_baud) { env.failed = 1; return false; }
    return true;
}
static void on_warn(void*, uint32_t kind, uint32_t offset, int64_t value) {
    if (env.warns < 32) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}

static UartBlock uart;

static void fresh() {
    env = Env();
    UartHost host;
    host.irq = on_irq;
    host.dreq = on_dreq;
    host.on_byte = on_byte;
    host.baud_changed = on_baud;
    host.warn = on_warn;
    host.failed = &env.failed;
    uart.init(host);
    // a block as the reference's constructor leaves it
    uart.ctrl = CR_RXE | CR_TXE;
    uart.line_ctrl = 0;
    uart.int_divisor = uart.frac_divisor = 0;
    uart.interrupt_mask = uart.interrupt_status = 0;
    uart.rx_reset();
    (void)uart.write_atomic(ICR, 0, kAtomicNormal);  // raw_write_value back to 0
    env.irq_n = env.dreq_n = env.bytes_n = env.baud_calls = env.warns = 0;
}
static uint32_t rd(uint32_t offset) { return uart.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { return uart.write_atomic(offset, value, atomic); }
static bool last_irq() { return env.irq_n > 0 && env.irq_calls[env.irq_n - 1]; }

static void test_power_on_state() {
    fresh();
    CHECK(rd(CR) == (CR_RXE | CR_TXE));
    CHECK(rd(FR) == (FR_TXFE | FR_RXFE));
    CHECK(rd(LCR_H) == 0 && rd(IBRD) == 0 && rd(FBRD) == 0 && rd(IMSC) == 0 && rd(IRIS) == 0 && rd(IMIS) == 0);
    CHECK(rd(PERIPHID0) == 0x11 && rd(PERIPHID1) == 0x10 && rd(PERIPHID2) == 0x34 && rd(PERIPHID3) == 0x00);
    CHECK(rd(PCELLID0) == 0x0D && rd(PCELLID1) == 0xF0 && rd(PCELLID2) == 0x05 && rd(PCELLID3) == 0xB1);
    CHECK(env.warns == 0);
}

static void test_a_default_constructed_block_is_at_power_on() {
    static UartBlock block;  // not through fresh(): the member defaults themselves
    UartHost host;
    host.irq = on_irq;
    host.dreq = on_dreq;
    host.on_byte = on_byte;
    host.baud_changed = on_baud;
    host.warn = on_warn;
    host.failed = &env.failed;
    block.init(host);
    CHECK(block.read(CR) == (CR_RXE | CR_TXE) && block.read(FR) == (FR_TXFE | FR_RXFE));
    CHECK(block.read(IBRD) == 0 && block.read(FBRD) == 0 && block.read(LCR_H) == 0 && block.read(IMSC) == 0 && block.read(IRIS) == 0);
    CHECK(block.rx_count() == 0 && block.raw_write_value() == 0);
}

static void test_a_received_byte_raises_rxintr_and_the_line_only_when_unmasked() {
    fresh();
    CHECK(uart.feed_byte(0x41));
    CHECK(rd(IRIS) == INT_RX && rd(IMIS) == 0 && !last_irq());  // masked: the status is there, the line is not
    CHECK(env.irq_n == 1);                                      // feed_byte always updates the line
    CHECK(wr(IMSC, INT_RX) && last_irq() && rd(IMIS) == INT_RX);
    CHECK(rd(FR) == (FR_TXFE));                                 // not empty, not full
    CHECK(rd(DR) == 0x41);
    CHECK(rd(IRIS) == 0 && !last_irq());                        // the last byte out clears RXINTR and drops the line
}

static void test_a_dr_read_keeps_rxintr_while_bytes_remain_and_empty_reads_zero() {
    fresh();
    wr(IMSC, INT_RX);
    uart.feed_byte(1);
    uart.feed_byte(2);
    CHECK(rd(DR) == 1 && rd(IRIS) == INT_RX && last_irq());
    CHECK(rd(DR) == 2 && rd(IRIS) == 0 && !last_irq());
    CHECK(rd(DR) == 0 && rd(IRIS) == 0);                        // empty: 0, and no RXINTR
    CHECK(uart.rx_count() == 0);
}

static void test_the_rx_fifo_is_32_deep_and_drops_what_does_not_fit() {
    fresh();
    for (uint32_t i = 0; i < 40; ++i) uart.feed_byte(i + 1);
    CHECK(uart.rx_count() == 32 && uart.rx_full());
    CHECK((rd(FR) & FR_RXFF) != 0 && (rd(FR) & FR_RXFE) == 0);
    for (uint32_t i = 0; i < 32; ++i) CHECK(rd(DR) == i + 1);   // the first 32 survive, in order; 33..40 were dropped
    CHECK(rd(DR) == 0 && (rd(FR) & FR_RXFE) != 0);
}

static void test_full_means_exactly_32_and_rx_at_follows_the_ring() {
    fresh();
    for (uint32_t i = 0; i < 31; ++i) uart.feed_byte(i + 1);
    CHECK(!uart.rx_full() && (rd(FR) & FR_RXFF) == 0);           // 31 is not full
    uart.feed_byte(32);
    CHECK(uart.rx_full() && (rd(FR) & FR_RXFF) != 0);
    for (uint32_t i = 0; i < 10; ++i) (void)rd(DR);              // drain 10, then add 5: the ring has wrapped
    for (uint32_t i = 0; i < 5; ++i) uart.feed_byte(100 + i);
    CHECK(uart.rx_count() == 27);
    CHECK(uart.rx_at(0) == 11 && uart.rx_at(21) == 32 && uart.rx_at(22) == 100 && uart.rx_at(26) == 104);
    CHECK(uart.rx_peek() == 11);
}

static void test_the_fed_value_is_kept_whole_and_the_ring_wraps() {
    fresh();
    uart.feed_byte(0xDEADBEEFu);
    CHECK(rd(DR) == 0xDEADBEEFu);
    for (int round = 0; round < 5; ++round) {                    // wrap the ring several times
        for (uint32_t i = 0; i < 20; ++i) uart.feed_byte(round * 100 + i);
        for (uint32_t i = 0; i < 20; ++i) CHECK(rd(DR) == static_cast<uint32_t>(round * 100 + i));
    }
}

static void test_a_dr_write_transmits_at_once_and_raises_txintr() {
    fresh();
    wr(IMSC, INT_TX);
    CHECK(!last_irq());                                          // an empty FIFO alone raises nothing: TXINTR is an edge
    CHECK(wr(DR, 0x1A5));
    CHECK(env.bytes_n == 1 && env.bytes[0] == 0xA5);             // masked to 8 bits
    CHECK(rd(IRIS) == INT_TX && last_irq());
    CHECK(rd(FR) == (FR_TXFE | FR_RXFE));                        // FR never shows TXFF or BUSY
}

static void test_icr_clears_the_raw_value_bits_for_good() {
    fresh();
    wr(IMSC, INT_TX | INT_RX);
    wr(DR, 1);
    uart.feed_byte(2);
    CHECK(rd(IRIS) == (INT_TX | INT_RX));
    CHECK(wr(ICR, INT_TX) && rd(IRIS) == INT_RX && last_irq());  // RX still pending: the line stays
    CHECK(wr(ICR, INT_RX) && rd(IRIS) == 0 && !last_irq());      // and TXINTR is not re-asserted
    CHECK(rd(IRIS) == 0);
}

static void test_icr_uses_the_raw_value_whatever_the_alias_and_a_direct_write_the_stale_one() {
    fresh();
    wr(DR, 1);
    uart.feed_byte(2);
    env.warns = 0;
    // CLR alias of ICR: the alias decode reads ICR first (an unimplemented read: a warning), but ICR clears the RAW bits
    CHECK(wr(ICR, INT_TX, kAtomicClear));
    CHECK(rd(IRIS) == INT_RX);
    CHECK(env.warns >= 1 && env.warn_kind[0] == kUartWarnRead && env.warn_offset[0] == ICR);
    CHECK(uart.raw_write_value() == INT_TX);
    // a direct write keeps the raw value the last atomic write left
    CHECK(uart.write(DR, 3));                                    // direct: raw_write_value stays INT_TX
    CHECK(rd(IRIS) == (INT_RX | INT_TX));
    CHECK(uart.write(ICR, 0x7FF));                               // value ignored: raw is still INT_TX
    CHECK(rd(IRIS) == INT_RX);
}

static void test_the_baud_divisors_are_masked_and_announced_every_time() {
    fresh();
    CHECK(wr(IBRD, 0x12345) && rd(IBRD) == 0x2345 && env.baud_calls == 1);
    CHECK(wr(FBRD, 0xFF) && rd(FBRD) == 0x3F && env.baud_calls == 2);
    CHECK(wr(FBRD, 0x3F) && env.baud_calls == 3);                // even with no change
    CHECK(wr(LCR_H, 0xFFFFFFFFu) && rd(LCR_H) == 0xFFFFFFFFu);   // stored whole
    CHECK(env.baud_calls == 3);                                  // LCR_H announces nothing
}

static void test_imsc_is_masked_to_eleven_bits_and_drives_the_line() {
    fresh();
    uart.feed_byte(1);
    CHECK(wr(IMSC, 0xFFFFFFFFu) && rd(IMSC) == 0x7FF && last_irq());
    CHECK(wr(IMSC, 0) && !last_irq());
}

static void test_cr_drives_the_tx_dreq_from_uarten_alone() {
    fresh();
    CHECK(wr(CR, CR_UARTEN | CR_TXE | CR_RXE) && env.dreq_n == 1 && env.dreq_calls[0]);
    CHECK(wr(CR, CR_TXE | CR_RXE) && env.dreq_n == 2 && !env.dreq_calls[1]);   // TXE alone does not assert it
    CHECK(wr(CR, CR_UARTEN) && env.dreq_calls[2]);                              // UARTEN alone does
    CHECK(wr(CR, 0x1FFFFu) && rd(CR) == 0x1FFFFu && env.dreq_calls[3]);          // stored whole
    CHECK(wr(CR, 0) && !env.dreq_calls[4] && env.dreq_n == 5);
}

static void test_alias_writes_decode_against_a_read_with_its_side_effects() {
    fresh();
    uart.feed_byte(0x55);
    uart.feed_byte(0x66);
    CHECK(wr(DR, 0x0F, kAtomicSet));                             // the decode READS DR first: one byte leaves the FIFO
    CHECK(uart.rx_count() == 1 && rd(DR) == 0x66);
    CHECK(env.bytes_n == 1 && env.bytes[0] == (0x55u | 0x0F));   // and the transmitted byte is read | raw
    CHECK(wr(IMSC, 0x30, kAtomicSet) && rd(IMSC) == 0x30);
    CHECK(wr(IMSC, 0x10, kAtomicXor) && rd(IMSC) == 0x20);
    CHECK(wr(IMSC, 0x20, kAtomicClear) && rd(IMSC) == 0);
}

static void test_unimplemented_offsets_warn_and_read_all_ones() {
    fresh();
    CHECK(rd(0x04) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kUartWarnRead && env.warn_offset[0] == 0x04);
    env.warns = 0;
    CHECK(rd(0x1004) == 0xFFFFFFFFu && env.warns == 2);          // above 0x1000 the atomic-region warning follows
    CHECK(env.warn_kind[1] == kUartWarnReadAtomicArea);
    env.warns = 0;
    CHECK(rd(0x1001) == 0xFFFFFFFFu && env.warns == 2);          // above 0x1000, aligned or not
    env.warns = 0;
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1);          // 0x1000 itself is not above it
    env.warns = 0;
    CHECK(wr(0x08, 0x1234) && env.warns == 1 && env.warn_kind[0] == kUartWarnWrite && env.warn_offset[0] == 0x08 && env.warn_value[0] == 0x1234);
    env.warns = 0;
    CHECK(wr(FR, 7) && env.warns == 1);                          // FR is read-only: a write is unimplemented
    CHECK(wr(IRIS, 7) && env.warns == 2);
}

static void test_reset_clears_the_registers_and_the_fifo_and_drops_the_line_only() {
    fresh();
    wr(IMSC, INT_RX);
    uart.feed_byte(9);
    wr(IBRD, 67);
    wr(FBRD, 52);
    wr(LCR_H, 0x70);
    wr(CR, CR_UARTEN);
    wr(DR, 1);                                                   // TXINTR pending too
    wr(IMSC, INT_RX | INT_TX);                                   // the last write: raw_write_value is now nonzero
    CHECK(rd(IRIS) == (INT_RX | INT_TX));
    const int64_t raw = uart.raw_write_value();
    env.irq_n = env.dreq_n = env.baud_calls = 0;
    CHECK(uart.reset());
    CHECK(rd(CR) == (CR_RXE | CR_TXE) && rd(LCR_H) == 0 && rd(IBRD) == 0 && rd(FBRD) == 0 && rd(IMSC) == 0 && rd(IRIS) == 0);
    CHECK(uart.rx_empty() && rd(FR) == (FR_TXFE | FR_RXFE));
    CHECK(env.irq_n == 1 && !env.irq_calls[0]);                  // the line dropped, once
    CHECK(env.dreq_n == 0 && env.baud_calls == 0);               // no DREQ change, no announcement
    CHECK(uart.raw_write_value() == raw);                        // not part of the reset
}

static void test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised() {
    fresh();
    env.fail_byte = true;
    CHECK(!wr(DR, 1));                                           // on_byte raised: no TXINTR was set
    CHECK(rd(IRIS) == 0);
    env.fail_byte = false;
    env.failed = 0;

    env.fail_irq = true;
    CHECK(!uart.feed_byte(5));                                   // the byte is in, RXINTR set, then the line update raised
    CHECK(uart.rx_count() == 1 && rd(IRIS) == INT_RX);
    env.fail_irq = false;
    env.failed = 0;

    env.fail_baud = true;
    CHECK(!wr(IBRD, 7) && rd(IBRD) == 7);                        // the register took the value, the announcement raised
    env.fail_baud = false;
    env.failed = 0;

    env.fail_dreq = true;
    CHECK(!wr(CR, CR_UARTEN) && rd(CR) == CR_UARTEN);
    env.fail_dreq = false;
    env.failed = 0;

    env.fail_irq = true;
    CHECK(!uart.reset());                                        // reset ran to the end, the line drop raised
    CHECK(uart.rx_empty() && rd(CR) == (CR_RXE | CR_TXE));
    env.fail_irq = false;
    env.failed = 0;
    CHECK(!uart.failed());
}

static void test_the_failure_flag_is_what_failed_reports() {
    fresh();
    CHECK(!uart.failed());
    env.failed = 1;
    CHECK(uart.failed());
    env.failed = 0;
}

static void test_the_window_handler_is_the_bus_entry_point() {
    fresh();
    WindowHandler h = uart.window_handler();
    h.write32(h.ctx, IMSC, INT_RX, kAtomicNormal);
    h.write32(h.ctx, IMSC, INT_TX, kAtomicSet);
    CHECK(h.read32(h.ctx, IMSC) == (INT_RX | INT_TX));
    uart.feed_byte(0x77);
    CHECK(h.read32(h.ctx, FR) == FR_TXFE);
    CHECK(h.read32(h.ctx, DR) == 0x77);
}

int main() {
    test_power_on_state();
    test_a_default_constructed_block_is_at_power_on();
    test_a_received_byte_raises_rxintr_and_the_line_only_when_unmasked();
    test_a_dr_read_keeps_rxintr_while_bytes_remain_and_empty_reads_zero();
    test_the_rx_fifo_is_32_deep_and_drops_what_does_not_fit();
    test_full_means_exactly_32_and_rx_at_follows_the_ring();
    test_the_fed_value_is_kept_whole_and_the_ring_wraps();
    test_a_dr_write_transmits_at_once_and_raises_txintr();
    test_icr_clears_the_raw_value_bits_for_good();
    test_icr_uses_the_raw_value_whatever_the_alias_and_a_direct_write_the_stale_one();
    test_the_baud_divisors_are_masked_and_announced_every_time();
    test_imsc_is_masked_to_eleven_bits_and_drives_the_line();
    test_cr_drives_the_tx_dreq_from_uarten_alone();
    test_alias_writes_decode_against_a_read_with_its_side_effects();
    test_unimplemented_offsets_warn_and_read_all_ones();
    test_reset_clears_the_registers_and_the_fifo_and_drops_the_line_only();
    test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised();
    test_the_failure_flag_is_what_failed_reports();
    test_the_window_handler_is_the_bus_entry_point();
    if (failures == 0) std::printf("test_uart: ok\n");
    return failures == 0 ? 0 : 1;
}
