// Standalone checks of src/rp2040py/native/core/dma.hpp (see tests/test_core_cpp.py for the flags).
//
// Directed tests of every behaviour of the reference (peripherals/_dma.py) that the lockstep differential
// (tests/test_dma_diff.py) also pins, on a real C++ Bus + Clock, so the header is exercised - and mutation-tested - without Cython.
#include <cstdio>
#include <cstring>

#include "bus.hpp"
#include "clock.hpp"
#include "dma.hpp"

using namespace rp2040core;
using namespace rp2040core::dma_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static const uint32_t kRam = 0x20000000, kProbe = 0x50400000, kDmaBase = 0x50000000;
static uint8_t ram[4096];

struct Env {
    // interrupt lines
    uint32_t irq_line[512];
    bool irq_level[512];
    int irqs = 0;
    int fail_irq_on_call = -1;
    // warnings
    uint32_t warn_kind[64], warn_offset[64];
    int warns = 0;
    // host clock
    double clk_sys = 125e6;
    int clk_calls = 0;
    // failure flag shared with the block
    int failed = 0;
    // the probe window
    uint32_t probe_writes[256][2];
    int probe_write_count = 0;
    uint32_t probe_reads = 0;
    int on_probe_write = 0;  // 0 nothing, 1 raise DREQ `probe_dreq`, 2 clear it, 3 rewrite channel 0's read address, 4 park a failure
    uint32_t probe_dreq = 0;
    uint32_t rewritten_read_addr = 0;
};

static Env env;
static Clock clk;
static Bus bus;
static DmaBlock dma;

static bool on_irq(void*, uint32_t line, bool level) {
    const int call = env.irqs;
    if (env.irqs < 512) {
        env.irq_line[env.irqs] = line;
        env.irq_level[env.irqs] = level;
        ++env.irqs;
    }
    if (call == env.fail_irq_on_call) {
        env.failed = 1;
        return false;
    }
    return true;
}
static double on_clk_sys(void*) {
    ++env.clk_calls;
    return env.clk_sys;
}
static void on_warn(void*, uint32_t kind, uint32_t offset, int64_t) {
    if (env.warns < 64) {
        env.warn_kind[env.warns] = kind;
        env.warn_offset[env.warns] = offset;
    }
    ++env.warns;
}

static uint32_t probe_read(void*, uint32_t) {
    return 0xA0000000u + ++env.probe_reads;
}
static void probe_write(void*, uint32_t offset, int64_t value, uint32_t) {
    if (env.probe_write_count < 256) {
        env.probe_writes[env.probe_write_count][0] = offset;
        env.probe_writes[env.probe_write_count][1] = static_cast<uint32_t>(value);
    }
    ++env.probe_write_count;
    switch (env.on_probe_write) {
        case 1: (void)dma.set_dreq(env.probe_dreq); break;
        case 2: dma.clear_dreq(env.probe_dreq); break;
        case 3: bus.write32(kDmaBase + READ_ADDR, env.rewritten_read_addr); break;
        case 4: env.failed = 1; break;
        case 5:
            env.on_probe_write = 0;  // once
            bus.write32(kDmaBase + AL1_CTRL, env.rewritten_read_addr);
            break;  // rewrite channel 0's CTRL (rewritten_read_addr carries the word)
        default: break;
    }
}

static void reset_env() {
    env.irqs = 0;
    env.warns = 0;
    env.clk_calls = 0;
    env.failed = 0;
    env.probe_write_count = 0;
    env.probe_reads = 0;
    env.on_probe_write = 0;
    env.fail_irq_on_call = -1;
    env.clk_sys = 125e6;
}

static uint32_t reg(uint32_t channel, uint32_t offset) { return dma.read(channel * 0x40 + offset); }
static void wr(uint32_t offset, int64_t value, uint32_t atomic = kAtomicNormal) {
    (void)dma.write_atomic(offset, value, atomic);
}
static void cwr(uint32_t channel, uint32_t offset, int64_t value) { wr(channel * 0x40 + offset, value); }

static uint32_t ctrl_word(uint32_t size_code, bool incr_read, bool incr_write, uint32_t treq, uint32_t chain_to, uint32_t ring_size = 0,
                          bool ring_on_write = false, bool quiet = false, bool bswap = false) {
    return EN | (size_code << 2) | (incr_read ? INCR_READ : 0) | (incr_write ? INCR_WRITE : 0) | (ring_size << 6) |
           (ring_on_write ? RING_SEL : 0) | (chain_to << 11) | (treq << 15) | (quiet ? IRQ_QUIET : 0) | (bswap ? BSWAP : 0);
}

// Programs a channel and triggers it through CTRL_TRIG.
static void run(uint32_t channel, uint32_t src, uint32_t dst, uint32_t count, uint32_t ctrl) {
    cwr(channel, READ_ADDR, src);
    cwr(channel, WRITE_ADDR, dst);
    cwr(channel, TRANS_COUNT, count);
    cwr(channel, CTRL_TRIG, ctrl);
}

static void settle() { (void)clk.tick(1e6); }

