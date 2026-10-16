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
    // the DREQs (TX and RX)
    bool dreq_calls[256];
    bool dreq_rx[256];  // which of the two requests the call was for
    int dreq_n = 0;
    // transmitted bytes
    uint32_t bytes[64];
    int bytes_n = 0;
    int baud_calls = 0;
    // the time of one bit on the wire, as the host computes it from clk_peri and the divider (0: no baud rate configured, a character leaves at once)
    double bit_nanos = 0.0;
    // warnings
    uint32_t warn_kind[32], warn_offset[32];
    int64_t warn_value[32];
    int warns = 0;
    // failure injection: the host function that fails (its error is "parked" by raising `failed`)
    int failed = 0;
    bool fail_irq = false, fail_dreq = false, fail_byte = false, fail_baud = false, fail_bit = false;
};
static Env env;
static constexpr uint32_t kTestBlock = 42, kTestIrq = 20, kTestDreqTx = 20;  // the id its warnings carry, its NVIC line and its TX request (RX follows)

static bool on_irq(void*, uint32_t line, bool level) {
    CHECK(line == kTestIrq);
    if (env.irq_n < 256) env.irq_calls[env.irq_n] = level;
    ++env.irq_n;
    if (env.fail_irq) { env.failed = 1; return false; }
    return true;
}
static bool on_dreq(void*, uint32_t number, bool asserted) {
    CHECK(number == kTestDreqTx || number == kTestDreqTx + 1);
    const bool rx = number == kTestDreqTx + 1;
    if (env.dreq_n < 256) {
        env.dreq_calls[env.dreq_n] = asserted;
        env.dreq_rx[env.dreq_n] = rx;
    }
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
static bool on_bit(void*, double* nanos) {
    *nanos = env.bit_nanos;
    if (env.fail_bit) { env.failed = 1; return false; }
    return true;
}
static void on_warn(void*, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) {
    CHECK(block == kTestBlock);
    if (env.warns < 32) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}

static const ChipPorts& test_ports() {
    static ChipPorts ports;
    ports.irq = on_irq;
    ports.dreq = on_dreq;
    ports.warn = on_warn;
    ports.failed = &env.failed;
    return ports;
}

static UartBlock uart;
static Clock clk;

static void fresh_power_on() {
    env = Env();
    UartHost host;
    host.on_byte = on_byte;
    host.baud_changed = on_baud;
    host.bit_ns = on_bit;
    uart.init(&clk, &test_ports(), kTestBlock, kTestIrq, kTestDreqTx, host);
    (void)uart.reset();  // the TX FIFO, the shift register and its alarm back to power-on
    // a block as the reference's constructor leaves it
    uart.ctrl = CR_RXE | CR_TXE;
    uart.line_ctrl = 0;
    uart.int_divisor = uart.frac_divisor = 0;
    uart.interrupt_mask = uart.interrupt_status = 0;
    uart.rx_reset();
    (void)uart.write_atomic(ICR, 0, kAtomicNormal);  // raw_write_value back to 0
    uart.ifls = IFLS_RESET;
    uart.ilpr = uart.dmacr = uart.rsr = 0;
    env.irq_n = env.dreq_n = env.bytes_n = env.baud_calls = env.warns = 0;
}
// An enabled UART (UARTEN, TXE, RXE), as a firmware leaves it: a disabled one neither sends nor receives (RP2040 datasheet, UARTCR).
static void fresh() {
    fresh_power_on();
    uart.ctrl = CR_UARTEN | CR_RXE | CR_TXE;
}
static uint32_t rd(uint32_t offset) { return uart.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { return uart.write_atomic(offset, value, atomic); }
static bool last_irq() { return env.irq_n > 0 && env.irq_calls[env.irq_n - 1]; }

static void test_power_on_state() {
    fresh_power_on();
    CHECK(rd(CR) == (CR_RXE | CR_TXE));
    CHECK(rd(FR) == (FR_TXFE | FR_RXFE));
    CHECK(rd(LCR_H) == 0 && rd(IBRD) == 0 && rd(FBRD) == 0 && rd(IMSC) == 0 && rd(IRIS) == 0 && rd(IMIS) == 0);
    CHECK(rd(PERIPHID0) == 0x11 && rd(PERIPHID1) == 0x10 && rd(PERIPHID2) == 0x34 && rd(PERIPHID3) == 0x00);
    CHECK(rd(PCELLID0) == 0x0D && rd(PCELLID1) == 0xF0 && rd(PCELLID2) == 0x05 && rd(PCELLID3) == 0xB1);
    CHECK(rd(IFLS) == 0x12 && rd(ILPR) == 0 && rd(DMACR) == 0 && rd(RSR) == 0);
    CHECK(env.warns == 0);
}

static void test_a_default_constructed_block_is_at_power_on() {
    static UartBlock block;  // not through fresh(): the member defaults themselves
    UartHost host;
    host.on_byte = on_byte;
    host.baud_changed = on_baud;
    host.bit_ns = on_bit;
    block.init(&clk, &test_ports(), kTestBlock, kTestIrq, kTestDreqTx, host);
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
    for (uint32_t i = 0; i < 40; ++i) uart.feed_byte(i + 1);  // the 33rd and later are an overrun: dropped, OE set
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

// RP2040 datasheet 4.2.3.2.6: with UARTCR.LBE set the transmit path is fed back to the receive path.
static void test_loopback_feeds_a_written_byte_to_the_receiver() {
    fresh();
    CHECK(wr(CR, CR_UARTEN | CR_TXE | CR_RXE | CR_LBE));
    const int sent_before = env.bytes_n;
    CHECK(wr(DR, 0x5A));
    CHECK(env.bytes_n == sent_before);                    // nothing reaches the device
    CHECK((rd(FR) & FR_RXFE) == 0 && rd(DR) == 0x5A);    // the receiver got it
    CHECK(wr(CR, CR_UARTEN | CR_TXE | CR_RXE));
    CHECK(wr(DR, 0x5B) && env.bytes_n == sent_before + 1 && (rd(FR) & FR_RXFE) != 0);
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
    CHECK(env.warns >= 1 && env.warn_kind[0] == kRegWarnRead && env.warn_offset[0] == ICR);
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
    CHECK(wr(LCR_H, 0xFFFFFFFFu) && rd(LCR_H) == 0xFFu);          // 31:8 are reserved
    CHECK(env.baud_calls == 3);                                  // LCR_H announces nothing
}

static void test_imsc_is_masked_to_eleven_bits_and_drives_the_line() {
    fresh();
    uart.feed_byte(1);
    CHECK(wr(IMSC, 0xFFFFFFFFu) && rd(IMSC) == 0x7FF && last_irq());
    CHECK(wr(IMSC, 0) && !last_irq());
}

static bool dreq_pair_is(int first, bool tx, bool rx) {  // the last two calls, TX then RX
    return env.dreq_n >= first + 2 && !env.dreq_rx[first] && env.dreq_rx[first + 1] && env.dreq_calls[first] == tx && env.dreq_calls[first + 1] == rx;
}

static void test_the_dma_requests_follow_dmacr_and_the_enables() {
    fresh_power_on();
    CHECK(wr(CR, CR_UARTEN | CR_TXE | CR_RXE) && env.dreq_n == 2 && dreq_pair_is(0, false, false));  // TXDMAE and RXDMAE are 0: nothing asks
    CHECK(wr(DMACR, DMACR_TXDMAE | DMACR_RXDMAE) && dreq_pair_is(2, true, false));                    // the TX FIFO never fills; the RX FIFO is empty
    CHECK(uart.feed_byte(0x42) && dreq_pair_is(4, true, true));
    CHECK(rd(DR) == 0x42 && dreq_pair_is(6, true, false));
    CHECK(wr(CR, CR_UARTEN | CR_RXE) && dreq_pair_is(8, false, false));                              // the transmitter is off
    CHECK(wr(CR, CR_TXE | CR_RXE) && dreq_pair_is(10, false, false));                                // UARTEN is what everything hangs on
    CHECK(wr(CR, 0xFFFFFFFFu) && rd(CR) == 0xFF87u);                                                  // 6:3 and 31:16 are reserved
    CHECK(wr(CR, 0) && env.dreq_n == 16 && dreq_pair_is(14, false, false));
}

static void test_dmaonerr_holds_the_receive_request_back_while_an_error_interrupt_is_up() {
    fresh();
    wr(DMACR, DMACR_RXDMAE | DMACR_DMAONERR);
    for (uint32_t i = 0; i < 33; ++i) uart.feed_byte(i);  // the 33rd arrives with the FIFO full: an overrun
    CHECK(rd(RSR) == RSR_OE && (rd(IRIS) & INT_OE) != 0);
    CHECK(env.dreq_n > 0 && env.dreq_rx[env.dreq_n - 1] && !env.dreq_calls[env.dreq_n - 1]);  // data is waiting, but the request is held back
    CHECK(wr(ICR, INT_OE));
    CHECK(env.dreq_rx[env.dreq_n - 1] && env.dreq_calls[env.dreq_n - 1]);                      // cleared: it goes through
    CHECK(wr(RSR, 0xFF) && rd(RSR) == 0);                                                      // UARTECR clears the flags
}

static void test_a_disabled_uart_sends_and_receives_nothing() {
    fresh_power_on();  // UARTEN is 0
    CHECK(wr(DR, 0x41) && env.bytes_n == 0 && rd(IRIS) == 0);
    CHECK(uart.feed_byte(0x42) && uart.rx_empty() && env.irq_n == 0);
    wr(CR, CR_UARTEN | CR_RXE);  // the transmitter off
    CHECK(wr(DR, 0x41) && env.bytes_n == 0);
    CHECK(uart.feed_byte(0x42) && uart.rx_count() == 1);
    wr(CR, CR_UARTEN | CR_TXE);  // the receiver off
    CHECK(uart.feed_byte(0x43) && uart.rx_count() == 1);
    CHECK(wr(DR, 0x44) && env.bytes_n == 1 && env.bytes[0] == 0x44);
}

static void test_ifls_ilpr_and_dmacr_keep_only_their_bits() {
    fresh();
    CHECK(wr(IFLS, 0xFFFFFFFFu) && rd(IFLS) == 0x3F);
    CHECK(wr(ILPR, 0xFFFFFFFFu) && rd(ILPR) == 0xFF);
    CHECK(wr(DMACR, 0xFFFFFFFFu) && rd(DMACR) == 0x7);
    CHECK(env.warns == 0);
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
    CHECK(rd(0x08) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kRegWarnRead && env.warn_offset[0] == 0x08);
    env.warns = 0;
    CHECK(rd(0x1004) == 0xFFFFFFFFu && env.warns == 2);          // above 0x1000 the atomic-region warning follows
    CHECK(env.warn_kind[1] == kRegWarnReadAtomicArea);
    env.warns = 0;
    CHECK(rd(0x1001) == 0xFFFFFFFFu && env.warns == 2);          // above 0x1000, aligned or not
    env.warns = 0;
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1);          // 0x1000 itself is not above it
    env.warns = 0;
    CHECK(wr(0x08, 0x1234) && env.warns == 1 && env.warn_kind[0] == kRegWarnWrite && env.warn_offset[0] == 0x08 && env.warn_value[0] == 0x1234);
    env.warns = 0;
    CHECK(wr(FR, 7) && env.warns == 1);                          // FR is read-only: a write is unimplemented
    CHECK(wr(IRIS, 7) && env.warns == 2);
}

static void test_reset_clears_the_registers_and_the_fifo_and_drops_the_line_only() {
    fresh();
    wr(IMSC, INT_RX);
    uart.feed_byte(9);
    wr(DR, 1);                                                   // TXINTR pending too (the FIFOs are off: the single location empties)
    wr(IBRD, 67);
    wr(FBRD, 52);
    wr(LCR_H, 0x70);
    wr(CR, CR_UARTEN | CR_TXE | CR_RXE);
    wr(IMSC, INT_RX | INT_TX);                                   // the last write: raw_write_value is now nonzero
    CHECK(rd(IRIS) == (INT_RX | INT_TX));
    const int64_t raw = uart.raw_write_value();
    env.irq_n = env.dreq_n = env.baud_calls = 0;
    CHECK(uart.reset());
    CHECK(rd(CR) == (CR_RXE | CR_TXE) && rd(LCR_H) == 0 && rd(IBRD) == 0 && rd(FBRD) == 0 && rd(IMSC) == 0 && rd(IRIS) == 0);
    CHECK(uart.rx_empty() && rd(FR) == (FR_TXFE | FR_RXFE));
    CHECK(env.irq_n == 1 && !env.irq_calls[0]);                  // the line dropped, once
    CHECK(env.dreq_n == 2 && !env.dreq_calls[0] && !env.dreq_calls[1] && env.baud_calls == 0);  // both DREQs dropped; no announcement
    CHECK(rd(IFLS) == 0x12 && rd(ILPR) == 0 && rd(DMACR) == 0 && rd(RSR) == 0);
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
    CHECK(!wr(CR, CR_UARTEN) && rd(CR) == CR_UARTEN);  // the register took the value, the DREQ call raised
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


// ---- the transmitter's time: a configured baud rate gives a character its time on the wire --------------------------------------------------

static bool tick(double ns) { return clk.tick(ns); }
static bool last_tx_dreq() {
    for (int i = env.dreq_n - 1; i >= 0; --i)
        if (!env.dreq_rx[i]) return env.dreq_calls[i];
    return false;
}
constexpr uint32_t LCR_8N1_FEN = 0x70;  // WLEN 8, FEN

static void test_a_character_is_delivered_when_its_frame_has_gone_out() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    CHECK(wr(DR, 0x41));
    CHECK(env.bytes_n == 0);                                   // on the wire, not delivered yet
    CHECK((rd(FR) & FR_BUSY) != 0 && (rd(FR) & FR_TXFE) != 0); // BUSY while shifting; the FIFO itself is empty (the character is in the shift register)
    CHECK(tick(9999) && env.bytes_n == 0);                     // start + 8 data + 1 stop = 10 bits
    CHECK(tick(1) && env.bytes_n == 1 && env.bytes[0] == 0x41);
    CHECK((rd(FR) & FR_BUSY) == 0 && (rd(FR) & FR_TXFE) != 0);
    // parity and two stop bits lengthen the frame: WLEN 5 (b00), PEN, STP2 = 1 + 5 + 1 + 2 = 9 bits
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_PEN | LCR_STP2);
    wr(DR, 1);
    CHECK(tick(8999) && env.bytes_n == 0);
    CHECK(tick(1) && env.bytes_n == 1);
}

static void test_the_tx_fifo_holds_32_behind_the_shift_register_and_loses_what_does_not_fit() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    for (int i = 0; i < 40; ++i) wr(DR, i);
    CHECK(uart.tx_count() == 32 && uart.tx_full() && (rd(FR) & FR_TXFF) != 0 && (rd(FR) & FR_TXFE) == 0);  // 1 shifting + 32 queued; 7 lost
    CHECK(tick(10 * 1000.0 * 40) && env.bytes_n == 33);
    CHECK(env.bytes[0] == 0 && env.bytes[32] == 32);                                                        // in order, the first 33
    CHECK((rd(FR) & FR_BUSY) == 0 && (rd(FR) & FR_TXFF) == 0 && (rd(FR) & FR_TXFE) != 0);
    // without FEN the FIFO is one holding register: the first character goes into the shift register, the second waits, the third is lost
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, 0x60);
    wr(DR, 1);
    wr(DR, 2);
    wr(DR, 3);
    CHECK(uart.tx_capacity() == 1 && (rd(FR) & FR_TXFF) != 0);
    CHECK(tick(100000) && env.bytes_n == 2 && env.bytes[0] == 1 && env.bytes[1] == 2);
}

