// Standalone checks of src/rp2040py/native/core/clock.hpp (see tests/test_core_cpp.py for the flags).
#include <cmath>
#include <cstdio>

#include "clock.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Probe {
    Clock* clock = nullptr;
    Alarm alarm;
    int id = 0;
    int fired = 0;
    double fired_at = -1;
    // what the callback does
    double reschedule_in = -1;  // >= 0: re-arm itself ...
    int reschedule_times = 0;   // ... this many times
    Alarm* cancel_other = nullptr;
    Alarm* arm_other = nullptr;
    double arm_other_in = 0;
    bool fail = false;
};

static int order[64];
static int order_count = 0;

static bool on_fire(void* ctx) {
    Probe* p = static_cast<Probe*>(ctx);
    ++p->fired;
    p->fired_at = p->clock->nanos();
    if (order_count < 64) order[order_count++] = p->id;
    if (p->reschedule_in >= 0 && p->fired <= p->reschedule_times) p->clock->schedule(&p->alarm, p->reschedule_in);
    if (p->cancel_other) p->clock->cancel(p->cancel_other);
    if (p->arm_other) p->clock->schedule(p->arm_other, p->arm_other_in);
    return !p->fail;
}

static void init(Probe& p, Clock& c, int id) {
    p.clock = &c;
    p.id = id;
    p.alarm.fire = on_fire;
    p.alarm.ctx = &p;
}

// ---- deferred time (`defer_ticks`) against ticking after every step -----------------------------------------------------------------------------------------------------------------------------
// The batch loop of a CPU running alone defers the time and the clock settles it when somebody looks or an alarm is due. The contract is that this is invisible: the same operations on an eager clock
// (`tick_ticks` after every step) and on a lazy one (`defer_ticks`) must give the same alarms, fired in the same order each at the same tick, the same time at every read, the same verdict at every step
// (a callback that fails stops the clock at its own time on both) - including callbacks that read the time, re-arm themselves and cancel or arm others while the clock is settling.
constexpr int kLazyAlarms = 6;
constexpr int kLazyLog = 6000;

struct LazySim;
struct LazyNode {
    LazySim* sim = nullptr;
    Alarm alarm;
    int id = 0;
    int fired = 0;
};
struct LazySim {
    Clock clock;
    LazyNode node[kLazyAlarms];
    int log_id[kLazyLog];
    int64_t log_at[kLazyLog];
    int log_n = 0;
    int fail_at = -1;  // the n-th firing of alarm 3 fails (-1: never)
};

static bool lazy_fire(void* ctx) {
    LazyNode* n = static_cast<LazyNode*>(ctx);
    LazySim* s = n->sim;
    ++n->fired;
    if (s->log_n < kLazyLog) {
        s->log_id[s->log_n] = n->id;
        s->log_at[s->log_n] = s->clock.ticks();  // reads the time from inside a callback, while the clock is settling
        ++s->log_n;
    }
    const uint32_t h = static_cast<uint32_t>(n->id + 1) * 2654435761u + static_cast<uint32_t>(n->fired) * 40503u;
    if ((h >> 4) % 3 == 0) s->clock.schedule_ticks(&n->alarm, 1 + static_cast<int64_t>((h >> 8) % 3000));
    if ((h >> 6) % 7 == 0) s->clock.cancel(&s->node[(h >> 10) % kLazyAlarms].alarm);
    if ((h >> 9) % 11 == 0) s->clock.schedule_ticks(&s->node[(h >> 13) % kLazyAlarms].alarm, 1 + static_cast<int64_t>((h >> 16) % 2000));
    if (s->fail_at >= 0 && n->id == 3 && n->fired == s->fail_at) return false;
    return true;
}

static void lazy_setup(LazySim& s, int fail_at) {
    s.clock.reset();
    s.log_n = 0;
    s.fail_at = fail_at;
    for (int i = 0; i < kLazyAlarms; ++i) {
        s.node[i].sim = &s;
        s.node[i].id = i;
        s.node[i].fired = 0;
        s.node[i].alarm = Alarm();
        s.node[i].alarm.fire = &lazy_fire;
        s.node[i].alarm.ctx = &s.node[i];
    }
}

