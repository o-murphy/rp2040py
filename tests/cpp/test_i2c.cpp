// Standalone checks of src/rp2040py/native/core/i2c.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_i2c.py) that the lockstep differential (tests/test_i2c_diff.py) also pins, against a recording host whose five device
// callbacks can each be the reference's default, "auto" (complete from inside the callback), deferred (the test completes later) or silent, with failure injection per host function.
#include <cstdio>

#include "i2c.hpp"

using namespace rp2040core;
using namespace rp2040core::i2c_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

enum Mode { kDefault = 0, kAuto = 1, kDefer = 2, kSilent = 3 };
enum Call { kStart = 1, kConnect = 2, kWrite = 3, kRead = 4, kStopCall = 5 };

static I2cBlock i2c;

struct Event {
    int kind;
    uint32_t a, b;
};
struct Env {
    bool irq_calls[256];
    int irq_n = 0;
    Event events[256];
    int events_n = 0;
    uint32_t warn_kind[32], warn_offset[32];
    int64_t warn_value[32];
    int warns = 0;
    int mode[6] = {0, 0, 0, 0, 0, 0};
    uint32_t counter = 0;
    bool nack_connect = false, nack_write = false;  // what an "auto" device answers
    uint32_t read_value = 0x40;
    int failed = 0;
    int fail_call = 0;  // a Call number whose host function fails, or 0
    bool fail_irq = false;
};
static Env env;