static void test_txintr_asserts_as_the_fifo_falls_to_the_trigger_level_and_a_write_above_it_clears() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);                                    // IFLS reset 0x12: TXIFLSEL b010 = at or below 16 entries
    for (int i = 0; i < 20; ++i) wr(DR, i);                    // 1 shifting, 19 queued
    CHECK(rd(IRIS) == 0);
    CHECK(tick(20000) && rd(IRIS) == 0);                       // 17 queued
    CHECK(tick(9999) && rd(IRIS) == 0);
    CHECK(tick(1) && uart.tx_count() == 16 && rd(IRIS) == INT_TX);
    wr(DR, 99);                                                // 17 queued: above the level again
    CHECK(rd(IRIS) == 0);
    CHECK(tick(10000) && rd(IRIS) == INT_TX);                  // falls to 16 once more
    CHECK(wr(ICR, INT_TX) && rd(IRIS) == 0);                   // cleared by ICR
    // TXIFLSEL b000: at or below 4 entries
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(IFLS, 0x10);                                            // RXIFLSEL b010, TXIFLSEL b000
    for (int i = 0; i < 8; ++i) wr(DR, i);                     // 1 shifting, 7 queued
    CHECK(tick(20000) && uart.tx_count() == 5 && rd(IRIS) == 0);       // 2 characters later: 5 queued, still above
    CHECK(tick(10000) && uart.tx_count() == 4 && rd(IRIS) == INT_TX);  // the third takes it to 4: the transition
    wr(ICR, INT_TX);
    CHECK(tick(40000) && uart.tx_count() == 0 && rd(IRIS) == 0);       // the characters that follow do not raise it again
    // FIFOs disabled: the trigger is "the single location is empty"
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, 0x60);
    wr(DR, 1);                                                 // moves into the shift register at once: the location is empty
    CHECK(rd(IRIS) == INT_TX);
}