static uint32_t lazy_rng(uint32_t& state) {
    state = state * 1664525u + 1013904223u;
    return state >> 8;
}

static void check_deferred_time_matches_eager() {
    static LazySim eager, lazy;
    for (uint32_t seed = 1; seed <= 400; ++seed) {
        const int fail_at = seed % 5 == 0 ? static_cast<int>(2 + seed % 9) : -1;
        lazy_setup(eager, fail_at);
        lazy_setup(lazy, fail_at);
        uint32_t rng = seed;
        bool stopped = false;
        for (int step = 0; step < 4000 && !stopped; ++step) {
            const uint32_t r = lazy_rng(rng);
            const uint32_t op = r % 10;
            if (op <= 5) {  // let some time pass: 1 .. ~3000 ticks (an instruction is 128 at 125 MHz; sometimes a longer jump)
                const int64_t d = 1 + static_cast<int64_t>(lazy_rng(rng) % (op == 5 ? 20000u : 400u));
                const bool a = eager.clock.tick_ticks(d);
                const bool b = lazy.clock.defer_ticks(d);
                if (a != b) { CHECK(a == b); stopped = true; }
                if (!a) stopped = true;
            } else if (op == 6) {  // look at the time; each reader first, in turn (a reader that did not settle would otherwise be settled by the one before it)
                if (lazy_rng(rng) % 2 == 0) {
                    CHECK(eager.clock.nanos() == lazy.clock.nanos());
                    CHECK(eager.clock.ticks() == lazy.clock.ticks());
                } else {
                    CHECK(eager.clock.ticks() == lazy.clock.ticks());
                    CHECK(eager.clock.nanos() == lazy.clock.nanos());
                }
            } else if (op == 7) {  // arm an alarm
                const int i = static_cast<int>(lazy_rng(rng) % kLazyAlarms);
                const int64_t d = 1 + static_cast<int64_t>(lazy_rng(rng) % 5000u);
                eager.clock.schedule_ticks(&eager.node[i].alarm, d);
                lazy.clock.schedule_ticks(&lazy.node[i].alarm, d);
            } else if (op == 8) {  // cancel one
                const int i = static_cast<int>(lazy_rng(rng) % kLazyAlarms);
                eager.clock.cancel(&eager.node[i].alarm);
                lazy.clock.cancel(&lazy.node[i].alarm);
            } else {  // ask about the next alarm
                CHECK(eager.clock.has_alarm() == lazy.clock.has_alarm());
                CHECK(eager.clock.ticks_to_next_alarm() == lazy.clock.ticks_to_next_alarm());
            }
        }
        CHECK(eager.clock.ticks() == lazy.clock.ticks());
        CHECK(lazy.clock.pending_ticks() == 0);
        CHECK(eager.log_n == lazy.log_n);
        for (int i = 0; i < eager.log_n && i < lazy.log_n; ++i) {
            if (eager.log_id[i] != lazy.log_id[i] || eager.log_at[i] != lazy.log_at[i]) {
                std::printf("seed %u: firing %d differs: eager alarm %d at %lld, lazy alarm %d at %lld\n", seed, i, eager.log_id[i], static_cast<long long>(eager.log_at[i]),
                            lazy.log_id[i], static_cast<long long>(lazy.log_at[i]));
                CHECK(false);
                break;
            }
        }
    }
}

