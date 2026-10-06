// Standalone checks of src/rp2040py/native/core/ssi.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_ssi.py) that the lockstep differential (tests/test_ssi_diff.py) also pins, on a real C++ PinBank for
// QSPI_SS, so the header is exercised - and mutation-tested - without Cython.
#include <cstdio>
#include <cstring>

#include "pin.hpp"
#include "ssi.hpp"

using namespace rp2040core;
using namespace rp2040core::ssi_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static const uint32_t kFlashSize = 0x20000;
static uint8_t flash[kFlashSize];

// QSPI_SS forced low / high through the pin's ctrl override (pico-sdk's flash_cs_force()), and released (no override at all)
static const uint32_t kOeForce = 3u << 12;
static const uint32_t kCsLow = kOeForce | (2u << 8), kCsHigh = kOeForce | (3u << 8), kCsReleased = 0;

struct Env {
    uint32_t warn_kind[32], warn_offset[32];
    int64_t warn_value[32];
    int warns = 0;
    bool cs_low_answer = false;
    bool cs_low_fail = false;
    int cs_low_calls = 0;
};
static Env env;

static void on_warn(void*, uint32_t kind, uint32_t offset, int64_t value) {
    if (env.warns < 32) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
        env.warn_value[env.warns] = value;
    }
    ++env.warns;
}
static bool on_cs_low(void*, bool* low) {
    ++env.cs_low_calls;
    *low = env.cs_low_answer;
    return !env.cs_low_fail;
}

static PinBank cs_pin;
static SsiBlock ssi;

static void cs(uint32_t ctrl) {
    cs_pin.pin(0).ctrl = ctrl;
    (void)cs_pin.check_for_updates(0);
}
static uint32_t rd(uint32_t offset) { return ssi.read(offset); }
static void wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { ssi.write_atomic(offset, value, atomic); }

// One whole transaction, the way the bootrom does it; the bytes the flash answered are left in `answer`.
static uint8_t answer[512];
static void send(const uint8_t* bytes, uint32_t length) {
    wr(SSIENR, 1);
    cs(kCsLow);
    for (uint32_t i = 0; i < length; ++i) {
        wr(DR0, bytes[i]);
        answer[i] = static_cast<uint8_t>(rd(DR0));
    }
    cs(kCsHigh);
}
static void send1(uint8_t a) { send(&a, 1); }
static void command(uint8_t opcode, uint32_t address, const uint8_t* data, uint32_t length) {
    uint8_t buf[512];
    buf[0] = opcode;
    buf[1] = static_cast<uint8_t>(address >> 16);
    buf[2] = static_cast<uint8_t>(address >> 8);
    buf[3] = static_cast<uint8_t>(address);
    for (uint32_t i = 0; i < length; ++i) buf[4 + i] = data[i];
    send(buf, 4 + length);
}
static uint8_t status1() {
    const uint8_t t[2] = {CMD_READ_STATUS_1, 0};
    send(t, 2);
    return answer[1];
}

static void fresh() {
    std::memset(flash, 0xFF, sizeof flash);
    cs(kCsHigh);
    wr(SSIENR, 0);
    (void)ssi.reset();
    cs(kCsHigh);
    env.warns = 0;
}

