// Standalone checks of src/rp2040py/native/core/xip_ctrl.hpp and of the memory map's XIP hooks (see tests/test_core_cpp.py for the flags).
//
// The end-to-end proof is tests/test_xip_diff.py (the reference against the chip's XIP, thousands of random steps with every mutant of the reference caught); these are the directed checks that need no Python:
// the power-on registers, the `flash_cache_perfctr` sequence, the aliases and the replacement, the flush clock, the cache as SRAM, the stream (FIFO depth, DREQ, pace, halting), the registers through the
// window and its atomic aliases, failure injection per host function - and the memory map: the flash window read-only to the bus in every width and alias, each access reported once, the 0x15 segment.
#include <cstdio>

#include "memory_map.hpp"
#include "xip_ctrl.hpp"

using namespace rp2040core;
using namespace rp2040core::xip_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t warn_kind[16], warn_address[16];
    int warns = 0;
    int dreq_calls = 0;
    uint32_t dreq_number = 0;
    bool dreq_level = false;
    int failed = 0;
    bool fail_dreq = false, fail_clk = false, fail_baudr = false;
    double clk_sys = 125e6, baudr = 2.0;
};
static Env env;
static Clock clk;
static XipBlock xip;
static ChipPorts ports;
static constexpr uint32_t kTestDreq = 37;  // DREQ_XIP_STREAM
static uint8_t flash[0x1000000];

static void on_warn(void*, uint32_t, uint32_t kind, uint32_t address, int64_t) {
    if (env.warns < 16) {
        env.warn_kind[env.warns] = kind;
        env.warn_address[env.warns] = address;
    }
    ++env.warns;
}
static bool on_dreq(void*, uint32_t number, bool asserted) {
    env.dreq_number = number;
    ++env.dreq_calls;
    env.dreq_level = asserted;
    if (env.fail_dreq) {
        env.failed = 1;
        return false;
    }
    return true;
}
static bool on_clk(void*, double* hz) {
    if (env.fail_clk) {
        env.failed = 1;
        return false;
    }
    *hz = env.clk_sys;
    return true;
}
static bool on_baudr(void*, double* value) {
    if (env.fail_baudr) {
        env.failed = 1;
        return false;
    }
    *value = env.baudr;
    return true;
}

static void fresh() {
    env = Env();
    clk.reset();
    for (uint32_t i = 0; i < sizeof flash; ++i) flash[i] = static_cast<uint8_t>(i * 7 + 1);
    XipHost host;
    host.clk_sys = on_clk;
    host.baudr = on_baudr;
    ports = ChipPorts();
    ports.warn = on_warn;
    ports.dreq = on_dreq;
    ports.failed = &env.failed;
    xip.detach();
    xip.init(&clk, flash, sizeof flash, &ports, 3, kTestDreq, host);
    CHECK(xip.reset());
    env.warns = 0;
}
static uint32_t rd(uint32_t offset) { return xip.read(offset); }
static bool wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) { return xip.write_atomic(offset, value, atomic); }
static void settle_flush() { rd(REG_FLUSH); }
static const uint32_t XIP0 = 0x10000000u, NOALLOC = 0x11000000u, NOCACHE = 0x12000000u, BYPASS = 0x13000000u;

static void test_power_on_state() {
    fresh();
    CHECK(rd(REG_CTRL) == (CTRL_EN | CTRL_ERR_BADWRITE));
    CHECK(rd(REG_CTR_HIT) == 0 && rd(REG_CTR_ACC) == 0 && rd(REG_STREAM_ADDR) == 0 && rd(REG_STREAM_CTR) == 0);
    CHECK(rd(REG_STAT) == STAT_FIFO_EMPTY);  // the flush that follows a reset is still running
    CHECK(!env.dreq_level && env.dreq_calls == 1);
    settle_flush();
    CHECK(rd(REG_STAT) == (STAT_FLUSH_READY | STAT_FIFO_EMPTY));
}