static void test_the_tx_dma_request_follows_the_fifo_not_being_full() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DMACR, DMACR_TXDMAE);
    CHECK(last_tx_dreq());                                     // empty: a place is free
    for (int i = 0; i < 33; ++i) wr(DR, i);
    CHECK(uart.tx_full() && !last_tx_dreq());
    CHECK(tick(10000) && !uart.tx_full() && last_tx_dreq());
    // FEN toggles the depth, and so whether it is full
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, 0x60);
    wr(DMACR, DMACR_TXDMAE);
    wr(DR, 1);
    wr(DR, 2);                                                 // the holding register is full
    CHECK(!last_tx_dreq());
    wr(LCR_H, LCR_8N1_FEN);                                    // 32 deep: a place is free again
    CHECK(last_tx_dreq());
}

static void test_loopback_delivers_to_the_receiver_after_the_frame_time() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(CR, CR_UARTEN | CR_TXE | CR_RXE | CR_LBE);
    wr(DR, 0x5A);
    CHECK(tick(9999) && (rd(FR) & FR_RXFE) != 0);
    CHECK(tick(1) && (rd(FR) & FR_RXFE) == 0 && rd(DR) == 0x5A && env.bytes_n == 0);
}

static void test_disabling_mid_frame_finishes_the_character_and_enabling_resumes() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 1);
    wr(DR, 2);
    wr(CR, CR_RXE);                                            // UARTEN off while the first is on the wire
    CHECK(tick(10000) && env.bytes_n == 1 && env.bytes[0] == 1);  // it still finishes
    CHECK(tick(100000) && env.bytes_n == 1 && uart.tx_count() == 1 && (rd(FR) & FR_BUSY) != 0);  // the second waits ("BUSY ... regardless of whether the UART is enabled")
    wr(CR, CR_UARTEN | CR_TXE | CR_RXE);
    CHECK(tick(10000) && env.bytes_n == 2 && env.bytes[1] == 2);
}

