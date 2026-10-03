// Standalone checks of src/rp2040py/native/core/timer.hpp (see tests/test_core_cpp.py for the flags).
#include <cstdio>

#include "timer.hpp"

using namespace rp2040core;
using namespace rp2040core::timer_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Recorder {
    static const int kMax = 256;
    uint32_t irq_line[kMax];
    bool irq_level[kMax];
    int irqs = 0;
    uint32_t warn_kind[kMax];
    uint32_t warn_offset[kMax];
    int64_t warn_value[kMax];
    int warns = 0;
    int fail_irq_on_call = -1;  // make the Nth irq call (0-based) report failure
};

static bool on_irq(void* ctx, uint32_t line, bool level) {
    Recorder* r = static_cast<Recorder*>(ctx);
    const int call = r->irqs;
    if (r->irqs < Recorder::kMax) {
        r->irq_line[r->irqs] = line;
        r->irq_level[r->irqs] = level;
        ++r->irqs;
    }
    return call != r->fail_irq_on_call;
}

static void on_warn(void* ctx, uint32_t kind, uint32_t offset, int64_t value) {
    Recorder* r = static_cast<Recorder*>(ctx);
    if (r->warns < Recorder::kMax) {
        r->warn_kind[r->warns] = kind;
        r->warn_offset[r->warns] = offset;
        r->warn_value[r->warns] = value;
        ++r->warns;
    }
}

static void clear(Recorder& r) {
    r.irqs = 0;
    r.warns = 0;
}

static uint32_t rd(TimerBlock& t, uint32_t offset) { return t.read(offset); }