static void test_the_flush_clock() {
    fresh();
    CHECK(!xip.flush_ready());
    clk.tick(1025 * 1e9 / 125e6);
    CHECK(!xip.flush_ready());  // just over 1024 cycles: 1026
    clk.tick(2 * 1e9 / 125e6);
    CHECK(xip.flush_ready());
    CHECK(wr(REG_FLUSH, 1) && !xip.flush_ready());
    CHECK(rd(REG_FLUSH) == 0 && xip.flush_ready());  // a read of FLUSH waits for it
    CHECK(wr(REG_FLUSH, 0) && xip.flush_ready());     // writing 0 flushes nothing
    env.clk_sys = 12e6;
    CHECK(wr(REG_FLUSH, 1));
    clk.tick(1100 * 1e9 / 125e6);
    CHECK(!xip.flush_ready());  // the flush takes cycles of the clock the chip runs at
    clk.tick(1100 * 1e9 / 12e6);
    CHECK(xip.flush_ready());
}

static void test_the_perfctr_sequence() {
    fresh();
    wr(REG_FLUSH, 1);
    settle_flush();
    wr(REG_CTR_ACC, 0);
    wr(REG_CTR_HIT, 0);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_ACC) == 1 && rd(REG_CTR_HIT) == 0);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_ACC) == 2 && rd(REG_CTR_HIT) == 1);
    xip.write_access(XIP0);  // a write deletes the entry and counts as neither
    CHECK(rd(REG_CTR_ACC) == 2 && rd(REG_CTR_HIT) == 1);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_ACC) == 3 && rd(REG_CTR_HIT) == 1);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_ACC) == 4 && rd(REG_CTR_HIT) == 2);
    xip.access(XIP0 + 4);  // the same 8-byte line
    CHECK(rd(REG_CTR_HIT) == 3);
    xip.access(XIP0 + 8);
    CHECK(rd(REG_CTR_HIT) == 3);
    wr(REG_FLUSH, 1);  // a flush forgets everything
    xip.access(XIP0);
    CHECK(rd(REG_CTR_HIT) == 3);
}

static void test_ways_and_replacement() {
    fresh();
    const uint32_t stride = 1024 * 8;
    for (uint32_t n = 0; n < 3; ++n) xip.access(XIP0 + n * stride);  // the third evicts the least recently used of the first two
    wr(REG_CTR_HIT, 0);
    xip.access(XIP0 + 2 * stride);
    xip.access(XIP0 + 1 * stride);
    CHECK(rd(REG_CTR_HIT) == 2);
    xip.access(XIP0);  // was evicted
    CHECK(rd(REG_CTR_HIT) == 2);
    // a hit makes its way the most recently used one
    fresh();
    xip.access(XIP0);
    xip.access(XIP0 + stride);
    xip.access(XIP0);                 // way of tag 0 is now the most recent
    xip.access(XIP0 + 2 * stride);    // evicts tag 1
    wr(REG_CTR_HIT, 0);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_HIT) == 1);
    xip.access(XIP0 + stride);
    CHECK(rd(REG_CTR_HIT) == 1);
    // a write invalidates both ways that match
    fresh();
    xip.access(XIP0);
    xip.access(XIP0 + stride);
    xip.write_access(XIP0 + stride);
    wr(REG_CTR_HIT, 0);
    xip.access(XIP0 + stride);
    CHECK(rd(REG_CTR_HIT) == 0);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_HIT) == 1);
}