static void note(int kind, uint32_t a = 0, uint32_t b = 0) {
    if (env.events_n < 256) env.events[env.events_n] = Event{kind, a, b};
    ++env.events_n;
}
static bool fails(int call) {
    if (env.fail_call == call) { env.failed = 1; return true; }
    return false;
}
static bool on_irq(void*, bool level) {
    if (env.irq_n < 256) env.irq_calls[env.irq_n] = level;
    ++env.irq_n;
    if (env.fail_irq) { env.failed = 1; return false; }
    return true;
}
static bool on_start(void*, bool repeated) {
    note(kStart, repeated);
    if (fails(kStart)) return false;
    if (env.mode[kStart] == kDefault || env.mode[kStart] == kAuto) return i2c.complete_start();
    return true;
}
static bool on_connect(void*, uint32_t address, uint32_t mode) {
    note(kConnect, address, mode);
    if (fails(kConnect)) return false;
    if (env.mode[kConnect] == kDefault) return i2c.complete_connect(false);
    if (env.mode[kConnect] == kAuto) return i2c.complete_connect(!env.nack_connect, 0);
    return true;
}
static bool on_write(void*, uint32_t value) {
    note(kWrite, value);
    if (fails(kWrite)) return false;
    if (env.mode[kWrite] == kDefault) return i2c.complete_write(false);
    if (env.mode[kWrite] == kAuto) return i2c.complete_write(!env.nack_write);
    return true;
}
static bool on_read(void*, bool ack) {
    note(kRead, ack);
    if (fails(kRead)) return false;
    if (env.mode[kRead] == kDefault) return i2c.complete_read(0xFF);
    if (env.mode[kRead] == kAuto) return i2c.complete_read(env.read_value++);
    return true;
}
static bool on_stop(void*) {
    note(kStopCall);
    if (fails(kStopCall)) return false;
    if (env.mode[kStopCall] == kDefault || env.mode[kStopCall] == kAuto) return i2c.complete_stop();
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

static void fresh(int all_modes = kDefault) {
    env = Env();
    for (int i = 1; i <= 5; ++i) env.mode[i] = all_modes;
    I2cHost host;
    host.irq = on_irq;
    host.start = on_start;
    host.connect = on_connect;
    host.write_byte = on_write;
    host.read_byte = on_read;
    host.stop = on_stop;
    host.warn = on_warn;
    host.failed = &env.failed;
    i2c.init(host);
    (void)i2c.reset();
    (void)i2c.write_atomic(CON, i2c.control, kAtomicNormal);  // raw_write_value: whatever, then back to 0 below
    (void)i2c.write_atomic(SS_SCL_HCNT, 0x28, kAtomicNormal);
    (void)i2c.write_atomic(CON, 0x65, kAtomicNormal);
    env.irq_n = env.events_n = env.warns = 0;
}
static uint32_t rd(uint32_t offset) { return i2c.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { return i2c.write_atomic(offset, value, atomic); }
static bool last_irq() { return env.irq_n > 0 && env.irq_calls[env.irq_n - 1]; }
static bool is(int index, int kind, uint32_t a = 0, uint32_t b = 0) {
    return index < env.events_n && env.events[index].kind == kind && env.events[index].a == a && env.events[index].b == b;
}

static void test_a_default_constructed_block_is_at_power_on() {
    static I2cBlock block;
    CHECK(block.control == 0x65 && block.target_address == 0x55 && block.slave_address == 0x55 && block.state == STATE_IDLE);
    CHECK(block.ss_clock_high == 0x28 && block.ss_clock_low == 0x2F && block.fs_clock_high == 0x06 && block.fs_clock_low == 0x0D && block.spikelen == 0x07);
    CHECK(block.enable == 0 && block.rx_threshold == 0 && block.tx_threshold == 0 && block.abort_source == 0 && block.int_raw == 0 && block.int_enable == 0x8FF);
    CHECK(!block.busy && !block.stop && !block.pending_restart && !block.first_byte && block.rx.empty() && block.tx.empty() && block.raw_write_value() == 0);
    CHECK(block.master_bits() == 7);
}

static void test_register_reads_at_power_on() {
    fresh();
    CHECK(rd(CON) == 0x65 && rd(TAR) == 0x55 && rd(SAR) == 0x55 && rd(SS_SCL_HCNT) == 0x28 && rd(SS_SCL_LCNT) == 0x2F && rd(FS_SCL_HCNT) == 0x06 && rd(FS_SCL_LCNT) == 0x0D);
    CHECK(rd(INTR_STAT) == 0 && rd(INTR_MASK) == 0x8FF && rd(RAW_INTR_STAT) == 0 && rd(RX_TL) == 0 && rd(TX_TL) == 0 && rd(ENABLE) == 0);
    CHECK(rd(STATUS) == (ST_TFE | ST_TFNF) && rd(TXFLR) == 0 && rd(RXFLR) == 0 && rd(SDA_HOLD) == 1 && rd(ENABLE_STATUS) == 0 && rd(FS_SPKLEN) == 7);
    CHECK(rd(COMP_PARAM_1) == 0 && rd(COMP_VERSION) == 0x3230312Au && rd(COMP_TYPE) == 0x44570140u && rd(TX_ABRT_SOURCE) == 0);
    CHECK(env.warns == 0);
}

static void test_masks_and_the_invalid_speed_field() {
    fresh();
    wr(CON, 0x61);                                               // speed field 0: rewritten to high speed (3)
    CHECK(rd(CON) == (0x61 | (3 << 1)));
    wr(CON, 0xFFFFFFFFu);
    CHECK(rd(CON) == 0x3FF);                                     // IC_CON 9:0 (bit 10 is read only, the rest reserved)
    wr(TAR, 0xFFFFFFFFu);
    wr(SAR, 0xFFFFFFFFu);
    CHECK(rd(TAR) == 0xFFF && rd(SAR) == 0x3FF);                 // IC_TAR 11:0, IC_SAR 9:0
    wr(SS_SCL_HCNT, 0x12345);
    wr(SS_SCL_LCNT, 0x23456);
    wr(FS_SCL_HCNT, 0x34567);
    wr(FS_SCL_LCNT, 0x45678);
    CHECK(rd(SS_SCL_HCNT) == 0x2345 && rd(SS_SCL_LCNT) == 0x3456 && rd(FS_SCL_HCNT) == 0x4567 && rd(FS_SCL_LCNT) == 0x5678);
    wr(RX_TL, 0x1FF);
    wr(TX_TL, 0x105);
    CHECK(rd(RX_TL) == 16 && rd(TX_TL) == 5);                    // 8 bits, then clamped to the FIFO size
    wr(SS_SCL_HCNT, 5);
    wr(SS_SCL_LCNT, 7);
    wr(FS_SCL_HCNT, 0);
    wr(FS_SCL_LCNT, 1);
    CHECK(rd(SS_SCL_HCNT) == 6 && rd(SS_SCL_LCNT) == 8 && rd(FS_SCL_HCNT) == 6 && rd(FS_SCL_LCNT) == 8);   // the minimum valid values: 6, 8, 6, 8
    wr(ENABLE, 1);
    wr(CON, 0x61);
    wr(TAR, 0x7);
    wr(SAR, 0x7);
    wr(SS_SCL_HCNT, 0x77);
    CHECK(rd(CON) == 0x3FF && rd(TAR) == 0xFFF && rd(SAR) == 0x3FF && rd(SS_SCL_HCNT) == 6);   // enabled: none of them is writable
    wr(ENABLE, 0);
    wr(RX_TL, 0xFF);
    CHECK(rd(RX_TL) == 16);
    wr(RX_TL, 0x105);
    CHECK(rd(RX_TL) == 5);
    wr(TX_TL, 0xFF);
    CHECK(rd(TX_TL) == 16);
    wr(TAR, 0x123);
    wr(SAR, 0x234);
    CHECK(rd(TAR) == 0x123 && rd(SAR) == 0x234);
    wr(RX_TL, 3);
    CHECK(rd(RX_TL) == 3);
    CHECK(env.warns == 0);
}

static void test_the_default_device_nacks_every_address_and_a_write_aborts() {
    fresh(kDefault);
    wr(ENABLE, 1);
    wr(DATA_CMD, 0x55);
    wr(DATA_CMD, 0x66);                                          // the first command already ran the whole failed attempt: this one is a new transfer
    CHECK(is(0, kStart, 0) && is(1, kConnect, 0x55, MODE_WRITE) && is(2, kStopCall));
    CHECK(i2c.state == STATE_IDLE && !i2c.busy);
    CHECK((rd(RAW_INTR_STAT) & (R_START_DET | R_STOP_DET | R_TX_ABRT)) == (R_START_DET | R_STOP_DET | R_TX_ABRT));
    CHECK((i2c.abort_source & ABRT_7B_ADDR_NOACK) != 0);
    CHECK(rd(TXFLR) == 0);                                       // the abort emptied the TX FIFO
}

static void test_a_whole_write_transaction_with_an_acking_device() {
    fresh(kAuto);
    wr(TAR, 0x3C);
    wr(ENABLE, 1);
    wr(DATA_CMD, 0x11);
    wr(DATA_CMD, 0x22);
    wr(DATA_CMD, 0x33 | STOP);
    CHECK(env.events_n == 6);
    CHECK(is(0, kStart, 0) && is(1, kConnect, 0x3C, MODE_WRITE) && is(2, kWrite, 0x11));
    CHECK(is(3, kWrite, 0x22) && is(4, kWrite, 0x33) && is(5, kStopCall));
    CHECK(i2c.state == STATE_IDLE && !i2c.busy && i2c.tx.empty() && i2c.abort_source == 0);
    CHECK((rd(RAW_INTR_STAT) & (R_START_DET | R_STOP_DET)) == (R_START_DET | R_STOP_DET));
    CHECK(rd(STATUS) == (ST_TFE | ST_TFNF));
}

static void test_a_queue_of_commands_runs_in_one_call_stack_through_re_entrant_completions() {
    fresh(kAuto);
    wr(ENABLE, 3 & ~EN_ABORT);                                   // enabled, but BLOCKED below to queue
    wr(ENABLE, EN_ENABLE | EN_TX_CMD_BLOCK);
    for (uint32_t i = 0; i < 5; ++i) wr(DATA_CMD, 0xA0 + i);
    CHECK(env.events_n == 0 && rd(TXFLR) == 5);                  // blocked: nothing left the FIFO
    wr(ENABLE, EN_ENABLE);                                       // unblocking: the whole queue goes in this one call
    CHECK(rd(TXFLR) == 0 && env.events_n == 7);                  // start, connect, 5 writes (no STOP: the bus stays open)
    CHECK(i2c.state == STATE_CONNECTED && !i2c.busy);
}

static void test_a_read_transaction_flags_the_first_byte_and_acks_all_but_the_last() {
    fresh(kAuto);
    wr(ENABLE, 1);
    wr(DATA_CMD, CMD);
    wr(DATA_CMD, CMD);
    wr(DATA_CMD, CMD | STOP);
    CHECK(is(0, kStart, 0) && is(1, kConnect, 0x55, MODE_READ) && is(2, kRead, 1) && is(3, kRead, 1) && is(4, kRead, 0) && is(5, kStopCall));
    CHECK(rd(RXFLR) == 3);
    CHECK(rd(DATA_CMD) == (0x40 | FIRST_DATA_BYTE));             // the first byte carries the flag
    CHECK(rd(DATA_CMD) == 0x41 && rd(DATA_CMD) == 0x42);
    CHECK(rd(DATA_CMD) == 0 && (rd(RAW_INTR_STAT) & R_RX_UNDER) != 0);  // empty: 0 and RX_UNDER
}

static void test_a_restart_asks_the_device_for_a_repeated_start() {
    fresh(kAuto);
    wr(ENABLE, 1);
    wr(DATA_CMD, 0x10);
    wr(DATA_CMD, 0x20 | RESTART | CMD);                          // a restart, now as a read
    CHECK(is(0, kStart, 0) && is(1, kConnect, 0x55, MODE_WRITE) && is(2, kWrite, 0x10));
    CHECK(is(3, kStart, 1) && is(4, kConnect, 0x55, MODE_READ) && is(5, kRead, 1));
}

static void test_addresses_in_7_and_10_bit_mode_and_the_reasons_for_a_nack() {
    fresh(kAuto);
    env.nack_connect = true;
    wr(TAR, 0x2AA);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(is(1, kConnect, 0xAA, MODE_WRITE));                    // 7-bit mode masks the address to 8 bits (the reference's own mask)
    CHECK((rd(TX_ABRT_SOURCE) & 0x1FF) == ABRT_7B_ADDR_NOACK);
    fresh(kAuto);
    env.nack_connect = true;
    wr(CON, 0x65 | CON_10BIT_MASTER);
    wr(TAR, 0x2AA);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(is(1, kConnect, 0x2AA, MODE_WRITE) && i2c.master_bits() == 10);
    CHECK((i2c.abort_source & 0x1FF) == ABRT_10ADDR1_NOACK);     // the nack_byte of an "auto" connect is 0
    CHECK(i2c.state == STATE_IDLE);
    wr(DATA_CMD, 2);                                             // the device is asked again; answer by hand with nack_byte 1
    fresh(kDefer);
    wr(CON, 0x65 | CON_10BIT_MASTER);
    wr(TAR, 0x155);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.complete_start());
    CHECK(i2c.complete_connect(false, 1));
    CHECK((i2c.abort_source & 0x1FF) == ABRT_10ADDR2_NOACK);
    fresh(kDefer);
    wr(TAR, 0);                                                  // a general call
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.complete_start());
    CHECK(i2c.complete_connect(false));
    CHECK((i2c.abort_source & 0x1FF) == ABRT_GCALL_NOACK);
}