static void test_a_reset_drops_the_character_on_the_wire() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 1);
    wr(DR, 2);
    CHECK(uart.reset());
    CHECK(!clk.has_alarm() && uart.tx_empty() && !uart.tx_shifting());
    CHECK(tick(100000) && env.bytes_n == 0);
}

static void test_a_host_failure_in_the_transmitter_stops_the_block() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 7);
    env.fail_byte = true;
    CHECK(!tick(10000));                                       // the delivery fails inside the alarm: the clock stops ticking
    CHECK(env.failed == 1 && !uart.tx_shifting());
    env.fail_byte = false;
    env.failed = 0;
}


static void test_the_tx_trigger_level_follows_txiflsel_and_fen() {
    fresh();
    wr(LCR_H, LCR_8N1_FEN);
    const uint32_t expect[8] = {4, 8, 16, 24, 28, 16, 16, 16};  // b101-b111 are reserved: they act as the reset value
    for (uint32_t sel = 0; sel < 8; ++sel) {
        wr(IFLS, sel | (7u << 3));                                  // the RX field (5:3) must not matter
        CHECK(uart.tx_trigger() == expect[sel]);
    }
    wr(LCR_H, 0x60);
    CHECK(uart.tx_trigger() == 0);                                  // FIFOs disabled: the single location being empty
    // the TX FIFO itself: 32 deep, a push beyond is ignored, a pull from empty reads 0
    for (uint32_t i = 0; i < 40; ++i) uart.tx_push(i);
    CHECK(uart.tx_count() == 32 && uart.tx_at(31) == 31);
    for (uint32_t i = 0; i < 32; ++i) CHECK(uart.tx_pull() == i);
    CHECK(uart.tx_count() == 0 && uart.tx_pull() == 0 && uart.tx_count() == 0);
    // without FEN the write that fills the holding register already takes TXINTR down (count 1 > trigger 0)
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, 0x60);
    wr(DR, 1);                                                      // moves into the shift register: the location is empty, TXINTR is up
    CHECK(rd(IRIS) == INT_TX);
    wr(DR, 2);                                                      // waits in the holding register
    CHECK(rd(IRIS) == 0);
    // a write that takes the FIFO to exactly the trigger level does not clear it ("greater than the trigger level")
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    for (int i = 0; i < 20; ++i) wr(DR, i);
    CHECK(tick(40000) && uart.tx_count() == 15 && rd(IRIS) == INT_TX);
    wr(DR, 1);
    CHECK(uart.tx_count() == 16 && rd(IRIS) == INT_TX);
    wr(DR, 1);
    CHECK(uart.tx_count() == 17 && rd(IRIS) == 0);
}