static void test_aliases() {
    fresh();
    xip.access(NOALLOC + 0x40);  // a miss that does not allocate
    xip.access(NOALLOC + 0x40);
    CHECK(rd(REG_CTR_HIT) == 0);
    xip.access(NOCACHE + 0x40);  // never checks for a hit, always updates the cache
    CHECK(rd(REG_CTR_HIT) == 0);
    xip.access(NOALLOC + 0x40);  // now present
    CHECK(rd(REG_CTR_HIT) == 1);
    xip.access(BYPASS + 0x80);  // counted, never looked up
    xip.access(BYPASS + 0x80);
    xip.access(XIP0 + 0x80);
    CHECK(rd(REG_CTR_HIT) == 1 && rd(REG_CTR_ACC) == 7);
    // bypass never looks up, even a line that is there
    fresh();
    xip.access(XIP0 + 0x80);
    xip.access(BYPASS + 0x80);
    CHECK(rd(REG_CTR_HIT) == 0 && rd(REG_CTR_ACC) == 2);
    // an allocation fills way 0 first, then way 1
    fresh();
    xip.access(XIP0 + 8);  // set 1
    CHECK(xip.tags[1][0] == 0 && xip.tags[1][1] == INVALID);
    xip.access(XIP0 + 8 + 1024 * 8);
    CHECK(xip.tags[1][0] == 0 && xip.tags[1][1] == 1);
    // NOCACHE on a line that is there makes it the most recently used
    fresh();
    xip.access(XIP0);
    xip.access(XIP0 + 1024 * 8);
    xip.access(NOCACHE);                     // tag 0 again: the most recent
    xip.access(XIP0 + 2 * 1024 * 8);         // evicts tag 1
    wr(REG_CTR_HIT, 0);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_HIT) == 1);
    // the address bit 23 is part of the tag (24-bit flash addresses)
    fresh();
    xip.access(XIP0);
    xip.access(XIP0 + 0x00800000u);
    CHECK(rd(REG_CTR_HIT) == 0);
    // with the cache off every alias bypasses
    fresh();
    wr(REG_CTRL, CTRL_ERR_BADWRITE);
    xip.access(XIP0 + 0x10);
    xip.access(XIP0 + 0x10);
    CHECK(rd(REG_CTR_HIT) == 0 && rd(REG_CTR_ACC) == 2);
    // the address bits above the flash size do not take part: 0x10 + 16 MB is the next alias
    fresh();
    xip.access(XIP0 + 0x00800000u);
    xip.access(XIP0 + 0x00800000u);
    CHECK(rd(REG_CTR_HIT) == 1);
    // writes: only the caching alias is deleted from, any other faults (reported) when ERR_BADWRITE is set
    fresh();
    xip.write_access(NOCACHE);
    CHECK(env.warns == 1 && env.warn_kind[0] == kXipWarnBadWrite && env.warn_address[0] == NOCACHE);
    xip.write_access(XIP0);
    CHECK(env.warns == 1);
    wr(REG_CTRL, CTRL_EN);
    xip.write_access(BYPASS);
    CHECK(env.warns == 1);
}

static void test_counters_saturate_and_clear() {
    fresh();
    xip.ctr_acc = xip.ctr_hit = 0xFFFFFFFFu;
    xip.access(XIP0);
    xip.access(XIP0);
    CHECK(rd(REG_CTR_ACC) == 0xFFFFFFFFu && rd(REG_CTR_HIT) == 0xFFFFFFFFu);
    wr(REG_CTR_ACC, 0x1234);
    wr(REG_CTR_HIT, 0x1234);
    CHECK(rd(REG_CTR_ACC) == 0 && rd(REG_CTR_HIT) == 0);
}

static void test_ctrl() {
    fresh();
    wr(REG_CTRL, 0xFFFFFFFFu);
    CHECK(rd(REG_CTRL) == (CTRL_ERR_BADWRITE | CTRL_POWER_DOWN));  // reserved bits do not stick; POWER_DOWN forces EN to 0
    wr(REG_CTRL, CTRL_EN);
    CHECK(rd(REG_CTRL) == CTRL_EN);
    wr(REG_CTRL, CTRL_EN | CTRL_POWER_DOWN);
    CHECK(rd(REG_CTRL) == CTRL_POWER_DOWN);
}

