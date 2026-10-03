// Standalone checks of src/rp2040py/native/core/clock.hpp (see tests/test_core_cpp.py for the flags).
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

    std::printf(failures ? "FAILED: %d\n" : "clock: all checks passed\n", failures);
    return failures ? 1 : 0;
}