static void test_a_nacked_data_byte_aborts_and_stops() {
    fresh(kAuto);
    env.nack_write = true;
    wr(ENABLE, 1);
    wr(DATA_CMD, 0x01);
    wr(DATA_CMD, 0x02);
    CHECK(is(2, kWrite, 1) && is(3, kStopCall));
    CHECK((i2c.abort_source & ABRT_TXDATA_NOACK) != 0 && (rd(RAW_INTR_STAT) & R_TX_ABRT) != 0);
    CHECK(i2c.state == STATE_IDLE);
}

static void test_abort_accounting_keeps_the_flush_count_in_the_high_bits() {
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);                                             // start asked, still pending
    wr(DATA_CMD, 2);
    wr(DATA_CMD, 3);
    CHECK(rd(TXFLR) == 3);
    CHECK(i2c.complete_start());
    CHECK(i2c.complete_connect(false));                          // abort with 3 commands queued
    CHECK((i2c.abort_source >> TX_FLUSH_CNT_SHIFT) == 3 && (i2c.abort_source & 0x1FF) == ABRT_7B_ADDR_NOACK);
    CHECK(rd(TXFLR) == 0);
    const uint32_t first = rd(TX_ABRT_SOURCE);                   // reading clears it
    CHECK(first == ((3u << TX_FLUSH_CNT_SHIFT) | ABRT_7B_ADDR_NOACK) && rd(TX_ABRT_SOURCE) == 0);
    // the reasons accumulate until read; the flush count is replaced by the new abort's
    i2c.abort_source = ABRT_TXDATA_NOACK | (2u << TX_FLUSH_CNT_SHIFT);
    i2c.tx.push(1);
    CHECK(i2c.arbitration_lost());
    CHECK((i2c.abort_source & 0x1FF) == ABRT_TXDATA_NOACK && (i2c.abort_source & ARB_LOST) != 0);
    CHECK((i2c.abort_source >> TX_FLUSH_CNT_SHIFT) == 1);
}

static void test_the_enable_register() {
    fresh(kDefer);
    wr(ENABLE, 1);
    CHECK(rd(ENABLE) == 1 && rd(ENABLE_STATUS) == 1);
    wr(DATA_CMD, 1);
    wr(DATA_CMD, 2);
    CHECK(i2c.state == STATE_START && i2c.busy);
    wr(ENABLE, 1 | EN_ABORT);                                    // aborting an active bus: reason USER_ABRT, FIFO flushed, stop forced
    CHECK((i2c.abort_source & ABRT_USER_ABRT) != 0 && i2c.stop && rd(TXFLR) == 0);
    CHECK((rd(ENABLE) & EN_ABORT) != 0);
    wr(ENABLE, 1);                                               // software cannot clear ABORT
    CHECK((rd(ENABLE) & EN_ABORT) != 0);
    CHECK(i2c.complete_stop());                                  // the stop completes the abort and clears the bit
    CHECK((rd(ENABLE) & EN_ABORT) == 0 && i2c.state == STATE_IDLE);
    wr(ENABLE, 1 | EN_ABORT);                                    // idle: the bit is dropped
    CHECK((rd(ENABLE) & EN_ABORT) == 0);
    wr(DATA_CMD, 7);
    wr(ENABLE, 0);                                               // disabling flushes both FIFOs
    CHECK(rd(TXFLR) == 0 && rd(RXFLR) == 0);
}

static void test_tx_overflow_rx_overflow_and_the_thresholds() {
    fresh(kSilent);
    wr(ENABLE, 1);
    for (uint32_t i = 0; i < 17; ++i) wr(DATA_CMD, i);           // the first command starts the bus and stays in the FIFO (silent device)
    CHECK(rd(TXFLR) == 16 && (rd(RAW_INTR_STAT) & R_TX_OVER) != 0);
    CHECK(rd(STATUS) == ST_MST_ACTIVITY + ST_ACTIVITY);          // active, TX full (no TFNF), not empty (no TFE)
    wr(RX_TL, 2);
    fresh(kSilent);
    wr(RX_TL, 2);
    i2c.first_byte = false;
    for (uint32_t i = 0; i < 3; ++i) (void)i2c.complete_read(i);
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) != 0);                 // 3 > 2
    fresh(kSilent);
    wr(RX_TL, 2);
    (void)i2c.complete_read(1);
    (void)i2c.complete_read(2);
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) == 0);                 // 2 is not above 2
    for (uint32_t i = 0; i < 14; ++i) (void)i2c.complete_read(i);
    CHECK(rd(RXFLR) == 16 && (rd(STATUS) & ST_RFF) != 0);
    (void)i2c.complete_read(99);
    CHECK((rd(RAW_INTR_STAT) & R_RX_OVER) != 0 && rd(RXFLR) == 16);
    CHECK(rd(DATA_CMD) != 0);                                    // 15 entries left, above the threshold of 2: RX_FULL is a level and stays
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) != 0);
    for (int i = 0; i < 12; ++i) (void)rd(DATA_CMD);             // 3 left
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) != 0);
    (void)rd(DATA_CMD);                                          // 2 left: not above the threshold any more
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) == 0);
    wr(RX_TL, 0);                                                // a change of the threshold re-evaluates the level
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) != 0);
    wr(RX_TL, 5);
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) == 0);
}