int main() {
    Clock clock;
    Recorder rec;
    TimerBlock timer;
    TimerHost host;
    host.irq = on_irq;
    host.warn = on_warn;
    host.ctx = &rec;
    host.lines[0] = 10;  // distinct from the register index, to prove the mapping is used
    host.lines[1] = 11;
    host.lines[2] = 12;
    host.lines[3] = 13;
    timer.init(&clock, host);

    // The count is microseconds since the epoch; TIMELR latches the high word that TIMEHR then returns.
    CHECK(rd(timer, TIMELR) == 0 && rd(timer, TIMERAWL) == 0);
    clock.tick(5000);  // 5 us
    CHECK(rd(timer, TIMELR) == 5 && rd(timer, TIMERAWL) == 5 && rd(timer, TIMERAWH) == 0);
    clock.tick(4294967296.0 * 1000.0 * 2 + 7000);  // 2^33 + 7 us more
    CHECK(rd(timer, TIMEHR) == 0);                  // not latched yet
    CHECK(rd(timer, TIMELR) == 12 && rd(timer, TIMEHR) == 2 && rd(timer, TIMERAWH) == 2);
    clear(rec);

    // Arming an alarm: the due time is the written value (microseconds) minus now, scheduled on the clock.
    const uint32_t now_us = rd(timer, TIMERAWL);  // 12
    timer.write_atomic(ALARM1, now_us + 100, kAtomicNormal);
    CHECK(rd(timer, ARMED) == 0x2 && rd(timer, ALARM1) == now_us + 100 && clock.has_alarm());
    CHECK(clock.nanos_to_next_alarm() == 100.0 * 1000.0);
    CHECK(rec.irqs == 0 && rec.warns == 0);

    // INTE enables; every change re-announces all four lines, in order, with the configured line numbers.
    timer.write_atomic(INTE, 0xF, kAtomicNormal);
    CHECK(rec.irqs == 4);
    for (int i = 0; i < 4; ++i) CHECK(rec.irq_line[i] == 10u + i && !rec.irq_level[i]);
    clear(rec);

    // The alarm fires at its own time: disarmed, INTR bit set, all four lines announced, line 1 high.
    clock.tick(99000);
    CHECK(rec.irqs == 0 && rd(timer, ARMED) == 0x2);
    clock.tick(1000);
    CHECK(rd(timer, ARMED) == 0 && rd(timer, INTR) == 0x2 && rd(timer, INTS) == 0x2);
    CHECK(rec.irqs == 4 && rec.irq_line[0] == 10 && !rec.irq_level[0] && rec.irq_line[1] == 11 && rec.irq_level[1]);
    clear(rec);

    // INTR is write-1-to-clear (acts on the raw written bits), also through the CLEAR alias; INTF forces.
    timer.write_atomic(INTR, 0x2, kAtomicClear);
    CHECK(rd(timer, INTR) == 0 && rd(timer, INTS) == 0 && rec.irqs == 4);
    timer.write_atomic(INTF, 0x4, kAtomicNormal);
    CHECK(rd(timer, INTS) == 0x4 && rd(timer, INTF) == 0x4);
    timer.write_atomic(INTF, 0x1, kAtomicSet);  // alias: current | 1
    CHECK(rd(timer, INTF) == 0x5);
    timer.write_atomic(INTF, 0xFF0, kAtomicNormal);  // only the low four bits exist
    CHECK(rd(timer, INTF) == 0);
    clear(rec);

    // ARMED: writing 1s disarms those alarms (raw bits), cancelling their clock alarms.
    timer.write_atomic(ALARM0, rd(timer, TIMERAWL) + 50, kAtomicNormal);
    timer.write_atomic(ALARM3, rd(timer, TIMERAWL) + 60, kAtomicNormal);
    CHECK(rd(timer, ARMED) == 0x9);
    timer.write_atomic(ARMED, 0x1, kAtomicNormal);
    CHECK(rd(timer, ARMED) == 0x8 && clock.has_alarm());
    timer.write_atomic(ARMED, 0xFF, kAtomicNormal);
    CHECK(rd(timer, ARMED) == 0 && !clock.has_alarm());

    // Re-arming an armed alarm moves it instead of duplicating it.
    timer.write_atomic(ALARM2, rd(timer, TIMERAWL) + 500, kAtomicNormal);
    timer.write_atomic(ALARM2, rd(timer, TIMERAWL) + 20, kAtomicNormal);
    CHECK(clock.nanos_to_next_alarm() == 20000.0);
    clock.tick(20000);
    CHECK(rd(timer, INTR) == 0x4 && !clock.has_alarm());
    timer.write_atomic(INTR, 0xF, kAtomicNormal);
    clear(rec);

    // PAUSE is not implemented: it stores the bit and warns when set.
    timer.write_atomic(PAUSE, 1, kAtomicNormal);
    CHECK(rd(timer, PAUSE) == 1 && rec.warns == 1 && rec.warn_kind[0] == kTimerWarnPause);
    timer.write_atomic(PAUSE, 0, kAtomicNormal);
    CHECK(rd(timer, PAUSE) == 0 && rec.warns == 1);
    clear(rec);

    // Unimplemented registers read as 0xFFFFFFFF and warn - twice in the atomic-alias area - and writes warn too.
    CHECK(rd(timer, 0x04) == 0xFFFFFFFFu);
    CHECK(rec.warns == 1 && rec.warn_kind[0] == kTimerWarnRead && rec.warn_offset[0] == 0x04);
    clear(rec);
    CHECK(rd(timer, 0x2004) == 0xFFFFFFFFu);
    CHECK(rec.warns == 2 && rec.warn_kind[1] == kTimerWarnReadAtomicArea);
    clear(rec);
    timer.write_atomic(0x00, -2, kAtomicNormal);
    CHECK(rec.warns == 1 && rec.warn_kind[0] == kTimerWarnWrite && rec.warn_offset[0] == 0 && rec.warn_value[0] == -2);
    clear(rec);

    // An alias write decodes against a read of the register - so a read side effect (TIMELR's latch) happens.
    clock.tick(4294967296.0 * 1000.0 * 3);  // the high word moves on, but TIMEHR only changes when TIMELR is read
    const uint32_t high_before = rd(timer, TIMEHR);
    timer.write_atomic(TIMELR, 0, kAtomicSet);  // not writable, but its alias decode still reads TIMELR first
    CHECK(rd(timer, TIMEHR) != high_before);
    clear(rec);

    // Reset: registers back to power-on, alarms cancelled, all four lines announced low, and the count restarts.
    timer.write_atomic(ALARM0, rd(timer, TIMERAWL) + 5000, kAtomicNormal);
    timer.write_atomic(INTF, 0x3, kAtomicNormal);
    clear(rec);
    CHECK(timer.reset());
    CHECK(rd(timer, ARMED) == 0 && rd(timer, INTF) == 0 && rd(timer, INTE) == 0 && !clock.has_alarm());
    CHECK(rec.irqs == 4 && rec.irq_line[0] == 10 && !rec.irq_level[0] && !rec.irq_level[3]);
    CHECK(rd(timer, TIMERAWL) == 0 && rd(timer, TIMERAWH) == 0 && rd(timer, ALARM0) == 0);

    // A failing interrupt-line call stops the work at once (as an exception would) and leaves the rest undone.
    clear(rec);
    rec.fail_irq_on_call = 1;  // the second call of the next announcement fails
    timer.write_atomic(INTE, 0x1, kAtomicNormal);
    CHECK(rec.irqs == 2);       // the third and fourth lines were never announced
    CHECK(rd(timer, INTE) == 1);  // the register itself was already updated
    rec.fail_irq_on_call = -1;
    timer.detach();
    CHECK(!clock.has_alarm());

    // The window entry points are the same protocol: a handler built from the block serves the bus's reads and writes.
    WindowHandler h = timer.window_handler();
    clock.tick(1000);
    CHECK(h.read32(h.ctx, TIMERAWL) == rd(timer, TIMERAWL));
    h.write32(h.ctx, INTE, 0x2, kAtomicNormal);
    CHECK(rd(timer, INTE) == 0x2);

    std::printf(failures ? "FAILED: %d\n" : "timer: all checks passed\n", failures);
    return failures ? 1 : 0;
}