static void test_the_cache_as_sram() {
    fresh();
    CHECK(!xip.sram_available());
    CHECK(xip.sram_read(0x15000000u) == 0xFFFFFFFFu && env.warns == 1 && env.warn_kind[0] == kXipWarnSramRead);
    xip.sram_write(0x15000000u, 0xAABBCCDDu);
    CHECK(env.warns == 2 && env.warn_kind[1] == kXipWarnSramWrite);
    wr(REG_CTRL, CTRL_ERR_BADWRITE);
    CHECK(xip.sram_available());
    xip.sram_write(0x15000004u, 0xAABBCCDDu);
    CHECK(xip.sram_read(0x15000004u) == 0xAABBCCDDu && xip.sram_read(0x15004004u) == 0xAABBCCDDu && xip.sram_read(0x15FFC004u) == 0xAABBCCDDu);  // 16 kB, mirrored
    CHECK(xip.sram[4] == 0xDD && xip.sram[7] == 0xAA);
    CHECK(xip.sram_read(0x15000000u) == 0 && env.warns == 2);
    xip.sram_write(0x15002000u, 0x11u);  // the RAM is 16 kB: the half is not a mirror
    CHECK(xip.sram_read(0x15000000u) == 0 && xip.sram_read(0x15002000u) == 0x11u);
    wr(REG_CTRL, CTRL_ERR_BADWRITE | CTRL_POWER_DOWN);
    CHECK(xip.sram_read(0x15000004u) == 0xFFFFFFFFu && env.warns == 3);
    // kept through a flush and a reset
    wr(REG_CTRL, CTRL_ERR_BADWRITE);
    wr(REG_FLUSH, 1);
    CHECK(xip.reset());
    wr(REG_CTRL, 0);
    CHECK(xip.sram_read(0x15000004u) == 0xAABBCCDDu);
}

static double word_ns() { return env.baudr * 32 * 1e9 / env.clk_sys; }