static void test_tx_empty_is_raised_at_or_below_the_threshold_and_cleared_by_a_write() {
    fresh(kAuto);
    wr(ENABLE, 1);
    wr(TX_TL, 2);
    wr(DATA_CMD, 1);                                             // goes straight through: the FIFO ends at 0 <= 2
    CHECK((rd(RAW_INTR_STAT) & R_TX_EMPTY) != 0);
    fresh(kSilent);
    wr(ENABLE, 1);
    wr(TX_TL, 1);
    wr(DATA_CMD, 1);                                             // start asked and nothing pulled yet
    CHECK((rd(RAW_INTR_STAT) & R_TX_EMPTY) == 0);
}

static void test_the_clear_registers_return_whether_they_cleared_and_clear_the_abort_source() {
    fresh(kSilent);
    i2c.int_raw = R_RX_UNDER | R_RX_OVER | R_TX_OVER | R_TX_ABRT | R_STOP_DET | R_START_DET | R_RX_FULL | R_TX_EMPTY;
    i2c.abort_source = ABRT_7B_ADDR_NOACK;
    CHECK(rd(CLR_RX_UNDER) == 1 && rd(CLR_RX_UNDER) == 0);
    CHECK(rd(CLR_RX_OVER) == 1 && rd(CLR_TX_OVER) == 1 && rd(CLR_STOP_DET) == 1 && rd(CLR_START_DET) == 1);
    CHECK(rd(CLR_RD_REQ) == 0 && rd(CLR_RX_DONE) == 0 && rd(CLR_ACTIVITY) == 0 && rd(CLR_GEN_CALL) == 0);
    CHECK(rd(CLR_TX_ABRT) == 1 && i2c.abort_source == 0);        // clears the abort source as well
    i2c.int_raw = R_RX_UNDER | R_TX_ABRT | R_RX_FULL | R_TX_EMPTY;
    i2c.abort_source = ABRT_TXDATA_NOACK;
    CHECK(rd(CLR_INTR) == 1 && i2c.abort_source == 0);
    CHECK(i2c.int_raw == (R_RX_FULL | R_TX_EMPTY));              // CLR_INTR leaves the level-type ones
    CHECK(rd(CLR_INTR) == 0);
}

static void test_the_interrupt_mask_register_gates_the_line() {
    fresh(kSilent);
    CHECK(rd(INTR_MASK) == 0x8FF);                               // the datasheet's reset value
    wr(INTR_MASK, R_RX_UNDER);
    CHECK(rd(INTR_MASK) == R_RX_UNDER && env.warns == 0);
    CHECK(rd(DATA_CMD) == 0 && last_irq() && rd(INTR_STAT) == R_RX_UNDER);
    CHECK(rd(CLR_RX_UNDER) == 1 && !last_irq());
    i2c.int_raw = R_RX_FULL | R_RX_UNDER;
    env.irq_n = 0;
    wr(INTR_MASK, R_RX_FULL);                                    // a write re-evaluates the line at once
    CHECK(env.irq_n == 1 && last_irq() && rd(INTR_STAT) == R_RX_FULL);
    wr(INTR_MASK, 0);
    CHECK(!last_irq() && rd(INTR_STAT) == 0);
    wr(INTR_MASK, 0xFFFFFFFFu);                                  // 13 bits are held
    CHECK(rd(INTR_MASK) == 0x1FFF);
    wr(INTR_MASK, 0x0F, kAtomicSet);
    CHECK(rd(INTR_MASK) == 0x1FFF);
    wr(INTR_MASK, 0xFF, kAtomicClear);
    CHECK(rd(INTR_MASK) == 0x1F00);
    CHECK(i2c.reset() && rd(INTR_MASK) == 0x8FF);
    i2c.int_enable = R_RX_UNDER;                                 // the embedder may still set it directly
    CHECK(i2c.check_interrupts());
}

static void test_status_composition() {
    fresh(kSilent);
    CHECK(rd(STATUS) == (ST_TFE | ST_TFNF));
    i2c.state = STATE_CONNECTED;
    CHECK(rd(STATUS) == (ST_MST_ACTIVITY | ST_ACTIVITY | ST_TFE | ST_TFNF));
    i2c.rx.push(1);
    CHECK((rd(STATUS) & (ST_RFNE | ST_RFF)) == ST_RFNE);
    for (int i = 0; i < 15; ++i) i2c.rx.push(i);
    CHECK((rd(STATUS) & (ST_RFNE | ST_RFF)) == (ST_RFNE | ST_RFF));
    i2c.tx.push(1);
    CHECK((rd(STATUS) & (ST_TFE | ST_TFNF)) == ST_TFNF);
    for (int i = 0; i < 15; ++i) i2c.tx.push(i);
    CHECK((rd(STATUS) & (ST_TFE | ST_TFNF)) == 0);
    CHECK(rd(TXFLR) == 16 && rd(RXFLR) == 16);
}

static void test_sda_hold_and_spike_length_are_written_only_while_disabled_with_their_minimum() {
    fresh();
    CHECK(rd(SDA_HOLD) == 1);                                    // reset: IC_SDA_TX_HOLD = 1
    wr(SDA_HOLD, 0xFFFFFFFFu);
    CHECK(rd(SDA_HOLD) == 0xFFFFFF && env.warns == 0);           // 24 bits, stored
    wr(SDA_HOLD, 0x2);
    CHECK(rd(SDA_HOLD) == 2);
    wr(FS_SPKLEN, 4);
    CHECK(rd(FS_SPKLEN) == 4);
    wr(FS_SPKLEN, 5);                                            // bit 0 of the VALUE does not matter: only the I2C being disabled does
    CHECK(rd(FS_SPKLEN) == 5);
    wr(FS_SPKLEN, 0);                                            // the field's minimum is 1: "if attempted results in 1 being set"
    CHECK(rd(FS_SPKLEN) == 1 && i2c.spikelen == 1);
    wr(FS_SPKLEN, 0x100);                                        // the 8-bit field is 0 here: 1 as well
    CHECK(rd(FS_SPKLEN) == 1);
    wr(FS_SPKLEN, 0x1FE);
    CHECK(rd(FS_SPKLEN) == 0xFE && i2c.spikelen == 0xFE);        // 8 bits held
    wr(ENABLE, 1);
    wr(FS_SPKLEN, 3);                                            // enabled: not writable
    wr(SDA_HOLD, 7);
    CHECK(rd(FS_SPKLEN) == 0xFE && rd(SDA_HOLD) == 2);
    wr(ENABLE, 0);
    wr(FS_SPKLEN, 3);
    CHECK(rd(FS_SPKLEN) == 3);
}