static void test_re_enabling_resumes_the_queue_and_updates_the_line() {
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(IFLS, 0x10);                                                 // TXIFLSEL b000: at or below 4 entries
    wr(IMSC, INT_TX);
    for (int i = 0; i < 6; ++i) wr(DR, i);                          // one on the wire, 5 queued: above the level
    CHECK(rd(IRIS) == 0 && !last_irq());
    wr(CR, CR_RXE);                                                 // UARTEN off, a frame on the wire
    CHECK(tick(10000) && env.bytes_n == 1 && !last_irq());          // finished; nothing new starts
    CHECK(wr(CR, CR_UARTEN | CR_TXE | CR_RXE));                     // the next moves in, the FIFO falls from 5 to 4: TXINTR, and the line follows at once
    CHECK(rd(IRIS) == INT_TX && last_irq());
}

static void test_a_receive_after_a_clear_raises_rxintr_again_and_reset_restores_the_fifo_level_bits() {
    fresh();
    uart.feed_byte(1);
    uart.feed_byte(2);
    wr(ICR, INT_RX);
    CHECK(rd(IRIS) == 0);
    CHECK(rd(DR) == 1 && rd(IRIS) == INT_RX);                       // one byte remains: the status is up again
    wr(IFLS, 0x3F);
    wr(ILPR, 0x55);
    wr(DMACR, 7);
    CHECK(uart.reset());
    CHECK(rd(IFLS) == 0x12 && rd(ILPR) == 0 && rd(DMACR) == 0);
}