static void test_the_stream() {
    fresh();
    for (uint32_t n = 0; n < 4; ++n) {
        const uint32_t word = 0x1000 + n;
        for (uint32_t b = 0; b < 4; ++b) flash[0x100 + n * 4 + b] = static_cast<uint8_t>(word >> (8 * b));
    }
    wr(REG_STREAM_ADDR, 0x103);
    CHECK(rd(REG_STREAM_ADDR) == 0x100);  // word aligned
    wr(REG_STREAM_CTR, 3);
    CHECK(xip.stream_in_flight && (rd(REG_STAT) & STAT_FIFO_EMPTY));
    clk.tick(word_ns() - 1);
    CHECK(xip.fifo_len == 0);
    clk.tick(1);
    CHECK(xip.fifo_len == 1 && env.dreq_level && !(rd(REG_STAT) & STAT_FIFO_EMPTY) && !(rd(REG_STAT) & STAT_FIFO_FULL));
    clk.tick(word_ns());
    CHECK(xip.fifo_len == 2 && (rd(REG_STAT) & STAT_FIFO_FULL) && !xip.stream_in_flight);
    clk.tick(10 * word_ns());  // full: nothing more comes
    CHECK(rd(REG_STREAM_ADDR) == 0x108 && rd(REG_STREAM_CTR) == 1);
    CHECK(rd(REG_STREAM_FIFO) == 0x1000 && env.dreq_level && xip.stream_in_flight);  // a pop made room
    clk.tick(word_ns());
    CHECK(rd(REG_STREAM_CTR) == 0 && !xip.stream_in_flight);
    CHECK(xip.pop_stream_fifo() == 0x1001);
    CHECK(rd(REG_STREAM_FIFO) == 0x1002 && !env.dreq_level);
    CHECK(rd(REG_STREAM_FIFO) == 0);  // empty: 0
    CHECK(rd(REG_STAT) & STAT_FIFO_EMPTY);
    // STREAM_CTR is 22 bits; zero halts and discards the read in flight
    fresh();
    wr(REG_STREAM_CTR, 0xFFFFFFFFu);
    CHECK(rd(REG_STREAM_CTR) == 0x3FFFFFu);
    wr(REG_STREAM_CTR, 0);
    CHECK(rd(REG_STREAM_CTR) == 0 && !xip.stream_in_flight);
    clk.tick(10 * word_ns());
    CHECK(xip.fifo_len == 0 && rd(REG_STREAM_ADDR) == 0);
    // the pace follows SCKDV (at least 2) and clk_sys
    fresh();
    env.baudr = 0;
    wr(REG_STREAM_CTR, 1);
    clk.tick(2 * 32 * 1e9 / 125e6 - 1);
    CHECK(xip.fifo_len == 0);  // a SCKDV of 0 is as slow as 2
    clk.tick(1);
    CHECK(xip.fifo_len == 1);
    fresh();
    env.baudr = 100;
    env.clk_sys = 48e6;
    wr(REG_STREAM_CTR, 1);
    clk.tick(100 * 32 * 1e9 / 48e6 - 1);
    CHECK(xip.fifo_len == 0);
    clk.tick(1);
    CHECK(xip.fifo_len == 1);
    // starting again while a word is in flight does not delay it
    fresh();
    wr(REG_STREAM_CTR, 3);
    clk.tick(word_ns() / 2);
    wr(REG_STREAM_CTR, 3);
    clk.tick(word_ns() / 2);
    CHECK(xip.fifo_len == 1);
    // a reset stops it and empties the FIFO
    fresh();
    wr(REG_STREAM_CTR, 5);
    clk.tick(word_ns());
    CHECK(xip.fifo_len == 1 && env.dreq_level);
    CHECK(xip.reset());
    CHECK(xip.fifo_len == 0 && !env.dreq_level && !xip.stream_in_flight && rd(REG_STREAM_CTR) == 0);
    clk.tick(10 * word_ns());
    CHECK(xip.fifo_len == 0);
    // the end of the image reads as nothing, as a short slice does in the reference
    fresh();
    flash[sizeof flash - 2] = 0x11;
    flash[sizeof flash - 1] = 0x22;
    wr(REG_STREAM_ADDR, 0x00FFFFFC);
    wr(REG_STREAM_CTR, 1);
    clk.tick(word_ns());
    CHECK(xip.pop_stream_fifo() == (static_cast<uint32_t>(flash[0x00FFFFFC]) | static_cast<uint32_t>(flash[0x00FFFFFD]) << 8 | 0x11u << 16 | 0x22u << 24));
    // an image shorter than the flash address space: the bytes past its end read as nothing
    {
        static uint8_t short_image[0x108];
        for (uint32_t i = 0; i < sizeof short_image; ++i) short_image[i] = 0xA0;
        XipHost host;
        host.clk_sys = on_clk;
        host.baudr = on_baudr;
        static uint8_t big[0x200];
        for (uint32_t i = 0; i < sizeof big; ++i) big[i] = 0xB0;
        XipBlock small;
        small.init(&clk, big, 0x102, &ports, 3, kTestDreq, host);  // a 0x102 byte image inside a bigger buffer: reading past 0x102 would find 0xB0
        CHECK(small.reset());
        small.write(REG_STREAM_ADDR, 0x100);
        small.write(REG_STREAM_CTR, 1);
        clk.tick(word_ns());
        CHECK(small.pop_stream_fifo() == 0x0000B0B0u);
        small.detach();
    }
}

static void test_registers_through_the_window() {
    fresh();
    CHECK(rd(0x20) == 0 && rd(0x40) == 0 && rd(0x2000) == 0 && rd(0x1) == 0);  // not all ones
    CHECK(wr(0x40, 0xDEADBEEF) && wr(0x2000, 1) && env.warns == 0);              // past the block: nothing to do
    CHECK(wr(0x1, 5) && env.warns == 1 && env.warn_kind[0] == kRegWarnWrite);   // between the registers: unimplemented
    CHECK(wr(REG_STAT, 7) && wr(REG_STREAM_FIFO, 7));                            // read only
    // atomic aliases decode against a read of the register
    wr(REG_CTRL, 0);
    CHECK(wr(REG_CTRL, CTRL_EN, kAtomicSet) && rd(REG_CTRL) == CTRL_EN);
    CHECK(wr(REG_CTRL, CTRL_ERR_BADWRITE, kAtomicXor) && rd(REG_CTRL) == (CTRL_EN | CTRL_ERR_BADWRITE));
    CHECK(wr(REG_CTRL, CTRL_EN, kAtomicClear) && rd(REG_CTRL) == CTRL_ERR_BADWRITE);
    CHECK(xip.raw_write_value() == CTRL_EN);
    // the FLUSH read an alias makes completes the flush
    wr(REG_FLUSH, 1);
    CHECK(!xip.flush_ready());
    CHECK(wr(REG_FLUSH, 0, kAtomicSet) && xip.flush_ready());
}