static void test_datasheet_audit_flags_flush_activity_and_the_stored_registers() {
    // FIRST_DATA_BYTE is bit 11 of IC_DATA_CMD (datasheet Table 456), not bit 10 (that is RESTART, of a write)
    CHECK(FIRST_DATA_BYTE == 0x800 && RESTART == 0x400);
    // ACTIVITY: set when the master starts, stays set until cleared by IC_CLR_ACTIVITY / IC_CLR_INTR - and only once the I2C is idle
    fresh(kSilent);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK((rd(RAW_INTR_STAT) & R_ACTIVITY) != 0 && i2c.state != STATE_IDLE);
    CHECK(rd(CLR_ACTIVITY) == 0 && (rd(RAW_INTR_STAT) & R_ACTIVITY) != 0);   // still active: stays set
    (void)rd(CLR_INTR);
    CHECK((rd(RAW_INTR_STAT) & R_ACTIVITY) != 0);
    i2c.state = STATE_IDLE;
    CHECK(rd(CLR_ACTIVITY) == 1 && (rd(RAW_INTR_STAT) & R_ACTIVITY) == 0);
    i2c.int_raw |= R_ACTIVITY;
    wr(ENABLE, 0);                                               // "Disabling the DW_apb_i2c" clears it as well
    CHECK((rd(RAW_INTR_STAT) & R_ACTIVITY) == 0);
    // a transmit abort flushes the RX FIFO too, and RX_FULL (a level) goes with it
    fresh(kSilent);
    wr(RX_TL, 0);
    i2c.first_byte = false;
    (void)i2c.complete_read(1);
    (void)i2c.complete_read(2);
    CHECK(rd(RXFLR) == 2 && (rd(RAW_INTR_STAT) & R_RX_FULL) != 0);
    CHECK(i2c.arbitration_lost());
    CHECK(rd(RXFLR) == 0 && (rd(RAW_INTR_STAT) & R_RX_FULL) == 0 && (rd(RAW_INTR_STAT) & R_TX_ABRT) != 0);
    // disabling flushes the RX FIFO and so clears RX_FULL
    fresh(kSilent);
    wr(RX_TL, 0);
    (void)i2c.complete_read(1);
    CHECK((rd(RAW_INTR_STAT) & R_RX_FULL) != 0);
    wr(ENABLE, 0);
    CHECK(rd(RXFLR) == 0 && (rd(RAW_INTR_STAT) & R_RX_FULL) == 0);
    // IC_ENABLE keeps bits 2:0 (ABORT is dropped at once when the bus is idle)
    wr(ENABLE, 0xFFFFFFFFu);
    CHECK(rd(ENABLE) == 0x5);
    wr(ENABLE, 0);
    // TX_EMPTY is cleared by a write only if the level goes above the threshold
    fresh(kSilent);
    wr(ENABLE, 1);
    wr(TX_TL, 4);
    i2c.int_raw |= R_TX_EMPTY;
    wr(DATA_CMD, 1);                                             // starts the bus; the command stays queued: level 1 <= 4
    CHECK((rd(RAW_INTR_STAT) & R_TX_EMPTY) != 0);
    for (int i = 0; i < 4; ++i) wr(DATA_CMD, 2);                 // level 5 > 4
    CHECK((rd(RAW_INTR_STAT) & R_TX_EMPTY) == 0);
    // IC_DATA_CMD keeps 11 bits of a write
    fresh(kSilent);
    wr(ENABLE, 1);
    wr(DATA_CMD, 0xFFFFFFFFu);
    CHECK(i2c.tx.at(0) == 0x7FF);
    // the registers that are stored and not acted on, with their reset values and masks
    fresh();
    CHECK(rd(SLV_DATA_NACK_ONLY) == 0 && rd(DMA_CR) == 0 && rd(DMA_TDLR) == 0 && rd(DMA_RDLR) == 0 && rd(SDA_SETUP) == 0x64 && rd(ACK_GENERAL_CALL) == 1 && rd(SDA_HOLD) == 1);
    wr(SLV_DATA_NACK_ONLY, 0xFFFFFFFFu);
    wr(DMA_CR, 0xFFFFFFFFu);
    wr(DMA_TDLR, 0xFFFFFFFFu);
    wr(DMA_RDLR, 0xFFFFFFFFu);
    wr(SDA_SETUP, 0xFFFFFFFFu);
    wr(ACK_GENERAL_CALL, 0xFFFFFFFFu);
    CHECK(rd(SLV_DATA_NACK_ONLY) == 1 && rd(DMA_CR) == 3 && rd(DMA_TDLR) == 0xF && rd(DMA_RDLR) == 0xF && rd(SDA_SETUP) == 0xFF && rd(ACK_GENERAL_CALL) == 1);
    wr(ENABLE, 1);                                               // IC_SDA_SETUP and IC_SLV_DATA_NACK_ONLY need the block disabled; IC_DMA_*, IC_ACK_GENERAL_CALL do not
    wr(SDA_SETUP, 5);
    wr(SLV_DATA_NACK_ONLY, 0);
    wr(DMA_CR, 1);
    wr(ACK_GENERAL_CALL, 0);
    CHECK(rd(SDA_SETUP) == 0xFF && rd(SLV_DATA_NACK_ONLY) == 1 && rd(DMA_CR) == 1 && rd(ACK_GENERAL_CALL) == 0);
    wr(ENABLE, 0);
    // IC_CLR_RESTART_DET clears RESTART_DET (nothing in this master raises it)
    i2c.int_raw |= R_RESTART_DET;
    CHECK(rd(CLR_RESTART_DET) == 1 && (rd(RAW_INTR_STAT) & R_RESTART_DET) == 0 && rd(CLR_RESTART_DET) == 0);
    CHECK(env.warns == 0);
    // the reset restores them
    CHECK(i2c.reset());
    CHECK(rd(SDA_SETUP) == 0x64 && rd(ACK_GENERAL_CALL) == 1 && rd(DMA_CR) == 0 && rd(SLV_DATA_NACK_ONLY) == 0);
}

static void test_unimplemented_offsets_warn_and_read_all_ones() {
    fresh();
    CHECK(rd(0x0C) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kI2cWarnRead && env.warn_offset[0] == 0x0C);
    env.warns = 0;
    CHECK(rd(0x1004) == 0xFFFFFFFFu && env.warns == 2 && env.warn_kind[1] == kI2cWarnReadAtomicArea);
    env.warns = 0;
    CHECK(rd(0x1001) == 0xFFFFFFFFu && env.warns == 2);
    env.warns = 0;
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1);
    env.warns = 0;
    CHECK(rd(0xAC) == 0xFFFFFFFFu && env.warns == 1);            // not a register
    env.warns = 0;
    CHECK(wr(0x28, 0x77) && env.warns == 1 && env.warn_kind[0] == kI2cWarnWrite && env.warn_offset[0] == 0x28 && env.warn_value[0] == 0x77);
    env.warns = 0;
    CHECK(wr(STATUS, 1) && wr(TXFLR, 1) && wr(COMP_TYPE, 1) && env.warns == 3);   // read-only registers
}