static void test_the_rx_dma_request_needs_rxdmae_and_dmaonerr_only_holds_it_back_for_an_error() {
    fresh();
    uart.feed_byte(1);                                              // a byte is waiting
    CHECK(wr(DMACR, 0));
    CHECK(env.dreq_n > 0 && env.dreq_rx[env.dreq_n - 1] && !env.dreq_calls[env.dreq_n - 1]);   // RXDMAE clear: no request
    CHECK(wr(DMACR, DMACR_RXDMAE | DMACR_DMAONERR));
    CHECK(env.dreq_rx[env.dreq_n - 1] && env.dreq_calls[env.dreq_n - 1]);                      // DMAONERR with no error up: the request goes through
    // an error interrupt is up (the FIFO overran) but DMAONERR is clear: the request is not held back
    fresh();
    for (int i = 0; i < 33; ++i) uart.feed_byte(i);
    CHECK((rd(IRIS) & INT_OE) != 0);
    CHECK(wr(DMACR, DMACR_RXDMAE));
    CHECK(env.dreq_rx[env.dreq_n - 1] && env.dreq_calls[env.dreq_n - 1]);
}

static void test_a_failing_interrupt_line_stops_an_icr_write_and_the_next_frame() {
    fresh();
    env.fail_irq = true;
    CHECK(!wr(ICR, INT_TX));
    env.fail_irq = false;
    env.failed = 0;
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 1);
    wr(DR, 2);
    env.fail_bit = true;
    CHECK(!tick(10000));                                            // the first frame ends, the second cannot start: its bit time fails inside the alarm
    env.fail_bit = false;
    env.failed = 0;
}