static void test_failures_stop_at_once() {
    fresh();
    env.fail_clk = true;
    CHECK(!wr(REG_FLUSH, 1));
    CHECK(!xip.reset());
    env.fail_clk = false;
    env.failed = 0;
    CHECK(xip.reset());
    env.fail_baudr = true;
    CHECK(!wr(REG_STREAM_CTR, 4) && !xip.stream_in_flight);
    env.fail_baudr = false;
    env.failed = 0;
    CHECK(wr(REG_STREAM_CTR, 4) && xip.stream_in_flight);
    env.fail_dreq = true;
    CHECK(!clk.tick(word_ns()));  // the alarm reports the parked failure and the clock stops
    env.fail_dreq = false;
    env.failed = 0;
    fresh();
    env.failed = 1;
    CHECK(!wr(REG_CTRL, CTRL_EN, kAtomicSet));  // a failure already pending: the write does not happen
    CHECK(rd(REG_CTRL) == (CTRL_EN | CTRL_ERR_BADWRITE));
}

static void test_the_windows() {
    fresh();
    const WindowHandler window = xip.window_handler(), aux = xip.aux_window_handler();
    flash[0] = 0x44;
    wr(REG_STREAM_ADDR, 0);
    wr(REG_STREAM_CTR, 2);
    clk.tick(2 * word_ns());
    CHECK(window.read32(window.ctx, REG_STREAM_CTR) == 0 && window.read32(window.ctx, REG_STAT) == xip.stat());
    window.write32(window.ctx, REG_CTRL, 0, kAtomicNormal);
    CHECK(rd(REG_CTRL) == 0);
    CHECK(aux.read32(aux.ctx, 0x123) == (0x44u | (static_cast<uint32_t>(flash[1]) << 8) | (static_cast<uint32_t>(flash[2]) << 16) | (static_cast<uint32_t>(flash[3]) << 24)));
    aux.write32(aux.ctx, 0, 5, kAtomicNormal);
    CHECK(xip.fifo_len == 1);  // XIP_AUX is read only
}

// --- the memory map ------------------------------------------------------------------------------------------------------