static void test_alias_writes_decode_against_a_read_with_its_side_effects() {
    fresh(kSilent);
    i2c.rx.push(0xAB);
    i2c.rx.push(0xCD);
    CHECK(wr(TAR, 0x0F, kAtomicSet) && rd(TAR) == (0x55 | 0x0F));
    CHECK(wr(TAR, 0x05, kAtomicXor) && rd(TAR) == ((0x5F ^ 0x05) & 0x3FF));
    CHECK(wr(TAR, 0x0F, kAtomicClear) && rd(TAR) == (0x5A & ~0x0F));
    wr(ENABLE, 1);
    CHECK(wr(DATA_CMD, 0x01, kAtomicSet));                       // the decode READS IC_DATA_CMD: 0xAB leaves the RX FIFO ...
    CHECK(i2c.rx.count() == 1 && i2c.rx.at(0) == 0xCD);
    CHECK(i2c.tx.count() == 1 && i2c.tx.at(0) == (0xABu | 0x01));  // ... and what is queued is read | raw
}

static void test_arbitration_loss_aborts_and_goes_idle() {
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.state == STATE_START && i2c.busy);
    CHECK(i2c.arbitration_lost());
    CHECK(i2c.state == STATE_IDLE && !i2c.busy && (i2c.abort_source & ARB_LOST) != 0 && (rd(RAW_INTR_STAT) & R_TX_ABRT) != 0 && rd(TXFLR) == 0);
}

static void test_a_completion_in_the_wrong_state_is_handled_as_the_reference_does() {
    fresh(kSilent);
    CHECK(i2c.complete_start() && is(0, kStopCall));             // nothing queued: the device is told to stop
    CHECK(i2c.complete_stop() && i2c.state == STATE_IDLE && (rd(RAW_INTR_STAT) & R_STOP_DET) != 0);
    CHECK(i2c.complete_write(true) && !i2c.busy);
    CHECK(i2c.complete_read(7) && i2c.rx.count() == 1 && i2c.rx.at(0) == 7);   // accepted with nothing asked for
}

static void test_reset_clears_the_state_and_keeps_the_host_and_raw_write_value() {
    fresh(kAuto);
    wr(CON, 0x7F);
    wr(TAR, 0x10);
    wr(SAR, 0x11);
    wr(SS_SCL_HCNT, 1);
    wr(SS_SCL_LCNT, 2);
    wr(FS_SCL_HCNT, 3);
    wr(FS_SCL_LCNT, 4);
    wr(RX_TL, 4);
    wr(TX_TL, 5);
    i2c.spikelen = 9;
    wr(ENABLE, EN_ENABLE | EN_TX_CMD_BLOCK);
    wr(DATA_CMD, 1);
    i2c.rx.push(5);
    i2c.int_raw = 0x7FF;
    i2c.int_enable = 0xFF;
    i2c.abort_source = 5;
    i2c.state = STATE_CONNECTED;
    i2c.busy = i2c.stop = i2c.pending_restart = i2c.first_byte = true;
    const int64_t raw = i2c.raw_write_value();
    env.irq_n = 0;
    CHECK(i2c.reset());
    CHECK(rd(CON) == 0x65 && rd(TAR) == 0x55 && rd(SAR) == 0x55 && rd(SS_SCL_HCNT) == 0x28 && rd(RX_TL) == 0 && rd(ENABLE) == 0 && rd(RAW_INTR_STAT) == 0 && rd(INTR_MASK) == 0x8FF);
    CHECK(rd(TXFLR) == 0 && rd(RXFLR) == 0 && i2c.abort_source == 0 && i2c.state == STATE_IDLE);
    CHECK(rd(SS_SCL_LCNT) == 0x2F && rd(FS_SCL_HCNT) == 6 && rd(FS_SCL_LCNT) == 0x0D && rd(TX_TL) == 0 && i2c.spikelen == 7);
    CHECK(!i2c.busy && !i2c.stop && !i2c.pending_restart && !i2c.first_byte);
    CHECK(env.irq_n == 1 && !env.irq_calls[0] && i2c.raw_write_value() == raw);
    wr(ENABLE, 1);
    env.events_n = 0;
    wr(DATA_CMD, 9);
    CHECK(env.events_n > 0 && is(0, kStart, 0));                 // the host is still wired
}

static void test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised() {
    for (int call = kStart; call <= kStopCall; ++call) {
        fresh(kAuto);
        wr(ENABLE, 1);
        env.fail_call = call;
        env.events_n = 0;
        bool ok = true;
        // a read command followed by a stop makes every callback run
        wr(DATA_CMD, 0x10);
        ok = wr(DATA_CMD, 0x20 | CMD | STOP);
        if (call == kStart) {
            CHECK(!ok || i2c.failed());
        }
        CHECK(i2c.failed());
        env.failed = 0;
        env.fail_call = 0;
    }
    fresh(kAuto);
    env.fail_irq = true;
    CHECK(!i2c.complete_stop());                                 // STOP_DET raw, the line update raised: busy was not cleared
    CHECK((i2c.int_raw & R_STOP_DET) != 0 && i2c.state == STATE_IDLE);
    env.fail_irq = false;
    env.failed = 0;

    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.busy);
    i2c.int_enable = R_TX_ABRT;
    env.fail_irq = true;
    CHECK(!i2c.arbitration_lost());                              // abort: reason set, FIFO flushed, then the line raised
    CHECK((i2c.abort_source & ARB_LOST) != 0 && i2c.tx.empty() && i2c.state == STATE_IDLE && !i2c.busy);
    env.fail_irq = false;
    env.failed = 0;
    CHECK(!i2c.failed());
}

static void test_the_window_handler_is_the_bus_entry_point() {
    fresh(kSilent);
    WindowHandler h = i2c.window_handler();
    h.write32(h.ctx, TAR, 0x12, kAtomicNormal);
    h.write32(h.ctx, TAR, 0x01, kAtomicSet);
    CHECK(h.read32(h.ctx, TAR) == 0x13);
    CHECK(h.read32(h.ctx, COMP_TYPE) == 0x44570140u);
}