int main() {
    PinHost host;
    const bool always_output_enabled = true;  // QSPI_SS is
    CHECK(cs_pin.init(1, host, &always_output_enabled, 1));
    cs(kCsHigh);
    SsiHost ssi_host;
    ssi_host.warn = on_warn;
    ssi.init(flash, kFlashSize, ssi_host);
    CHECK(ssi.bind_cs(&cs_pin));
    fresh();

    // ---- the register file ----------------------------------------------------------------------------------------------
    CHECK(rd(IDR) == 0x51535049u && rd(VERSION_ID) == 0x3430312Au);
    CHECK(rd(SR) == (SR_TFE | SR_TFNF) && rd(RXFLR) == 0);
    wr(CTRLR0, 0xDEADBEEF);
    wr(CTRLR1, 0x1234);
    wr(BAUDR, 6);
    wr(TXFLR, 77);
    wr(SPI_CTRL_R0, 0x1F0F0F0F);
    wr(RX_SAMPLE_DLY, 0x1FF);
    wr(TXD_DRIVE_EDGE, 0x2A5);
    // datasheet widths: CTRLR0 24 and 22:0, SPI_CTRLR0 31:24, 18:11, 9:8 and 5:0; TXFLR is read only
    CHECK(rd(CTRLR0) == (0xDEADBEEFu & CTRLR0_MASK) && rd(CTRLR1) == 0x1234 && rd(BAUDR) == 6 && rd(TXFLR) == 0 && rd(SPI_CTRL_R0) == (0x1F0F0F0Fu & SPI_CTRLR0_MASK));
    CHECK(rd(RX_SAMPLE_DLY) == 0xFF && rd(TXD_DRIVE_EDGE) == 0xA5);  // 8 bits each
    wr(RXFLR, 9);
    CHECK(rd(RXFLR) == 0);  // read-only
    CHECK(env.warns == 0);
    wr(CTRLR1, 0xFFFFFFFFu);
    wr(BAUDR, 0xFFFFFFFFu);
    wr(SSIENR, 0xFFFFFFFFu);
    CHECK(rd(CTRLR1) == 0xFFFF && rd(BAUDR) == 0xFFFF && rd(SSIENR) == 1);
    wr(SSIENR, 0);
    // the stored, not-acted-on registers: widths, and the reset value of SPI_CTRLR0 (XIP_CMD = 0x03)
    wr(MWCR, 0xFFFFFFFFu); wr(SER, 0xFFFFFFFFu); wr(TXFTLR, 0xFFFFFFFFu); wr(RXFTLR, 0xFFFFFFFFu); wr(IMR, 0xFFFFFFFFu); wr(DMACR, 0xFFFFFFFFu); wr(DMATDLR, 0xFFFFFFFFu); wr(DMARDLR, 0xFFFFFFFFu);
    CHECK(rd(MWCR) == 7 && rd(SER) == 1 && rd(TXFTLR) == 0xFF && rd(RXFTLR) == 0xFF && rd(IMR) == 0x3F && rd(DMACR) == 3 && rd(DMATDLR) == 0xFF && rd(DMARDLR) == 0xFF);
    wr(ISR, 0xFFFFFFFFu); wr(ICR, 0xFFFFFFFFu);
    CHECK(rd(ISR) == 0 && rd(RISR) == 0 && rd(ICR) == 0 && rd(TXOICR) == 0 && rd(RXOICR) == 0 && rd(RXUICR) == 0 && rd(MSTICR) == 0);   // read-only, nothing is raised
    CHECK(ssi.reset());
    CHECK(rd(SPI_CTRL_R0) == 0x03000000u && rd(IMR) == 0 && rd(SER) == 0 && rd(CTRLR0) == 0);
    fresh();
    CHECK(rd(0x64) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kSsiWarnRead && env.warn_offset[0] == 0x64);
    env.warns = 0;
    CHECK(rd(0x1064) == 0xFFFFFFFFu && env.warns == 2 && env.warn_kind[0] == kSsiWarnRead && env.warn_kind[1] == kSsiWarnReadAtomicArea);
    env.warns = 0;
    wr(0x68, 0x1234);
    CHECK(env.warns == 1 && env.warn_kind[0] == kSsiWarnWrite && env.warn_offset[0] == 0x68 && env.warn_value[0] == 0x1234);
    env.warns = 0;
    // the atomic aliases decode against a read of the register
    wr(CTRLR0, 0x0F, kAtomicNormal);
    wr(CTRLR0, 0x3C, kAtomicSet);  // 0x0F | 0x3C (an xor would give 0x33)
    CHECK(rd(CTRLR0) == 0x3F);
    wr(CTRLR0, 0x0F, kAtomicXor);
    CHECK(rd(CTRLR0) == 0x30);
    wr(CTRLR0, 0x70, kAtomicClear);  // 0x30 & ~0x70 = 0 (an and would give 0x30)
    CHECK(rd(CTRLR0) == 0 && ssi.raw_write_value() == 0x70);
    CHECK(rd(0x1000) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kSsiWarnRead);  // offset 0x1000 itself is not "in the atomic area"
    env.warns = 0;

    // a finished command leaves nothing behind
    {
        const uint8_t t[3] = {CMD_READ_STATUS_1, 0, 0};
        send(t, 3);
        CHECK(ssi.tx_length() == 0 && ssi.ssienr() == 1 && !ssi.cs_asserted());
    }

    // ---- JEDEC id, status, the cheap commands ----------------------------------------------------------------------------
    fresh();
    {
        const uint8_t jedec[5] = {CMD_READ_JEDEC_ID, 0, 0, 0, 0};
        send(jedec, 5);
        CHECK(answer[0] == 0xFF && answer[1] == 0xEF && answer[2] == 0x40 && answer[3] == 0x15 && answer[4] == 0);
        const uint8_t s2[2] = {CMD_READ_STATUS_2, 0};
        send(s2, 2);
        CHECK(answer[1] == STATUS2_QE);
        const uint8_t unknown[4] = {0xAB, 1, 2, 3};
        send(unknown, 4);
        CHECK(answer[0] == 0xFF && answer[1] == 0xFF && answer[2] == 0xFF && answer[3] == 0xFF);
        CHECK(status1() == 0);
        send1(CMD_WRITE_ENABLE);
        CHECK(ssi.write_enabled() && status1() == STATUS_WEL);  // reading the status does not clear it
        send1(CMD_WRITE_DISABLE);
        CHECK(!ssi.write_enabled() && status1() == 0);
        send1(CMD_WRITE_ENABLE);
        const uint8_t ws[2] = {CMD_WRITE_STATUS, 0x55};
        send(ws, 2);  // accepted, answered 0, ignored - and the latch is cleared when the command ends
        CHECK(answer[1] == 0 && !ssi.write_enabled());
    }

    // ---- erase -------------------------------------------------------------------------------------------------------------
    fresh();
    for (uint32_t i = 0; i < kFlashSize; ++i) flash[i] = static_cast<uint8_t>(i * 7 + 1);
    command(CMD_SECTOR_ERASE, 0x1234, nullptr, 0);  // no write enable: nothing happens
    CHECK(flash[0x1000] == static_cast<uint8_t>(0x1000 * 7 + 1));
    send1(CMD_WRITE_ENABLE);
    command(CMD_SECTOR_ERASE, 0x1234, nullptr, 0);
    CHECK(flash[0xFFF] == static_cast<uint8_t>(0xFFF * 7 + 1) && flash[0x1000] == 0xFF && flash[0x1FFF] == 0xFF && flash[0x2000] == static_cast<uint8_t>(0x2000 * 7 + 1));
    CHECK(!ssi.write_enabled());  // cleared by the erase
    command(CMD_SECTOR_ERASE, 0x3000, nullptr, 0);  // so a second erase needs another write enable
    CHECK(flash[0x3000] == static_cast<uint8_t>(0x3000 * 7 + 1));
    send1(CMD_WRITE_ENABLE);
    command(CMD_BLOCK_ERASE, 0x1ABCD, nullptr, 0);  // 64 KiB, aligned down to 0x10000
    CHECK(flash[0xFFFF] == static_cast<uint8_t>(0xFFFF * 7 + 1) && flash[0x10000] == 0xFF && flash[0x1FFFF] == 0xFF);
    // an erase shorter than its address is not applied - and does not clear the latch (it is not an erase yet)
    send1(CMD_WRITE_ENABLE);
    {
        const uint8_t shortcmd[3] = {CMD_SECTOR_ERASE, 0, 0};
        send(shortcmd, 3);
        CHECK(ssi.write_enabled() && flash[0] == static_cast<uint8_t>(1));
    }
    // a block erase with write enable off still clears nothing, and still consumes the latch only when it was set
    send1(CMD_WRITE_DISABLE);
    command(CMD_BLOCK_ERASE, 0, nullptr, 0);
    CHECK(flash[0] == 1);

    // an empty command (chip-select toggled with nothing shifted) applies nothing, whatever opcode the last command left behind
    fresh();
    {
        const uint8_t ws[2] = {CMD_WRITE_STATUS, 0};
        send(ws, 2);
        ssi.set_write_enabled(true);
        cs(kCsLow);
        cs(kCsHigh);
        CHECK(ssi.write_enabled());
    }

    // ---- page program ----------------------------------------------------------------------------------------------------
    fresh();
    {
        const uint8_t data[4] = {0xF0, 0x0F, 0xAA, 0x55};
        command(CMD_PAGE_PROGRAM, 0x100, data, 4);  // no write enable
        CHECK(flash[0x100] == 0xFF);
        send1(CMD_WRITE_ENABLE);
        command(CMD_PAGE_PROGRAM, 0x100, data, 4);
        CHECK(flash[0x100] == 0xF0 && flash[0x101] == 0x0F && flash[0x102] == 0xAA && flash[0x103] == 0x55 && flash[0x104] == 0xFF && !ssi.write_enabled());
        const uint8_t more[4] = {0x3C, 0xFF, 0x0F, 0xFF};  // programming only clears bits: AND, never overwrite
        send1(CMD_WRITE_ENABLE);
        command(CMD_PAGE_PROGRAM, 0x100, more, 4);
        CHECK(flash[0x100] == (0xF0 & 0x3C) && flash[0x101] == 0x0F && flash[0x102] == (0xAA & 0x0F) && flash[0x103] == 0x55);
        send1(CMD_WRITE_ENABLE);  // a program with no data bytes is not a program: the latch stays
        command(CMD_PAGE_PROGRAM, 0x200, nullptr, 0);
        CHECK(ssi.write_enabled() && flash[0x200] == 0xFF);
        send1(CMD_WRITE_DISABLE);
        uint8_t big[300];
        for (int i = 0; i < 300; ++i) big[i] = static_cast<uint8_t>(i ^ 0x80);
        send1(CMD_WRITE_ENABLE);
        command(CMD_PAGE_PROGRAM, 0x400, big, 300);  // only 256 data bytes are kept
        CHECK(flash[0x400] == 0x80 && flash[0x401] == 0x81 && flash[0x4FE] == 0x7E && flash[0x4FF] == 0x7F);
        CHECK(flash[0x500] == 0xFF);  // the 257th data byte (0x00) was not applied
        send1(CMD_WRITE_ENABLE);
        command(CMD_PAGE_PROGRAM, 0x800, big, 257);  // exactly one data byte more than a page
        CHECK(flash[0x8FF] == 0x7F && flash[0x900] == 0xFF);
        send1(CMD_WRITE_ENABLE);
        command(CMD_PAGE_PROGRAM, 0xA00, big, 256);  // exactly a page
        CHECK(flash[0xAFF] == 0x7F && flash[0xB00] == 0xFF);
        send1(CMD_WRITE_ENABLE);
        command(CMD_PAGE_PROGRAM, kFlashSize - 2, big + 10, 4);  // past the end of the flash: only what fits
        CHECK(flash[kFlashSize - 2] == (10 ^ 0x80) && flash[kFlashSize - 1] == (11 ^ 0x80));
    }

    // ---- read data -------------------------------------------------------------------------------------------------------
    fresh();
    for (uint32_t i = 0; i < kFlashSize; ++i) flash[i] = static_cast<uint8_t>(i ^ (i >> 8));
    {
        uint8_t zeros[300] = {};
        command(CMD_READ_DATA, 0x1F0, zeros, 300);  // longer than the 260 bytes the command keeps: read by position
        bool ok = true;
        for (uint32_t i = 0; i < 300; ++i) ok = ok && answer[4 + i] == flash[0x1F0 + i];
        CHECK(ok && answer[0] == 0xFF && answer[1] == 0xFF && answer[2] == 0xFF && answer[3] == 0xFF);
        command(CMD_READ_DATA, kFlashSize - 2, zeros, 4);  // past the end: 0xFF
        CHECK(answer[4] == flash[kFlashSize - 2] && answer[5] == flash[kFlashSize - 1] && answer[6] == 0xFF && answer[7] == 0xFF);
    }

    // ---- chip-select and the RX queue --------------------------------------------------------------------------------------
    fresh();
    wr(SSIENR, 1);
    cs(kCsLow);
    wr(DR0, CMD_READ_STATUS_1);
    wr(DR0, 0);
    wr(DR0, 0);
    CHECK(rd(RXFLR) == 3 && (rd(SR) & SR_RFNE) && ssi.tx_length() == 3);
    CHECK(rd(DR0) == 0xFF && rd(DR0) == 0 && rd(RXFLR) == 1);  // in order
    cs(kCsHigh);
    cs(kCsLow);  // asserting starts a fresh command and empties the queue
    CHECK(rd(RXFLR) == 0 && ssi.tx_length() == 0 && !(rd(SR) & SR_RFNE));
    CHECK(rd(DR0) == 0);  // an empty queue reads 0
    cs(kCsHigh);
    wr(DR0, 0x77);  // chip-select deasserted: the idle bus answers, so firmware's flow control sees a byte
    CHECK(rd(RXFLR) == 1 && rd(DR0) == 0xFF && ssi.tx_length() == 0);
    wr(SSIENR, 0);
    cs(kCsLow);
    wr(DR0, 0x77);  // SSIENR clear: ignored
    CHECK(rd(RXFLR) == 0);
    cs(kCsHigh);
    cs(kCsHigh);  // no edge, nothing changes
    cs(kCsLow);
    cs(kCsHigh);  // an empty command applies nothing
    CHECK(rd(RXFLR) == 0 && !ssi.cs_asserted());
    // the ring is bounded: above its capacity the oldest byte is dropped (undefined in the reference)
    fresh();
    for (uint32_t i = 0; i < SsiBlock::kRxCapacity + 5; ++i) ssi.rx_push(static_cast<uint8_t>(i));
    CHECK(rd(RXFLR) == SsiBlock::kRxCapacity && rd(DR0) == 5);
    // a DR0 alias read pops, whatever it is decoded for
    fresh();
    ssi.rx_push(0x11);
    ssi.rx_push(0x22);
    CHECK(rd(RXFLR) == 2);
    wr(DR0, 0, kAtomicSet);  // the decode reads DR0 first; the byte it popped is gone (SSIENR is clear, so nothing is pushed back)
    CHECK(rd(RXFLR) == 1 && rd(DR0) == 0x22);

    // the views the shell reads: the ring is read from its head, the command from its start
    fresh();
    for (uint32_t i = 0; i < 10; ++i) ssi.rx_push(static_cast<uint8_t>(0xA0 + i));
    for (int i = 0; i < 4; ++i) (void)rd(DR0);
    CHECK(ssi.rx_count() == 6 && ssi.rx_at(0) == 0xA4 && ssi.rx_at(5) == 0xA9);
    for (uint32_t i = 0; i < 5; ++i) ssi.tx_append(static_cast<uint8_t>(0x10 + i));
    CHECK(ssi.tx_length() == 5 && ssi.tx_at(0) == 0x10 && ssi.tx_at(4) == 0x14 && ssi.tx_at(1000) == 0);
    for (uint32_t i = 0; i < 400; ++i) ssi.tx_append(0x55);
    CHECK(ssi.tx_length() == 405 && ssi.tx_at(SsiBlock::kTxKept - 1) == 0x55 && ssi.tx_at(SsiBlock::kTxKept) == 0);  // counts past what it keeps
    ssi.set_ssienr(3);
    CHECK(ssi.ssienr() == 3 && rd(SSIENR) == 3);

    // ---- reset ---------------------------------------------------------------------------------------------------------------
    fresh();
    wr(CTRLR0, 5);
    wr(BAUDR, 6);
    wr(SSIENR, 1);
    send1(CMD_WRITE_ENABLE);
    cs(kCsLow);
    wr(DR0, CMD_READ_JEDEC_ID);
    CHECK(ssi.cs_asserted() && ssi.tx_length() == 1 && rd(RXFLR) == 1);
    CHECK(ssi.reset());  // mid-command: the command and the queue go, the pin is still forced low
    CHECK(rd(CTRLR0) == 0 && rd(BAUDR) == 0 && rd(SSIENR) == 0 && !ssi.write_enabled() && ssi.tx_length() == 0 && rd(RXFLR) == 0 && ssi.cs_asserted());
    cs(kCsHigh);
    CHECK(ssi.reset() && !ssi.cs_asserted());
    // an always-output-enabled pad nothing drives resolves LOW, i.e. *asserted*: reset reads the pin, it does not assume
    cs(kCsReleased);
    CHECK(ssi.reset() && ssi.cs_asserted());
    cs(kCsHigh);

    // ---- binding ----------------------------------------------------------------------------------------------------------------
    {
        SsiBlock other;
        other.init(flash, kFlashSize, ssi_host);
        cs(kCsLow);
        CHECK(other.bind_cs(&cs_pin) && other.cs_asserted());  // synced from the pin at bind time, not assumed
        cs(kCsHigh);
        CHECK(!other.cs_asserted());
        other.detach();
        cs(kCsLow);
        CHECK(!other.cs_asserted());  // detached: it no longer hears the pin
        cs(kCsHigh);
    }
    {
        // a pin whose level cannot be read (a failing source): bind and reset report it
        struct Fail {
            static bool source(void*, uint32_t, uint32_t*) { return false; }
        };
        PinHost failing;
        failing.source = Fail::source;
        PinBank bad_pin;
        CHECK(bad_pin.init(1, failing, &always_output_enabled, 1));
        bad_pin.pin(0).ctrl = 5;  // SIO function: the level comes from a source
        SsiBlock other;
        other.init(flash, kFlashSize, ssi_host);
        CHECK(!other.bind_cs(&bad_pin));
        other.detach();
    }
    {
        // no native pin: a Python chip-select calls on_cs_change() and reset() asks the host
        SsiBlock python_cs;
        SsiHost fallback = ssi_host;
        fallback.cs_low = on_cs_low;
        python_cs.init(flash, kFlashSize, fallback);
        env.cs_low_answer = true;
        CHECK(python_cs.reset() && python_cs.cs_asserted() && env.cs_low_calls == 1);
        python_cs.on_cs_change(false);
        CHECK(!python_cs.cs_asserted());
        env.cs_low_fail = true;
        CHECK(!python_cs.reset());  // the failure is pending with the caller
        env.cs_low_fail = false;
    }

    if (failures == 0) std::printf("test_ssi: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