static void test_the_memory_map_hooks() {
    for (int fast = 0; fast < 2; ++fast) {
        fresh();
        static uint8_t image[0x1000];
        for (uint32_t i = 0; i < sizeof image; ++i) image[i] = static_cast<uint8_t>(i * 3 + 9);
        static uint8_t ram[0x100];
        MemoryMap map;
        map.set_fast_paths(fast != 0);
        const int flash_i = map.attach({0x10000000u, 0x04000000u, sizeof image, 0x00FFFFFFu, image, kSubWord | kXip});
        map.attach({0x20000000u, sizeof ram, sizeof ram, 0xFFFFFFFFu, ram, kSubWord});
        map.set_xip(xip.hooks());
        uint32_t v = 0;
        wr(REG_CTR_ACC, 0);
        wr(REG_CTR_HIT, 0);

        // reads: each access reported once, whatever the width; sub-word reads above the image / on other aliases are the caller's to compose
        CHECK(map.read32(0x10000010u, &v) == flash_i && v == (image[0x10] | image[0x11] << 8 | image[0x12] << 16 | static_cast<uint32_t>(image[0x13]) << 24));
        CHECK(rd(REG_CTR_ACC) == 1);
        CHECK(map.read16(0x10000010u, &v) == flash_i && rd(REG_CTR_ACC) == 2);
        CHECK(map.read8(0x10000011u, &v) == flash_i && v == image[0x11] && rd(REG_CTR_ACC) == 3);
        CHECK(map.read16(0x12000010u, &v) == kNotHandled && rd(REG_CTR_ACC) == 3);
        CHECK(map.read32(0x12000010u, &v) == flash_i && rd(REG_CTR_ACC) == 4);  // another alias, the same bytes
        CHECK(map.read32(0x13000FFCu, &v) == flash_i && rd(REG_CTR_ACC) == 5);
        CHECK(map.read32(0x10002000u, &v) == kNotHandled && rd(REG_CTR_ACC) == 5);  // past this short image: nothing to serve, nothing to count
        CHECK(map.read8(0x10002000u, &v) == kNotHandled && rd(REG_CTR_ACC) == 5);
        CHECK(rd(REG_CTR_HIT) == 2);  // 0x10, 0x10 and 0x11 are the same line; the NOCACHE and BYPASS reads are not looked up

        // writes of every width and alias are swallowed: the image never changes, and the hook sees each once
        const uint8_t before = image[0x20];
        CHECK(map.write32(0x10000020u, 0xFFFFFFFFu) == flash_i);
        CHECK(map.write16(0x10000020u, 0xFFFFu) == flash_i);
        CHECK(map.write8(0x10000020u, 0xFFu) == flash_i);
        CHECK(map.write32(0x11000020u, 0) == flash_i && map.write16(0x12000020u, 0) == flash_i && map.write8(0x13000020u, 0) == flash_i);
        CHECK(image[0x20] == before && image[0x21] == static_cast<uint8_t>(0x21 * 3 + 9));
        CHECK(env.warns == 3);  // the three writes to non-caching aliases fault
        CHECK(rd(REG_CTR_ACC) == 5);

        // the cache-as-SRAM segment: 32-bit accesses are served by the block, sub-word ones are the caller's
        CHECK(map.read32(0x15000000u, &v) == flash_i && v == 0xFFFFFFFFu);
        CHECK(map.write32(0x15000000u, 1) == flash_i);
        CHECK(map.read16(0x15000000u, &v) == kNotHandled && map.read8(0x15000000u, &v) == kNotHandled);
        CHECK(map.write16(0x15000000u, 1) == kNotHandled && map.write8(0x15000000u, 1) == kNotHandled);
        wr(REG_CTRL, 0);
        CHECK(map.write32(0x15000008u, 0x01020304u) == flash_i);
        CHECK(map.read32(0x15004008u, &v) == flash_i && v == 0x01020304u);
        CHECK(map.read32(0x14000000u, &v) == kNotHandled && map.read32(0x16000000u, &v) == kNotHandled);

        // RAM is untouched by all that
        CHECK(map.write32(0x20000004u, 0x55AA55AAu) != kNotHandled && map.read32(0x20000004u, &v) != kNotHandled && v == 0x55AA55AAu);
    }
}

static void test_the_map_without_hooks_is_plain_memory() {
    static uint8_t image[0x100];
    MemoryMap map;
    map.attach({0x10000000u, 0x04000000u, sizeof image, 0x00FFFFFFu, image, kSubWord | kXip});
    uint32_t v = 0;
    CHECK(map.write32(0x10000010u, 0x12345678u) != kNotHandled && image[0x10] == 0x78);  // no block, nothing to say it is read only
    CHECK(map.read32(0x10000010u, &v) != kNotHandled && v == 0x12345678u);
    CHECK(map.read32(0x15000000u, &v) == kNotHandled);
}

int main() {
    test_power_on_state();
    test_the_flush_clock();
    test_the_perfctr_sequence();
    test_ways_and_replacement();
    test_aliases();
    test_counters_saturate_and_clear();
    test_ctrl();
    test_the_cache_as_sram();
    test_the_stream();
    test_registers_through_the_window();
    test_failures_stop_at_once();
    test_the_windows();
    test_the_memory_map_hooks();
    test_the_map_without_hooks_is_plain_memory();
    if (failures == 0) std::printf("test_xip_ctrl: ok\n");
    return failures == 0 ? 0 : 1;
}