static void test_the_transmitter_reports_a_failing_host_call() {
    // the bit time cannot be had
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    env.fail_bit = true;
    CHECK(!wr(DR, 1));
    env.fail_bit = false;
    env.failed = 0;
    // the interrupt line fails at the end of a DR write
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    env.fail_irq = true;
    CHECK(!wr(DR, 1));
    env.fail_irq = false;
    env.failed = 0;
    // ... and when the frame is over
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 1);
    env.fail_irq = true;
    CHECK(!tick(10000));
    env.fail_irq = false;
    env.failed = 0;
    // ... the DMA request after a frame
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 1);
    env.fail_dreq = true;
    CHECK(!tick(10000));
    env.fail_dreq = false;
    env.failed = 0;
    // re-enabling: the bit time, then the line
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 1);
    wr(DR, 2);
    wr(CR, CR_RXE);
    CHECK(tick(10000));
    env.fail_bit = true;
    CHECK(!wr(CR, CR_UARTEN | CR_TXE | CR_RXE));
    env.fail_bit = false;
    env.failed = 0;
    fresh();
    env.bit_nanos = 1000.0;
    wr(LCR_H, LCR_8N1_FEN);
    wr(DR, 1);
    wr(DR, 2);
    wr(CR, CR_RXE);
    CHECK(tick(10000));
    env.fail_irq = true;
    CHECK(!wr(CR, CR_UARTEN | CR_TXE | CR_RXE));
    env.fail_irq = false;
    env.failed = 0;
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
    test_loopback_feeds_a_written_byte_to_the_receiver();
    test_icr_clears_the_raw_value_bits_for_good();
    test_icr_uses_the_raw_value_whatever_the_alias_and_a_direct_write_the_stale_one();
    test_the_baud_divisors_are_masked_and_announced_every_time();
    test_imsc_is_masked_to_eleven_bits_and_drives_the_line();
    test_the_dma_requests_follow_dmacr_and_the_enables();
    test_dmaonerr_holds_the_receive_request_back_while_an_error_interrupt_is_up();
    test_a_disabled_uart_sends_and_receives_nothing();
    test_ifls_ilpr_and_dmacr_keep_only_their_bits();
    test_alias_writes_decode_against_a_read_with_its_side_effects();
    test_unimplemented_offsets_warn_and_read_all_ones();
    test_reset_clears_the_registers_and_the_fifo_and_drops_the_line_only();
    test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised();
    test_the_failure_flag_is_what_failed_reports();
    test_the_window_handler_is_the_bus_entry_point();
    test_a_character_is_delivered_when_its_frame_has_gone_out();
    test_the_tx_fifo_holds_32_behind_the_shift_register_and_loses_what_does_not_fit();
    test_txintr_asserts_as_the_fifo_falls_to_the_trigger_level_and_a_write_above_it_clears();
    test_the_tx_dma_request_follows_the_fifo_not_being_full();
    test_loopback_delivers_to_the_receiver_after_the_frame_time();
    test_disabling_mid_frame_finishes_the_character_and_enabling_resumes();
    test_a_reset_drops_the_character_on_the_wire();
    test_a_host_failure_in_the_transmitter_stops_the_block();
    test_the_tx_trigger_level_follows_txiflsel_and_fen();
    test_re_enabling_resumes_the_queue_and_updates_the_line();
    test_a_receive_after_a_clear_raises_rxintr_again_and_reset_restores_the_fifo_level_bits();
    test_the_transmitter_reports_a_failing_host_call();
    test_the_rx_dma_request_needs_rxdmae_and_dmaonerr_only_holds_it_back_for_an_error();
    test_a_failing_interrupt_line_stops_an_icr_write_and_the_next_frame();
    if (failures == 0) std::printf("test_uart: ok\n");
    return failures == 0 ? 0 : 1;
}