int main() {
    Clock c;
    CHECK(c.frequency == 125e6 && c.nanos() == 0 && !c.has_alarm() && c.nanos_to_next_alarm() == 0);
    CHECK(c.tick(100) && c.nanos() == 100);  // no alarms: just time

    // Alarms fire in due-time order, each at its own time, and the clock ends at the target.
    Probe a, b, d;
    init(a, c, 1); init(b, c, 2); init(d, c, 3);
    c.schedule(&b.alarm, 50);   // due 150
    c.schedule(&a.alarm, 20);   // due 120
    c.schedule(&d.alarm, 500);  // due 600
    CHECK(c.has_alarm() && c.nanos_to_next_alarm() == 20);
    order_count = 0;
    CHECK(c.tick(60));
    CHECK(order_count == 2 && order[0] == 1 && order[1] == 2);
    CHECK(a.fired_at == 120 && b.fired_at == 150 && c.nanos() == 160);
    CHECK(!a.alarm.scheduled && !b.alarm.scheduled && d.alarm.scheduled);
    CHECK(c.nanos_to_next_alarm() == 440);

    // The same due time fires in the order the alarms were scheduled (FIFO), whichever way they were queued.
    Probe e, f, g;
    init(e, c, 4); init(f, c, 5); init(g, c, 6);
    c.schedule(&e.alarm, 10);
    c.schedule(&f.alarm, 10);
    c.schedule(&g.alarm, 10);
    order_count = 0;
    c.tick(10);
    CHECK(order_count == 3 && order[0] == 4 && order[1] == 5 && order[2] == 6);

    // A zero-delay producer that re-arms itself must not cut in front of a consumer already pending: the
    // consumer was linked first, so it fires between the producer's turns instead of being starved.
    Probe producer, consumer;
    init(producer, c, 7); init(consumer, c, 8);
    producer.reschedule_in = 0;
    producer.reschedule_times = 3;  // fires 4 times in all, then stops re-arming
    c.schedule(&producer.alarm, 0);
    c.schedule(&consumer.alarm, 0);
    order_count = 0;
    c.tick(0);
    CHECK(order_count == 5 && order[0] == 7 && order[1] == 8 && order[2] == 7 && order[3] == 7 && order[4] == 7);
    CHECK(producer.fired == 4 && consumer.fired == 1);

    // schedule() on a linked alarm moves it, cancel() removes it (and is safe on one that is not linked).
    Probe m;
    init(m, c, 9);
    c.schedule(&m.alarm, 100);
    c.schedule(&m.alarm, 300);
    c.tick(150);
    CHECK(m.fired == 0);
    c.tick(150);
    CHECK(m.fired == 1);
    c.schedule(&m.alarm, 10);
    c.cancel(&m.alarm);
    c.cancel(&m.alarm);
    c.tick(100);
    CHECK(m.fired == 1 && !m.alarm.scheduled);

    // The clock may be destroyed before its alarms (a garbage collector frees a cycle in no particular order): the alarms are
    // released, and cancel_alarm() on them is then a no-op. The other order - the alarm goes first - unlinks it from a live clock.
    {
        Probe survivor;
        {
            Clock dying;
            init(survivor, dying, 40);
            dying.schedule(&survivor.alarm, 10);
            CHECK(survivor.alarm.scheduled && survivor.alarm.owner == &dying);
        }
        CHECK(!survivor.alarm.scheduled && survivor.alarm.owner == nullptr && survivor.alarm.next == nullptr);
        cancel_alarm(&survivor.alarm);
        CHECK(!survivor.alarm.scheduled);

        Clock alive;
        Probe first, last;
        init(first, alive, 41); init(last, alive, 42);
        alive.schedule(&first.alarm, 10);
        alive.schedule(&last.alarm, 20);
        cancel_alarm(&first.alarm);
        CHECK(!first.alarm.scheduled && first.alarm.owner == nullptr && alive.nanos_to_next_alarm() == 20);
        alive.tick(30);
        CHECK(first.fired == 0 && last.fired == 1);
    }

    // A callback may cancel another alarm, and arm one that is due inside the same tick.
    Clock c2;
    Probe x, y, z;
    init(x, c2, 10); init(y, c2, 11); init(z, c2, 12);
    x.cancel_other = &y.alarm;
    z.arm_other = &x.alarm;
    z.arm_other_in = 5;
    c2.schedule(&x.alarm, 10);
    c2.schedule(&y.alarm, 20);  // cancelled by x before it is due
    c2.schedule(&z.alarm, 30);  // arms x for time 35, inside the tick below
    order_count = 0;
    c2.tick(100);
    CHECK(y.fired == 0);
    CHECK(order_count == 3 && order[0] == 10 && order[1] == 12 && order[2] == 10);
    CHECK(x.fired == 2 && x.fired_at == 35 && c2.nanos() == 100);

    // A failing callback stops the tick: the clock stays at that alarm's time, later alarms stay linked.
    Clock c3;
    Probe ok1, bad, ok2;
    init(ok1, c3, 13); init(bad, c3, 14); init(ok2, c3, 15);
    bad.fail = true;
    c3.schedule(&ok1.alarm, 10);
    c3.schedule(&bad.alarm, 20);
    c3.schedule(&ok2.alarm, 30);
    CHECK(!c3.tick(100));
    CHECK(ok1.fired == 1 && bad.fired == 1 && ok2.fired == 0);
    CHECK(c3.nanos() == 20 && ok2.alarm.scheduled && c3.nanos_to_next_alarm() == 10);
    bad.fail = false;
    CHECK(c3.tick(100) && ok2.fired == 1 && c3.nanos() == 120);

    // ---- the integer time base (docs/records/0100) ----------------------------------------------------------------------
    {
        Clock t;
        CHECK(kTicksPerNano == 16 && t.ticks() == 0);
        CHECK(nanos_to_ticks(8.0) == 128 && nanos_to_ticks(1.0 / 32) == 1 && nanos_to_ticks(1.0 / 32 - 1e-3) == 1 && nanos_to_ticks(0.0) == 0 && nanos_to_ticks(-1.0 / 32) == 0 && nanos_to_ticks(-0.1) == -2);  // round half up
        CHECK(nanos_to_ticks(1e30) == kMaxTicks && nanos_to_ticks(-1e30) == -kMaxTicks && nanos_to_ticks(std::nan("")) == 0);
        // past 2^53 ns, where a double of nanoseconds could not hold now + 8: an 8 ns step still resolves, tick by tick
        CHECK(t.tick_ticks((int64_t{1} << 53) * 16 + 7));
        const int64_t start = t.ticks();
        Probe p1, p2;
        init(p1, t, 21); init(p2, t, 22);
        t.schedule(&p1.alarm, 8.0);
        t.schedule(&p2.alarm, 8.0);
        order_count = 0;
        CHECK(t.tick(8.0) && p1.fired == 1 && p2.fired == 1 && t.ticks() == start + 128 && order[0] == 21 && order[1] == 22);
        CHECK(t.tick_ticks(1) && t.ticks() == start + 129);
        // 2.5 years of simulated time is nowhere near the end of the range
        Clock y;
        CHECK(y.tick_ticks(static_cast<int64_t>(7.8e16) * 16) && !y.saturated());
        const int64_t before = y.ticks();
        CHECK(y.tick(8.0) && y.ticks() == before + 128);
        // the end of the range saturates instead of wrapping, and says so
        Clock z;
        Probe never;
        init(never, z, 23);
        z.schedule(&never.alarm, 1e30);
        CHECK(z.saturated() && z.ticks_to_next_alarm() == kMaxTicks);
        CHECK(z.tick_ticks(kMaxTicks) && never.fired == 1 && z.rebased_ticks() > 0.0);  // the end of the range is reached and the origin moves on
        CHECK(z.tick_ticks(kMaxTicks) && z.ticks() < kRebaseAt);
        z.reset();
        CHECK(!z.saturated() && z.ticks() == 0);
        // a negative delta moves time back, as the double clock allowed
        Clock n;
        CHECK(n.tick_ticks(100) && n.tick_ticks(-40) && n.ticks() == 60);
        // a period that is not a whole number of ticks: the remainder is carried, however the cycles are split
        const double tpc = 1e9 / 133e6 * 16;
        Clock one, chunks;
        for (int i = 0; i < 1000000; ++i) CHECK(one.advance_cycles(1, tpc));
        for (int i = 0; i < 10000; ++i) CHECK(chunks.advance_cycles(100, tpc));
        const double exact = 1e6 * tpc;
        CHECK(std::fabs(static_cast<double>(one.ticks()) - exact) <= 1.0 && std::fabs(static_cast<double>(chunks.ticks()) - exact) <= 1.0);
        Clock c125;
        for (int i = 0; i < 1000; ++i) CHECK(c125.advance_cycles(3, 128.0));
        CHECK(c125.ticks() == 384000 && c125.nanos() == 24000.0);
    }

    std::printf(failures ? "FAILED: %d\n" : "clock: all checks passed\n", failures);
    check_deferred_time_matches_eager();
    return failures ? 1 : 0;
}