static void fresh() {
    dma.detach();
    reset_env();
    (void)dma.reset();
    wr(CHAN_ABORT, 0xFFF);  // a reset leaves BUSY alone (see below), and a BUSY channel ignores its next trigger
    for (int i = 0; i < DmaBlock::kChannels; ++i) {  // a reset leaves the addresses and the reload value alone
        cwr(i, READ_ADDR, 0);
        cwr(i, WRITE_ADDR, 0);
        cwr(i, TRANS_COUNT, 1);
    }
    for (uint32_t n = 0; n < 64; ++n) dma.clear_dreq(n);
    std::memset(ram, 0, sizeof ram);
    reset_env();
}

int main() {
    BusHost host;
    bus.init(host);
    bus.mem.attach({kRam, sizeof ram, sizeof ram, 0xFFFFFFFF, ram, kSubWord});
    bus.windows.attach(kProbe, {probe_read, probe_write, nullptr});
    DmaHost dma_host;
    dma_host.irq = on_irq;
    dma_host.clk_sys = on_clk_sys;
    dma_host.warn = on_warn;
    dma_host.failed = &env.failed;
    dma_host.lines[0] = 30;  // distinct from the usual numbers, to prove the mapping is used
    dma_host.lines[1] = 31;
    dma.init(&bus, &clk, dma_host);
    bus.windows.attach(kDmaBase, dma.window_handler());

    // ---- a plain 32-bit copy on PERMANENT: everything happens at the same instant --------------------------------------
    fresh();
    for (uint32_t i = 0; i < 4; ++i) bus.write32(kRam + 4 * i, 0x11111111u * (i + 1));
    wr(INTE0, 0x1);
    env.irqs = 0;
    run(0, kRam, kRam + 0x100, 4, ctrl_word(2, true, true, TREQ_PERMANENT, 0));
    CHECK(reg(0, CTRL_TRIG) & BUSY);
    CHECK(clk.has_alarm() && clk.nanos_to_next_alarm() == 0.0);
    CHECK(bus.read32(kRam + 0x100) == 0);  // nothing moved before the clock runs
    settle();
    for (uint32_t i = 0; i < 4; ++i) CHECK(bus.read32(kRam + 0x100 + 4 * i) == 0x11111111u * (i + 1));
    CHECK(reg(0, READ_ADDR) == kRam + 16 && reg(0, WRITE_ADDR) == kRam + 0x110 && reg(0, TRANS_COUNT) == 0);
    CHECK(!(reg(0, CTRL_TRIG) & BUSY) && dma.int_raw == 1 && !clk.has_alarm());
    CHECK(env.irqs == 2 && env.irq_line[0] == 30 && env.irq_level[0] && env.irq_line[1] == 31 && !env.irq_level[1]);
    CHECK(dma.read(INTS0) == 1 && dma.read(INTS1) == 0);

    // ---- data sizes, and the byte swaps of 16 and 32 bits ----------------------------------------------------------------
    fresh();
    bus.write32(kRam, 0x44332211u);
    run(1, kRam, kRam + 0x40, 4, ctrl_word(0, true, true, TREQ_PERMANENT, 1));
    settle();
    CHECK(bus.read32(kRam + 0x40) == 0x44332211u && reg(1, READ_ADDR) == kRam + 4);
    run(2, kRam, kRam + 0x40, 2, ctrl_word(1, true, true, TREQ_PERMANENT, 2));
    settle();
    CHECK(bus.read32(kRam + 0x40) == 0x44332211u && reg(2, WRITE_ADDR) == kRam + 0x44);
    run(3, kRam, kRam + 0x80, 1, ctrl_word(1, false, false, TREQ_PERMANENT, 3, 0, false, false, true));  // 16-bit swap
    settle();
    CHECK(bus.read16(kRam + 0x80) == 0x1122u && reg(3, READ_ADDR) == kRam && reg(3, WRITE_ADDR) == kRam + 0x80);  // fixed addresses
    run(4, kRam, kRam + 0x90, 1, ctrl_word(2, true, true, TREQ_PERMANENT, 4, 0, false, false, true));  // 32-bit swap
    settle();
    CHECK(bus.read32(kRam + 0x90) == 0x11223344u);
    run(5, kRam, kRam + 0xA0, 1, ctrl_word(0, true, true, TREQ_PERMANENT, 5, 0, false, false, true));  // BSWAP does nothing to bytes
    settle();
    CHECK(bus.read8(kRam + 0xA0) == 0x11u);
    run(6, kRam, kRam + 0xB0, 1, ctrl_word(3, true, true, TREQ_PERMANENT, 6));  // the reserved size is taken as bytes
    settle();
    CHECK(bus.read8(kRam + 0xB0) == 0x11u && reg(6, READ_ADDR) == kRam + 1);

    // ---- rings: the read side by default, the write side with RING_SEL -------------------------------------------------
    fresh();
    for (uint32_t i = 0; i < 16; ++i) bus.write8(kRam + 0x100 + i, 0xA0 + i);
    run(0, kRam + 0x104, kRam + 0x200, 12, ctrl_word(0, true, true, TREQ_PERMANENT, 0, 3));  // 8-byte ring, source starts mid-ring
    settle();
    CHECK(reg(0, READ_ADDR) == kRam + 0x100);                                                   // 4 + 12 bytes on: back at the ring's start
    CHECK(bus.read8(kRam + 0x200) == 0xA4 && bus.read8(kRam + 0x203) == 0xA7 && bus.read8(kRam + 0x204) == 0xA0 && bus.read8(kRam + 0x20B) == 0xA7);
    CHECK(reg(0, WRITE_ADDR) == kRam + 0x20C);                                                  // the write side runs free
    run(1, kRam + 0x100, kRam + 0x304, 12, ctrl_word(0, true, true, TREQ_PERMANENT, 1, 3, true));
    settle();
    CHECK(reg(1, WRITE_ADDR) == kRam + 0x300 && reg(1, READ_ADDR) == kRam + 0x10C);
    CHECK(bus.read8(kRam + 0x304) == 0xA8 && bus.read8(kRam + 0x300) == 0xA4 && bus.read8(kRam + 0x307) == 0xAB);

    // ---- chaining, and what is not a chain ----------------------------------------------------------------------------
    fresh();
    bus.write32(kRam, 0xCAFEF00Du);
    cwr(1, READ_ADDR, kRam);
    cwr(1, WRITE_ADDR, kRam + 0x40);
    cwr(1, TRANS_COUNT, 1);
    cwr(1, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 1));  // armed through an alias that does not trigger
    CHECK(!(reg(1, CTRL_TRIG) & BUSY));
    run(0, kRam, kRam + 0x20, 1, ctrl_word(2, true, true, TREQ_PERMANENT, 1));  // 0 chains to 1
    settle();
    CHECK(bus.read32(kRam + 0x20) == 0xCAFEF00Du && bus.read32(kRam + 0x40) == 0xCAFEF00Du);
    CHECK(dma.int_raw == 0x3);
    for (uint32_t missing = 12; missing <= 15; ++missing) {  // 12..15 do not exist
        fresh();
        run(0, kRam, kRam + 0x20, 1, ctrl_word(2, true, true, TREQ_PERMANENT, missing));
        settle();
        CHECK(dma.int_raw == 1 && !clk.has_alarm());
    }
    fresh();
    run(2, kRam, kRam + 0x20, 1, ctrl_word(2, true, true, TREQ_PERMANENT, 5));  // chained-to channel 5 is disabled: start() is a no-op
    settle();
    CHECK(dma.int_raw == 0x4 && !(reg(5, CTRL_TRIG) & BUSY));

    // ---- IRQ_QUIET and the null trigger -------------------------------------------------------------------------------
    fresh();
    run(0, kRam, kRam + 0x20, 1, ctrl_word(2, true, true, TREQ_PERMANENT, 0, 0, false, true));
    settle();
    CHECK(dma.int_raw == 0);
    cwr(0, CTRL_TRIG, ctrl_word(2, true, true, TREQ_PERMANENT, 0, 0, false, true));  // trigger value non-zero: starts again
    settle();
    CHECK(dma.int_raw == 0);
    env.irqs = 0;
    cwr(0, AL1_CTRL, IRQ_QUIET);
    wr(0 * 0x40 + AL3_READ_ADDR_TRIG, 0);  // a null trigger on a quiet channel raises the interrupt, and starts nothing
    CHECK(dma.int_raw == 1 && env.irqs == 2 && !clk.has_alarm());
    cwr(1, AL1_CTRL, 0);
    wr(1 * 0x40 + AL3_READ_ADDR_TRIG, 0);  // ... on a channel that is not quiet it does nothing at all
    CHECK(dma.int_raw == 1);

    // ---- DREQ pacing: a rising edge wakes, the level gates --------------------------------------------------------------
    // (the probe is the target and clears the DREQ on every write, so each rising edge buys exactly one transfer)
    fresh();
    for (uint32_t i = 0; i < 4; ++i) bus.write32(kRam + 4 * i, 0x100 + i);
    run(0, kRam, kProbe, 4, ctrl_word(2, true, false, 5, 0));
    CHECK((reg(0, CTRL_TRIG) & BUSY) && !clk.has_alarm());  // nothing asserted: it waits (and no pacing timer runs)
    CHECK(env.clk_calls == 0);                               // ... without asking the host for clk_sys
    env.on_probe_write = 2;
    env.probe_dreq = 5;
    CHECK(dma.set_dreq(5) && clk.has_alarm());
    (void)clk.tick(0);
    CHECK(reg(0, TRANS_COUNT) == 3 && env.probe_write_count == 1 && env.probe_writes[0][1] == 0x100);
    CHECK(!dma.dreq(5) && !clk.has_alarm());  // the transfer ran, found the DREQ gone and did not schedule another
    CHECK(dma.set_dreq(5) && clk.has_alarm());  // a rising edge wakes an active channel
    clk.cancel(&dma.channels[0].alarm);
    CHECK(dma.set_dreq(5) && !clk.has_alarm());  // the level is already high: no edge, no wake
    CHECK(dma.set_dreq(9) && !clk.has_alarm());  // another DREQ's edge does not wake it either
    dma.clear_dreq(5);
    CHECK(!dma.dreq(5) && dma.dreq(9) && dma.dreq_mask() == (1ull << 9));
    CHECK(dma.set_dreq(64) && dma.dreq_mask() == (1ull << 9));  // out of range: ignored
    env.on_probe_write = 0;
    CHECK(dma.set_dreq(5) && clk.has_alarm());
    settle();
    CHECK(reg(0, TRANS_COUNT) == 0 && env.probe_write_count == 4 && env.probe_writes[3][1] == 0x103);

    // ---- pacing timers --------------------------------------------------------------------------------------------------
    fresh();
    wr(TIMER0, (1u << 16) | 125u);  // X/Y = 1/125: one TREQ every 125 sys_clk cycles = 1.0 us at 125 MHz
    run(0, kRam, kRam + 0x40, 3, ctrl_word(2, true, true, TREQ_TIMER0, 0));
    CHECK(clk.has_alarm() && clk.nanos_to_next_alarm() == 1000.0 && env.clk_calls == 1);
    (void)clk.tick(999);
    CHECK(reg(0, TRANS_COUNT) == 3);
    (void)clk.tick(1);
    CHECK(reg(0, TRANS_COUNT) == 2 && clk.nanos_to_next_alarm() == 1000.0);
    env.clk_sys = 62.5e6;  // clk_sys is read again for every period
    settle();
    CHECK(reg(0, TRANS_COUNT) == 0);
    {
        bool ok;
        CHECK(dma.get_timer(TREQ_TIMER0, &ok) == 125.0 * 1e6 / 62.5e6 && ok);   // Y/X = 125 cycles
        CHECK(dma.get_timer(TREQ_TIMER1, &ok) == 0.0 && ok);  // a timer that was never programmed
        wr(TIMER1, (3u << 16) | 0u);
        CHECK(dma.get_timer(TREQ_TIMER1, &ok) == 0.0);  // a zero divisor disables it
        wr(TIMER1, (0u << 16) | 7u);
        CHECK(dma.get_timer(TREQ_TIMER1, &ok) == 0.0);  // so does a zero dividend (a rate of 0)
        wr(TIMER1, (3u << 16) | 2u);
        CHECK(dma.get_timer(TREQ_TIMER1, &ok) == (1.0 * 1e6) / 62.5e6);  // X/Y > 1: no faster than one TREQ per sys_clk, the permanent TREQ
        env.clk_calls = 0;
        CHECK(dma.get_timer(5, &ok) == 0.0 && env.clk_calls == 0);  // a DREQ number: zero, and the host is not asked
        // TIMER3 is laid out like the other three: X in 31:16 (it used to be read from bit 4)
        wr(TIMER3, (1u << 16) | 2000u);
        CHECK(dma.get_timer(TREQ_TIMER3, &ok) == 2000.0 * 1e6 / 62.5e6);
        wr(TIMER2, (125u << 4) | 0u);  // a value with nothing in bits 31:16: dividend 0, so disabled
        CHECK(dma.get_timer(TREQ_TIMER2, &ok) == 0.0);
        wr(TIMER3, (125u << 4) | 0u);
        CHECK(dma.get_timer(TREQ_TIMER3, &ok) == 0.0);
        CHECK(dma.get_timer(TREQ_PERMANENT, &ok) == (1.0 * 1e6) / 62.5e6);
    }
    CHECK(dma.read(TIMER0) == ((1u << 16) | 125u) && dma.read(TIMER3) == (125u << 4));
    CHECK(dma.read(MULTI_CHAN_TRIGGER) == 0 && dma.read(CHAN_ABORT) == 0 && dma.read(FIFO_LEVELS) == 0);   // self-clearing / debug: pico-sdk polls CHAN_ABORT until it reads 0
    // a host that cannot answer clk_sys: the failure surfaces, and nothing is scheduled
    fresh();
    wr(TIMER0, (1u << 16) | 1u);
    cwr(0, READ_ADDR, kRam);
    cwr(0, WRITE_ADDR, kRam + 0x40);
    cwr(0, TRANS_COUNT, 2);
    {
        // make the next host call fail by raising the flag from inside it
        struct Fail {
            static double clk(void*) {
                env.failed = 1;
                return 0.0;
            }
        };
        DmaHost failing = dma_host;
        failing.clk_sys = Fail::clk;
        DmaBlock other;
        Clock other_clock;
        other.init(&bus, &other_clock, failing);
        (void)other.write_atomic(TIMER0, (1u << 16) | 1u, kAtomicNormal);
        (void)other.write_atomic(READ_ADDR, kRam, kAtomicNormal);
        (void)other.write_atomic(TRANS_COUNT, 2, kAtomicNormal);
        CHECK(!other.write_atomic(CTRL_TRIG, ctrl_word(2, true, true, TREQ_TIMER0, 0), kAtomicNormal));
        CHECK(env.failed == 1 && !other_clock.has_alarm());
        other.detach();
        env.failed = 0;
    }

    // ---- abort, disable, and the registers' side effects --------------------------------------------------------------
    fresh();
    run(0, kRam, kRam + 0x40, 4, ctrl_word(2, true, true, TREQ_PERMANENT, 0));
    run(1, kRam, kRam + 0x50, 4, ctrl_word(2, true, true, TREQ_PERMANENT, 1));
    wr(CHAN_ABORT, 0x1);
    CHECK(!(reg(0, CTRL_TRIG) & BUSY) && (reg(1, CTRL_TRIG) & BUSY) && clk.has_alarm());
    cwr(1, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 1) & ~EN);  // disabling cancels the alarm of a running channel
    CHECK(!clk.has_alarm() && (reg(1, CTRL_TRIG) & BUSY));
    cwr(1, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 1));  // ... and enabling a BUSY channel schedules it again
    CHECK(clk.has_alarm());
    settle();
    CHECK(!(reg(1, CTRL_TRIG) & BUSY) && dma.int_raw == 0x2);
    // MULTI_CHAN_TRIGGER starts every channel in the mask; a channel that is not enabled ignores it
    fresh();
    cwr(2, READ_ADDR, kRam);
    cwr(2, WRITE_ADDR, kRam + 0x40);
    cwr(2, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 2));
    cwr(3, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 3) & ~EN);
    wr(MULTI_CHAN_TRIGGER, 0xC);
    CHECK((reg(2, CTRL_TRIG) & BUSY) && !(reg(3, CTRL_TRIG) & BUSY));
    // starting a channel that is already BUSY does not restart it
    fresh();
    run(0, kRam, kProbe, 5, ctrl_word(2, true, false, 5, 0));
    env.on_probe_write = 2;
    env.probe_dreq = 5;
    (void)dma.set_dreq(5);
    (void)clk.tick(0);
    CHECK(reg(0, TRANS_COUNT) == 4 && (reg(0, CTRL_TRIG) & BUSY));
    wr(MULTI_CHAN_TRIGGER, 1);
    CHECK(reg(0, TRANS_COUNT) == 4);
    env.on_probe_write = 0;

    // ---- a count of 0 starts nothing: not BUSY, no interrupt, no chain, nothing scheduled (as rp2040-emu) ---------------------
    fresh();
    cwr(7, TRANS_COUNT, 0);
    cwr(7, CTRL_TRIG, ctrl_word(2, true, true, TREQ_PERMANENT, 7));
    CHECK(!(reg(7, CTRL_TRIG) & BUSY) && !clk.has_alarm() && dma.int_raw == 0);
    cwr(7, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 7));  // a rewrite of CTRL does not wake it either
    settle();
    CHECK(!(reg(7, CTRL_TRIG) & BUSY) && reg(7, TRANS_COUNT) == 0 && dma.int_raw == 0);
    cwr(7, TRANS_COUNT, 1);  // ... and it starts normally once it has a count
    cwr(7, CTRL_TRIG, ctrl_word(2, true, true, TREQ_PERMANENT, 7));
    settle();
    CHECK(dma.int_raw == 0x80);
    fresh();  // a chain into a channel whose reload is 0 starts nothing; the chaining channel still finishes and interrupts
    cwr(7, TRANS_COUNT, 0);
    cwr(7, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 7));
    run(0, kRam, kRam + 0x40, 1, ctrl_word(2, true, true, TREQ_PERMANENT, 7));
    settle();
    CHECK(dma.int_raw == 0x1 && !(reg(7, CTRL_TRIG) & BUSY) && !clk.has_alarm());

    // ---- reset: BUSY survives, the DREQs survive, everything else goes ------------------------------------------------
    fresh();
    cwr(7, TRANS_COUNT, 3);
    cwr(7, CTRL_TRIG, ctrl_word(2, true, true, 5, 7));  // BUSY, waiting for a DREQ nobody raises
    CHECK(reg(7, CTRL_TRIG) & BUSY);
    (void)dma.set_dreq(11);
    wr(INTE0, 0xFF);
    wr(INTF1, 0x3);
    wr(TIMER2, 77);
    dma.int_raw = 0x40;
    env.irqs = 0;
    CHECK(dma.reset());
    CHECK((reg(7, CTRL_TRIG) & BUSY) && (reg(7, CTRL_TRIG) & 0xFFFFFF) == (7u << 11));
    CHECK(dma.dreq(11) && dma.int_raw == 0 && dma.read(INTE0) == 0 && dma.read(INTF1) == 0 && dma.read(TIMER2) == 0);
    CHECK(env.irqs == 2 && !env.irq_level[0] && !env.irq_level[1]);
    CHECK(reg(7, TRANS_COUNT) == 3 && reg(7, DBG_TCR) == 3);  // the live count and the reload value are left alone

    // ---- the interrupt registers ------------------------------------------------------------------------------------
    fresh();
    wr(INTE0, 0xFFFFFFFFll);
    wr(INTE1, 0x10001);
    wr(INTF0, 0x20003);
    CHECK(dma.read(INTE0) == 0xFFFF && dma.read(INTE1) == 0x1 && dma.read(INTF0) == 0x3);  // 16 bits each
    CHECK(dma.read(INTS0) == 0x3 && dma.read(INTS1) == 0);
    dma.int_raw = 0xF0F;
    wr(INTR, 0x0F, kAtomicNormal);  // write-1-to-clear, on the raw bits
    CHECK(dma.read(INTR) == 0xF00);
    wr(INTR, 0xF00, kAtomicClear);  // ... also through an alias (the raw value, not the decoded one)
    CHECK(dma.read(INTR) == 0);
    dma.int_raw = 0x6;
    wr(INTS1, 0x2);
    CHECK(dma.read(INTR) == 0x4);
    wr(INTE0, 0x3, kAtomicSet);
    wr(INTE0, 0x1, kAtomicXor);
    CHECK(dma.read(INTE0) == 0xFFFE);  // 0xFFFF | 3, then ^ 1
    // lines are re-announced, both, whatever changed
    env.irqs = 0;
    wr(INTE1, 0x4);
    CHECK(env.irqs == 2 && env.irq_line[0] == 30 && env.irq_line[1] == 31 && env.irq_level[0] && env.irq_level[1]);  // INTF0 and INTE1 & raw both hold
    // a failing line call stops the write
    env.fail_irq_on_call = 0;
    env.irqs = 0;
    CHECK(!dma.write_atomic(INTE1, 0x4, kAtomicNormal) && env.irqs == 1);
    env.failed = 0;
    env.fail_irq_on_call = -1;

    // ---- the register file's edges -------------------------------------------------------------------------------------
    fresh();
    CHECK(dma.read(N_CHANNELS) == 12);
    cwr(3, READ_ADDR, 0x1234);
    CHECK(reg(3, AL1_READ_ADDR) == 0x1234 && reg(3, AL2_READ_ADDR) == 0x1234 && reg(3, AL3_READ_ADDR_TRIG) == 0x1234);
    cwr(3, AL3_WRITE_ADDR, 0x5678);
    CHECK(reg(3, WRITE_ADDR) == 0x5678 && reg(3, AL1_WRITE_ADDR) == 0x5678 && reg(3, AL2_WRITE_ADDR_TRIG) == 0x5678);
    cwr(3, AL2_TRANS_COUNT, 9);
    CHECK(dma.read(3 * 0x40 + DBG_TCR) == 9 && dma.read(3 * 0x40 + DBG_CTDREQ) == 0);
    cwr(3, DBG_CTDREQ, 5);  // any write clears the counter
    CHECK(dma.read(3 * 0x40 + DBG_CTDREQ) == 0);
    CHECK(reg(3, 0x808) == 0);  // an unmapped channel offset reads as 0, quietly
    CHECK(env.warns == 0);
    // CTRL: the write-clear error bits, and BUSY is not writable
    cwr(3, CTRL_TRIG, BUSY | READ_ERROR | WRITE_ERROR);
    CHECK(!(reg(3, CTRL_TRIG) & BUSY) && !(reg(3, CTRL_TRIG) & (READ_ERROR | WRITE_ERROR)));
    // unimplemented registers warn; reads give all ones, with the second warning in the atomic area
    dma.read(0x438);
    CHECK(env.warns == 1 && env.warn_kind[0] == kDmaWarnRead && env.warn_offset[0] == 0x438);
    env.warns = 0;
    CHECK(dma.read(0x1438) == 0xFFFFFFFFu && env.warns == 2 && env.warn_kind[0] == kDmaWarnRead && env.warn_kind[1] == kDmaWarnReadAtomicArea);
    wr(0x434, 5);
    CHECK(env.warns == 3 && env.warn_kind[2] == kDmaWarnWrite && env.warn_offset[2] == 0x434);

    // ---- failures stop a transfer where the reference's exception would -------------------------------------------------
    fresh();
    bus.write32(kRam, 0x600DF00Du);
    env.on_probe_write = 4;  // a write to the probe parks a failure
    run(0, kRam, kProbe, 3, ctrl_word(2, true, false, TREQ_PERMANENT, 0));
    CHECK(!clk.tick(1e6));  // the tick stops at the failing alarm
    CHECK(env.failed == 1 && env.probe_write_count == 1);
    CHECK((reg(0, CTRL_TRIG) & BUSY) && reg(0, TRANS_COUNT) == 3 && reg(0, READ_ADDR) == kRam && dma.int_raw == 0);  // nothing advanced
    env.failed = 0;
    env.on_probe_write = 0;
    // the same stop on the read side is not reachable through memory, so check the bus parking from the probe's *read* instead:
    // a failed flag raised before the transfer ends it before any write
    fresh();
    env.failed = 1;  // as if a read had parked a failure
    run(0, kRam, kRam + 0x40, 2, ctrl_word(2, true, true, TREQ_PERMANENT, 0));
    CHECK(!clk.tick(1e6));
    CHECK(bus.read32(kRam + 0x40) == 0 && reg(0, TRANS_COUNT) == 2);  // nothing was written, nothing counted
    env.failed = 0;

    // ---- re-entrancy: the probe answers a write by changing a DREQ, or by rewriting the channel's own registers ---------
    fresh();
    bus.write32(kRam, 1);
    bus.write32(kRam + 4, 2);
    run(0, kRam, kProbe, 2, ctrl_word(2, true, false, TREQ_PERMANENT, 0));  // the source is RAM, the target the probe
    run(1, kRam + 0x40, kRam + 0x80, 1, ctrl_word(2, true, true, 12, 1));    // channel 1 waits for DREQ 12
    CHECK((reg(1, CTRL_TRIG) & BUSY) && clk.nanos_to_next_alarm() == 0.0);
    env.on_probe_write = 1;
    env.probe_dreq = 12;
    bus.write32(kRam + 0x40, 0x5EED);
    settle();  // channel 0's first transfer writes the probe, which raises DREQ 12 and wakes channel 1 *inside* the transfer
    CHECK(env.probe_write_count == 2 && dma.dreq(12));
    CHECK(reg(0, TRANS_COUNT) == 0 && !(reg(1, CTRL_TRIG) & BUSY) && dma.int_raw == 0x3);
    CHECK(bus.read32(kRam + 0x80) == 0x5EEDu);  // channel 1 could only have run because of the probe's DREQ
    fresh();
    bus.write32(kRam, 7);
    bus.write32(kRam + 0x100, 9);
    run(0, kRam, kProbe, 1, ctrl_word(2, true, false, TREQ_PERMANENT, 0));
    env.on_probe_write = 3;  // the probe rewrites channel 0's READ_ADDR while the transfer is still running
    env.rewritten_read_addr = kRam + 0x100;
    settle();
    CHECK(reg(0, READ_ADDR) == kRam + 0x104);  // the increment applies to what is there *now* (as in the reference)
    CHECK(env.probe_writes[0][1] == 7);
    env.on_probe_write = 0;

    // ---- the window the bus calls -------------------------------------------------------------------------------------
    fresh();
    bus.write32(kDmaBase + 0x3000 + READ_ADDR, 0x00F0);  // CLEAR alias on 0 reads current (0) & ~0xF0
    CHECK(bus.read32(kDmaBase + READ_ADDR) == 0);
    bus.write32(kDmaBase + READ_ADDR, 0x0F0F);
    bus.write32(kDmaBase + 0x2000 + READ_ADDR, 0xF000);  // SET
    bus.write32(kDmaBase + 0x1000 + READ_ADDR, 0x0001);  // XOR
    CHECK(bus.read32(kDmaBase + READ_ADDR) == 0xFF0Eu);
    CHECK(dma.raw_write_value() == 0x0001);

    // ---- every alias of a register reads the same, and only four of them trigger ---------------------------------------
    fresh();
    cwr(2, READ_ADDR, 0x2000AAAA);
    cwr(2, AL3_WRITE_ADDR, 0x2000BBBB);
    cwr(2, AL2_TRANS_COUNT, 21);
    cwr(2, AL2_CTRL, 0x00F0F0F0 & ~EN & ~BUSY);
    const uint32_t read_aliases[] = {READ_ADDR, AL1_READ_ADDR, AL2_READ_ADDR, AL3_READ_ADDR_TRIG};
    const uint32_t write_aliases[] = {WRITE_ADDR, AL1_WRITE_ADDR, AL2_WRITE_ADDR_TRIG, AL3_WRITE_ADDR};
    const uint32_t count_aliases[] = {TRANS_COUNT, AL1_TRANS_COUNT_TRIG, AL2_TRANS_COUNT, AL3_TRANS_COUNT};
    const uint32_t ctrl_aliases[] = {CTRL_TRIG, AL1_CTRL, AL2_CTRL, AL3_CTRL};
    for (int i = 0; i < 4; ++i) {
        CHECK(reg(2, read_aliases[i]) == 0x2000AAAAu);
        CHECK(reg(2, write_aliases[i]) == 0x2000BBBBu);
        CHECK(reg(2, ctrl_aliases[i]) == (0x00F0F0F0u & ~EN & ~BUSY));
        CHECK(reg(2, count_aliases[i]) == reg(2, TRANS_COUNT));  // the count register shows the live count ...
        CHECK(dma.read(2 * 0x40 + DBG_TCR) == 21);  // ... and the reload value is behind DBG_TCR
    }
    {
        const uint32_t triggers[] = {CTRL_TRIG, AL1_TRANS_COUNT_TRIG, AL2_WRITE_ADDR_TRIG, AL3_READ_ADDR_TRIG};
        const uint32_t quiet[] = {AL1_CTRL, AL1_READ_ADDR, AL1_WRITE_ADDR, AL2_CTRL, AL2_TRANS_COUNT, AL2_READ_ADDR, AL3_CTRL, AL3_WRITE_ADDR,
                                  AL3_TRANS_COUNT, READ_ADDR, WRITE_ADDR, TRANS_COUNT};
        for (uint32_t trigger : triggers) {
            fresh();
            cwr(4, AL1_CTRL, ctrl_word(2, true, true, 5, 4));  // armed, waiting for a DREQ nobody raises
            cwr(4, TRANS_COUNT, 3);
            cwr(4, trigger, trigger == CTRL_TRIG ? ctrl_word(2, true, true, 5, 4) : (trigger == AL1_TRANS_COUNT_TRIG ? 3 : kRam));
            CHECK(reg(4, CTRL_TRIG) & BUSY);
            CHECK(reg(4, TRANS_COUNT) == 3);
        }
        for (uint32_t offset : quiet) {
            fresh();
            cwr(4, AL1_CTRL, ctrl_word(2, true, true, 5, 4));
            cwr(4, TRANS_COUNT, 3);
            cwr(4, offset, offset == AL1_CTRL || offset == AL2_CTRL || offset == AL3_CTRL ? ctrl_word(2, true, true, 5, 4) : 3);
            CHECK(!(reg(4, CTRL_TRIG) & BUSY));
        }
    }

    // ---- a failure before any read of any size stops the transfer before its write ---------------------------------------
    for (uint32_t size_code = 0; size_code < 3; ++size_code) {
        fresh();
        bus.write32(kRam, 0x8BADF00Du);
        env.failed = 1;  // as if the read had parked a failure
        run(0, kRam, kRam + 0x40, 2, ctrl_word(size_code, true, true, TREQ_PERMANENT, 0));
        CHECK(!clk.tick(1e6));
        CHECK(bus.read32(kRam + 0x40) == 0 && reg(0, TRANS_COUNT) == 2 && reg(0, READ_ADDR) == kRam);
        env.failed = 0;
    }

    // ---- the control word and data size a transfer started with are the ones it advances by, whatever the probe does -------
    fresh();
    bus.write32(kRam, 0x11u);
    env.on_probe_write = 5;
    env.rewritten_read_addr = ctrl_word(0, false, false, TREQ_PERMANENT, 0);  // byte size, fixed addresses
    run(0, kRam, kProbe, 1, ctrl_word(2, true, false, TREQ_PERMANENT, 0));
    settle();  // (the CTRL rewrite while BUSY also re-armed the alarm: a second, stale transfer follows, as in the reference)
    CHECK(env.probe_write_count == 2 && reg(0, READ_ADDR) == kRam + 4);
    CHECK(reg(0, TRANS_COUNT) == 0xFFFFFFFFu);  // the stale transfer took the finished channel's count to -1 (the reference's quirk)

    // ---- chains reach channels 8..15's range (a 4-bit field), rings reach 32 KiB (a 4-bit field) -----------------------------
    fresh();
    bus.write32(kRam, 0xB16B00B5u);
    cwr(9, READ_ADDR, kRam);
    cwr(9, WRITE_ADDR, kRam + 0x40);
    cwr(9, TRANS_COUNT, 1);
    cwr(9, AL1_CTRL, ctrl_word(2, true, true, TREQ_PERMANENT, 9));
    run(0, kRam, kRam + 0x20, 1, ctrl_word(2, true, true, TREQ_PERMANENT, 9));
    settle();
    CHECK(bus.read32(kRam + 0x40) == 0xB16B00B5u && dma.int_raw == (1u | (1u << 9)));
    fresh();
    bus.write32(kRam + 0x3F8, 0xAAAA0001u);
    bus.write32(kRam + 0x3FC, 0xAAAA0002u);
    bus.write32(kRam + 0x200, 0xAAAA0003u);
    bus.write32(kRam + 0x204, 0xAAAA0004u);
    run(0, kRam + 0x3F8, kRam + 0x600, 4, ctrl_word(2, true, true, TREQ_PERMANENT, 0, 9));  // a 512-byte ring
    settle();
    CHECK(bus.read32(kRam + 0x600) == 0xAAAA0001u && bus.read32(kRam + 0x604) == 0xAAAA0002u && bus.read32(kRam + 0x608) == 0xAAAA0003u &&
          bus.read32(kRam + 0x60C) == 0xAAAA0004u && reg(0, READ_ADDR) == kRam + 0x208);

    // ---- a DREQ's edge wakes only channels that are running ---------------------------------------------------------------
    fresh();
    run(0, kRam, kRam + 0x40, 1, ctrl_word(2, true, true, 5, 0));  // waiting on DREQ 5 ...
    (void)dma.set_dreq(5);
    settle();  // ... runs and finishes (the DREQ stays high)
    CHECK(!(reg(0, CTRL_TRIG) & BUSY) && dma.int_raw == 1);
    dma.clear_dreq(5);
    dma.int_raw = 0;
    CHECK(dma.set_dreq(5) && !clk.has_alarm());  // enabled but idle: an edge does not start it
    CHECK(!dma.dreq(5 + 64) && !dma.dreq(69));    // numbers of 64 and more are never reported as asserted

    // ---- INTF1/INTS1, a CLEAR alias on a plain register, DBG writes leave the addresses alone --------------------------------
    fresh();
    wr(INTF1, 0x10003);
    CHECK(dma.read(INTF1) == 0x3 && dma.read(INTS1) == 0x3 && dma.read(INTS0) == 0);
    wr(INTE0, 0xFFFE);
    wr(INTE0, 0x3, kAtomicClear);
    CHECK(dma.read(INTE0) == 0xFFFC);
    cwr(3, READ_ADDR, 0x1111);
    cwr(3, DBG_CTDREQ, 5);
    CHECK(reg(3, READ_ADDR) == 0x1111 && reg(3, WRITE_ADDR) == 0 && dma.read(3 * 0x40 + DBG_CTDREQ) == 0);

    if (failures == 0) std::printf("test_dma: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