static void test_more_corners_of_the_state_machine() {
    // complete_start: each of its three guards on its own
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    wr(ENABLE, 0);                                               // flushes the FIFO while the start is pending
    env.events_n = 0;
    CHECK(i2c.complete_start() && is(0, kStopCall) && env.events_n == 1);
    fresh(kDefer);
    i2c.tx.push(1);                                              // not in the START state
    CHECK(i2c.complete_start() && is(0, kStopCall) && env.events_n == 1);
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    i2c.stop = true;
    env.events_n = 0;
    CHECK(i2c.complete_start() && is(0, kStopCall) && env.events_n == 1);
    // the states between the answers
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.complete_start() && i2c.state == STATE_CONNECT && (i2c.int_raw & R_START_DET) != 0 && i2c.busy);
    // a stop requested while the address is answered: the connect is abandoned without an abort
    i2c.stop = true;
    env.events_n = 0;
    CHECK(i2c.complete_connect(true) && i2c.state == STATE_STOP && is(0, kStopCall) && i2c.abort_source == 0);
    // a NACKed address leaves the machine in STOP until the device's stop completes
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.complete_start() && i2c.complete_connect(false) && i2c.state == STATE_STOP && is(2, kStopCall));
    // the abort cannot be raised: the block stops before telling the device
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.complete_start());
    i2c.int_enable = R_TX_ABRT;
    env.fail_irq = true;
    env.events_n = 0;
    CHECK(!i2c.complete_connect(false) && env.events_n == 0 && i2c.state == STATE_CONNECT);
    env.fail_irq = false;
    env.failed = 0;
    // a NACKed data byte, with the stop left to the device
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    CHECK(i2c.complete_start() && i2c.complete_connect(true) && i2c.complete_write(false));
    CHECK(i2c.state == STATE_STOP && (i2c.abort_source & ABRT_TXDATA_NOACK) != 0 && is(3, kStopCall));
    // a read finished by a stop
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, CMD | STOP);
    CHECK(i2c.complete_start() && i2c.complete_connect(true) && i2c.complete_read(3));
    CHECK(i2c.state == STATE_STOP && i2c.rx.count() == 1 && is(3, kStopCall));
    // complete_stop clears the pending restart and the busy flag, and starts the next queued command unless the abort bit says otherwise
    fresh(kSilent);
    i2c.pending_restart = true;
    i2c.busy = true;
    CHECK(i2c.complete_stop() && !i2c.pending_restart && !i2c.busy);
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    wr(ENABLE, 1 | EN_ABORT);
    wr(DATA_CMD, 2);                                             // queued behind the abort
    env.events_n = 0;
    CHECK(i2c.complete_stop() && env.events_n == 0 && (i2c.enable & EN_ABORT) == 0 && i2c.tx.count() == 1);
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 1);
    wr(DATA_CMD, 2);
    env.events_n = 0;
    CHECK(i2c.complete_stop() && is(0, kStart, 0));              // no abort: the next command goes on
    // a command already queued behind a stop is run by the whole chain of completions
    fresh(kAuto);
    wr(ENABLE, EN_ENABLE | EN_TX_CMD_BLOCK);
    wr(DATA_CMD, 0x11 | STOP);
    wr(DATA_CMD, 0x22);
    wr(ENABLE, EN_ENABLE);
    CHECK(is(0, kStart, 0) && is(1, kConnect, 0x55, MODE_WRITE) && is(2, kWrite, 0x11) && is(3, kStopCall) && is(4, kStart, 0) && is(5, kConnect, 0x55, MODE_WRITE) && is(6, kWrite, 0x22));
    // a queue of reads runs through complete_read
    fresh(kAuto);
    wr(ENABLE, EN_ENABLE | EN_TX_CMD_BLOCK);
    for (int i = 0; i < 3; ++i) wr(DATA_CMD, CMD);
    wr(ENABLE, EN_ENABLE);
    CHECK(i2c.rx.count() == 3 && i2c.tx.empty() && is(2, kRead, 1) && is(3, kRead, 1) && is(4, kRead, 1));
    // next_command: not enabled, a restart that is already pending, the restart and pending flags
    fresh(kDefer);
    wr(DATA_CMD, 1);
    CHECK(env.events_n == 0 && i2c.tx.count() == 1);            // not enabled: the command waits
    fresh(kSilent);
    i2c.state = STATE_CONNECTED;
    i2c.pending_restart = true;
    i2c.tx.push(0x20 | RESTART);
    wr(ENABLE, 1);
    CHECK(is(0, kWrite, 0x20) && !i2c.pending_restart);          // already restarted: the command itself goes out
    fresh(kSilent);
    i2c.state = STATE_CONNECTED;
    i2c.stop = true;                                             // a stop is already wanted: no restart, the command goes out as it is
    i2c.tx.push(0x20 | RESTART);
    wr(ENABLE, 1);
    CHECK(is(0, kWrite, 0x20) && !i2c.pending_restart);
    fresh(kDefer);
    wr(ENABLE, 1);
    wr(DATA_CMD, 0x10);
    CHECK(i2c.complete_start() && i2c.complete_connect(true) && i2c.complete_write(true));
    wr(DATA_CMD, 0x20 | RESTART);
    CHECK(is(3, kStart, 1) && i2c.pending_restart);
    CHECK(i2c.complete_start() && i2c.complete_connect(true));
    CHECK(!i2c.pending_restart && is(5, kWrite, 0x20));
    // a failing device call ends the call at once (no TX_EMPTY raised after it)
    for (int call = kWrite; call <= kRead; ++call) {
        fresh(kAuto);
        wr(ENABLE, 1);
        wr(TX_TL, 16);
        env.fail_call = call;
        (void)wr(DATA_CMD, call == kRead ? CMD : 0x33);
        CHECK(i2c.failed() && (i2c.int_raw & R_TX_EMPTY) == 0);
        env.failed = 0;
        env.fail_call = 0;
    }
    // TX_EMPTY at exactly the threshold
    fresh(kDefer);
    wr(TX_TL, 2);
    wr(ENABLE, EN_ENABLE | EN_TX_CMD_BLOCK);
    for (int i = 0; i < 4; ++i) wr(DATA_CMD, 0x10 + i);
    wr(ENABLE, EN_ENABLE);
    CHECK(i2c.complete_start() && i2c.complete_connect(true));   // pulls the first: 3 left
    CHECK((i2c.int_raw & R_TX_EMPTY) == 0 && i2c.tx.count() == 3);
    CHECK(i2c.complete_write(true));                             // pulls the second: 2 left, at the threshold
    CHECK((i2c.int_raw & R_TX_EMPTY) != 0 && i2c.tx.count() == 2);
}

static void test_registers_with_side_effects_and_masks_in_detail() {
    fresh(kSilent);
    // IC_INTR_STAT is raw & mask, not raw
    i2c.int_raw = R_RX_FULL | R_TX_EMPTY;
    i2c.int_enable = R_RX_FULL;
    CHECK(rd(INTR_STAT) == R_RX_FULL);
    // the line follows the masked status, not the raw one
    i2c.int_enable = 0;
    env.irq_n = 0;
    CHECK(i2c.check_interrupts() && !last_irq());
    // every IC_CLR_* register clears its own bit, only, and says so
    static const struct { uint32_t offset, bit; } clears[] = {
        {CLR_RX_UNDER, R_RX_UNDER}, {CLR_RX_OVER, R_RX_OVER}, {CLR_TX_OVER, R_TX_OVER}, {CLR_RD_REQ, R_RD_REQ}, {CLR_TX_ABRT, R_TX_ABRT},
        {CLR_RX_DONE, R_RX_DONE},   {CLR_ACTIVITY, R_ACTIVITY}, {CLR_STOP_DET, R_STOP_DET}, {CLR_START_DET, R_START_DET}, {CLR_GEN_CALL, R_GEN_CALL},
    };
    for (const auto& c : clears) {
        i2c.int_raw = 0xFFF;
        CHECK(rd(c.offset) == 1 && i2c.int_raw == (0xFFFu & ~c.bit));
        CHECK(rd(c.offset) == 0 && i2c.int_raw == (0xFFFu & ~c.bit));
    }
    // IC_CLR_INTR clears exactly those ten, leaving RX_FULL, TX_EMPTY and RESTART_DET
    i2c.int_raw = 0x1FFF;
    CHECK(rd(CLR_INTR) == 1 && i2c.int_raw == (R_RX_FULL | R_TX_EMPTY | R_RESTART_DET));
    // bit 9 of the abort source survives every clear
    i2c.abort_source = ABRT_SBYTE_NORSTRT | ABRT_TXDATA_NOACK;
    i2c.int_raw = R_TX_ABRT;
    (void)rd(CLR_INTR);
    CHECK(i2c.abort_source == ABRT_SBYTE_NORSTRT);
    i2c.abort_source = ABRT_SBYTE_NORSTRT | ABRT_TXDATA_NOACK;
    (void)rd(CLR_TX_ABRT);
    CHECK(i2c.abort_source == ABRT_SBYTE_NORSTRT);
    i2c.abort_source = ABRT_SBYTE_NORSTRT | ABRT_TXDATA_NOACK;
    CHECK(rd(TX_ABRT_SOURCE) == (ABRT_SBYTE_NORSTRT | ABRT_TXDATA_NOACK) && i2c.abort_source == ABRT_SBYTE_NORSTRT);
    // ... and of the next abort
    i2c.abort_source = ABRT_SBYTE_NORSTRT;
    i2c.tx.reset();
    CHECK(i2c.arbitration_lost() && (i2c.abort_source & ABRT_SBYTE_NORSTRT) != 0);
    // IC_ENABLE_STATUS is bit 0 of IC_ENABLE only
    wr(ENABLE, EN_ENABLE | EN_TX_CMD_BLOCK);
    CHECK(rd(ENABLE) == (EN_ENABLE | EN_TX_CMD_BLOCK) && rd(ENABLE_STATUS) == 1);
    // a write clears TX_EMPTY
    i2c.int_raw = R_TX_EMPTY;
    wr(DATA_CMD, 1);
    CHECK((i2c.int_raw & R_TX_EMPTY) == 0);
    // an interrupt that is already raw does not call the line again; a clear of one that is not raw does not either
    fresh(kSilent);
    env.irq_n = 0;
    (void)rd(DATA_CMD);
    (void)rd(DATA_CMD);
    CHECK(env.irq_n == 1);
    (void)rd(CLR_RX_OVER);
    CHECK(env.irq_n == 1);
    // an empty-FIFO read does not touch RX_FULL
    fresh(kSilent);
    i2c.int_raw = R_RX_FULL;
    CHECK(rd(DATA_CMD) == 0 && (i2c.int_raw & R_RX_FULL) != 0);
    // a failing line on a read of a clear register leaves the clear done
    fresh(kSilent);
    i2c.int_raw = R_STOP_DET;
    i2c.int_enable = R_STOP_DET;
    env.fail_irq = true;
    CHECK(rd(CLR_STOP_DET) == 1 && i2c.int_raw == 0 && i2c.failed());
    env.fail_irq = false;
    env.failed = 0;
    // abort() on its own: the reasons stay, the flush count (5) is replaced by the new one (0: nothing queued), the FIFO is flushed
    fresh(kSilent);
    i2c.abort_source = ABRT_SBYTE_NORSTRT | (5u << TX_FLUSH_CNT_SHIFT);
    CHECK(i2c.arbitration_lost());
    CHECK(i2c.abort_source == (ABRT_SBYTE_NORSTRT | ARB_LOST));
    // IC_ENABLE: ABORT works whenever the machine is not idle, and a disable flushes the RX FIFO too
    fresh(kSilent);
    i2c.state = STATE_CONNECTED;
    wr(ENABLE, 1 | EN_ABORT);
    CHECK((i2c.abort_source & ABRT_USER_ABRT) != 0 && i2c.stop && (i2c.enable & EN_ABORT) != 0);
    fresh(kSilent);
    i2c.busy = true;                                             // busy, but the machine is idle: the bit is dropped
    wr(ENABLE, 1 | EN_ABORT);
    CHECK((i2c.enable & EN_ABORT) == 0 && i2c.abort_source == 0 && !i2c.stop);
    fresh(kSilent);
    i2c.rx.push(1);
    wr(ENABLE, 0);
    CHECK(i2c.rx.empty());
    // write_atomic remembers the raw value
    fresh(kSilent);
    wr(TAR, 0x01, kAtomicSet);
    CHECK(i2c.raw_write_value() == 1);
    wr(TAR, 0x77);
    CHECK(i2c.raw_write_value() == 0x77);
}

int main() {
    test_a_default_constructed_block_is_at_power_on();
    test_register_reads_at_power_on();
    test_masks_and_the_invalid_speed_field();
    test_the_default_device_nacks_every_address_and_a_write_aborts();
    test_a_whole_write_transaction_with_an_acking_device();
    test_a_queue_of_commands_runs_in_one_call_stack_through_re_entrant_completions();
    test_a_read_transaction_flags_the_first_byte_and_acks_all_but_the_last();
    test_a_restart_asks_the_device_for_a_repeated_start();
    test_addresses_in_7_and_10_bit_mode_and_the_reasons_for_a_nack();
    test_a_nacked_data_byte_aborts_and_stops();
    test_abort_accounting_keeps_the_flush_count_in_the_high_bits();
    test_the_enable_register();
    test_tx_overflow_rx_overflow_and_the_thresholds();
    test_tx_empty_is_raised_at_or_below_the_threshold_and_cleared_by_a_write();
    test_the_clear_registers_return_whether_they_cleared_and_clear_the_abort_source();
    test_the_interrupt_mask_register_gates_the_line();
    test_status_composition();
    test_sda_hold_and_spike_length_are_written_only_while_disabled_with_their_minimum();
    test_unimplemented_offsets_warn_and_read_all_ones();
    test_alias_writes_decode_against_a_read_with_its_side_effects();
    test_arbitration_loss_aborts_and_goes_idle();
    test_a_completion_in_the_wrong_state_is_handled_as_the_reference_does();
    test_reset_clears_the_state_and_keeps_the_host_and_raw_write_value();
    test_a_failing_host_call_stops_the_block_where_the_reference_would_have_raised();
    test_the_window_handler_is_the_bus_entry_point();
    test_more_corners_of_the_state_machine();
    test_registers_with_side_effects_and_masks_in_detail();
    test_datasheet_audit_flags_flush_activity_and_the_stored_registers();
    if (failures == 0) std::printf("test_i2c: ok\n");
    return failures == 0 ? 0 : 1;
}
