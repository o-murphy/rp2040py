// Standalone checks of src/rp2040py/native/core/timer32.hpp (see tests/test_core_cpp.py for the flags).
//
// Every expectation below was produced by the pure-Python reference (utils/timer32.py, on the pure SimulationClock) by running the same scenario, so these checks pin the translation to the
// reference rather than to a second reading of it: the counter in each of the three modes, the prescaler and frequency changes, the quirks (a prescaler write that switches a stopped timer on,
// a stopped counter that is not reduced modulo TOP, ZIGZAG with TOP == 0), the alarm schedule (the sitting-on-the-target guard, a target above TOP, the zigzag and decrement arithmetic), the
// order the timer tells its alarms in, and a failing callback. The end-to-end proof is tests/test_pwm_diff.py, which drives the whole PWM on top of it.
#include <cstdio>

#include "timer32.hpp"

using namespace rp2040core;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static const double kFreq = 1e6;  // one tick per microsecond

struct Rig {
    Clock clk;
    Timer32 t;
    explicit Rig(TimerMode mode = TimerMode::kIncrement, int64_t top = Timer32::kNoTop, double freq = kFreq) {
        t.init(&clk, freq);
        t.set_mode(mode);
        t.set_top(top);
    }
    void us(int n) { CHECK(clk.tick(n * 1000.0)); }
};

struct Fires {
    double at[16];
    int n = 0;
    Rig* rig = nullptr;
    bool fail_on_first = false;
};

static bool on_fire(void* ctx) {
    Fires* f = static_cast<Fires*>(ctx);
    if (f->n < 16) f->at[f->n] = f->rig->clk.nanos();
    ++f->n;
    return !f->fail_on_first;
}

static void check_sequence(Rig& r, const uint32_t* expected, int count) {
    for (int i = 0; i < count; ++i) {
        CHECK(r.t.counter() == expected[i]);
        r.us(1);
    }
}

static void test_the_counter() {
    {
        Rig r(TimerMode::kIncrement, 9);
        static const uint32_t seq[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1};
        check_sequence(r, seq, 12);
    }
    {
        Rig r(TimerMode::kZigzag, 4);
        static const uint32_t seq[] = {0, 1, 2, 3, 4, 3, 2, 1, 0, 1};
        check_sequence(r, seq, 10);
    }
    {
        Rig r(TimerMode::kDecrement, 4);
        static const uint32_t seq[] = {0, 4, 3, 2, 1, 0, 4, 3};
        check_sequence(r, seq, 8);
    }
    static const TimerMode kModes[] = {TimerMode::kIncrement, TimerMode::kDecrement, TimerMode::kZigzag};
    for (TimerMode mode : kModes) {  // TOP == 0 never divides by zero
        Rig r(mode, 0);
        for (int i = 0; i < 5; ++i) {
            CHECK(r.t.counter() == 0);
            CHECK(r.t.raw_counter() == 0);
            r.us(7);
        }
    }
    {  // TOP set to zero on a running zigzag counter
        Rig r(TimerMode::kZigzag, 50);
        r.us(20);
        r.t.set_top(0);
        CHECK(r.t.counter() == 0);
    }
    {  // a fractional prescaler: 2.5 clock ticks per count
        Rig r;
        r.t.set_prescaler(2.5);
        static const uint32_t seq[] = {0, 0, 1, 1, 2, 2, 2, 3};
        check_sequence(r, seq, 8);
    }
}

static void test_set_advance_and_the_stopped_counter() {
    {
        Rig r(TimerMode::kIncrement, 9);
        r.t.set(7);
        r.us(5);
        CHECK(r.t.counter() == 2);
        r.t.advance(-9);  // advance moves the base value; the modulo applies when the counter runs
        CHECK(r.t.raw_counter() == 3 && r.t.counter() == 3);
    }
    {  // a stopped counter holds its value and resumes from it
        Rig r(TimerMode::kIncrement, 9);
        r.us(4);
        r.t.set_enable(false);
        CHECK(r.t.counter() == 4);
        r.us(100);
        CHECK(r.t.counter() == 4 && r.t.raw_counter() == 4);
        r.t.set_enable(true);
        r.us(3);
        CHECK(r.t.counter() == 7);
    }
    {  // the prescaler write sets `enabled` from the OLD prescaler: it can switch a stopped timer on
        Rig r(TimerMode::kIncrement, 9);
        r.t.set_enable(false);
        r.t.set_prescaler(2);
        CHECK(r.t.enable());
    }
    {  // ... and a zero prescaler stops it (and the next write, from the zero, leaves it stopped)
        Rig r;
        r.t.set_prescaler(0);
        CHECK(r.t.enable());
        r.t.set_prescaler(3);
        CHECK(!r.t.enable());
    }
    {  // a stopped counter is not reduced modulo TOP, so it can sit outside 0..TOP, and a negative one reads as its two's complement
        Rig r(TimerMode::kIncrement, 9);
        r.t.set_enable(false);
        r.t.advance(-3);
        CHECK(r.t.counter() == 7u && r.t.raw_counter() == 7);  // advance() keeps the base value in range when TOP is set
        Rig q;
        q.t.set_enable(false);
        q.t.advance(-3);
        CHECK(q.t.counter() == 4294967293u && q.t.raw_counter() == -3);
    }
    {  // a frequency change keeps the counter and continues at the new rate
        Rig r(TimerMode::kIncrement, 99);
        r.us(10);
        r.t.set_frequency(2e6);
        r.us(10);
        CHECK(r.t.counter() == 30);
    }
    {  // TOP below the counter resets it, TOP above keeps it
        Rig r;
        r.us(50);
        r.t.set_top(20);
        CHECK(r.t.counter() == 0);
        Rig q;
        q.us(5);
        q.t.set_top(20);
        CHECK(q.t.counter() == 5);
    }
    {  // set() with zig_zag_down counts from the far side of the fold
        Rig r(TimerMode::kZigzag, 10);
        r.t.set(3, true);
        CHECK(r.t.raw_counter() == 17 && r.t.counter() == 3);
        r.us(2);
        CHECK(r.t.raw_counter() == 19 && r.t.counter() == 1);
    }
    {  // a zero prescaler holds the counter where it was (base value untouched), and so does a zero frequency
        Rig r(TimerMode::kIncrement, 99);
        r.us(7);
        r.t.set_prescaler(0);
        r.us(50);
        CHECK(r.t.counter() == 7 && r.t.raw_counter() == 7);
        Rig q(TimerMode::kIncrement, 99, 0.0);
        q.us(50);
        CHECK(q.t.counter() == 0);
    }
    {  // TOP equal to the counter keeps it
        Rig r;
        r.us(5);
        r.t.set_top(5);
        CHECK(r.t.counter() == 5);
    }
    {  // reset() restarts the count from now
        Rig r;
        r.us(5);
        r.t.reset();
        CHECK(r.t.counter() == 0);
        r.us(3);
        CHECK(r.t.counter() == 3);
    }
    {  // JS rounding: half and below round toward -infinity for a negative value, not toward zero
        Rig r;
        r.t.set(-3);
        CHECK(r.clk.tick(200.0));
        CHECK(r.t.raw_counter() == -3 && r.t.counter() == 4294967293u);
        Rig q;
        q.t.set(-3);
        CHECK(q.clk.tick(600.0));
        CHECK(q.t.raw_counter() == -2 && q.t.counter() == 4294967294u);
    }
    {  // enabling an enabled timer, or setting the mode it already has, changes nothing
        Rig r;
        r.us(3);
        r.t.set_enable(true);
        CHECK(r.t.counter() == 3);
        Rig z(TimerMode::kZigzag, 4);
        z.us(6);
        CHECK(z.t.raw_counter() == 6 && z.t.counter() == 2);
        z.t.set_mode(TimerMode::kZigzag);
        CHECK(z.t.raw_counter() == 6 && z.t.counter() == 2);
    }
    {  // JS rounding of an exact half goes up (0.5 -> 1)
        Rig r;
        r.t.set_prescaler(2);
        r.us(1);
        CHECK(r.t.raw_counter() == 1 && r.t.counter() == 1);
    }
    {  // a running counter pushed below zero wraps to the top, and set(..., zig_zag_down) on a stopped counter stores the far-side value
        Rig r(TimerMode::kIncrement, 9);
        r.t.advance(-3);
        CHECK(r.t.raw_counter() == 7 && r.t.counter() == 7);
        Rig z(TimerMode::kZigzag, 10);
        z.t.set_enable(false);
        z.t.set(3, true);
        CHECK(z.t.raw_counter() == 17 && z.t.counter() == 3);
    }
    {  // the accessors
        Rig r(TimerMode::kIncrement, 9);
        r.us(3);
        r.t.set(2);
        CHECK(r.t.base_value() == 2 && r.t.base_nanos() == 3000.0 && r.t.frequency() == 1e6 && r.t.prescaler() == 1.0);
        CHECK(r.t.top() == 9 && r.t.mode() == TimerMode::kIncrement && r.t.enable());
    }
    {  // a mode change keeps the value
        Rig r(TimerMode::kIncrement, 9);
        r.us(4);
        r.t.set_mode(TimerMode::kDecrement);
        CHECK(r.t.counter() == 4);
        r.us(1);
        CHECK(r.t.counter() == 3);
    }
}

struct Vec {
    int mode;
    int64_t top;
    double prescaler, freq, nanos;
    int64_t raw;
    uint32_t counter;
};
#define V(m, top, presc, freq, ns, raw, ctr) Vec{m, top, presc, freq, ns, raw, ctr}

static void test_long_runs_and_fractions_against_the_reference() {
    static const Vec vectors[] = {
        V(1, 100ll, 256, 1000000.0, 1234567.0, 96ll, 96u),
        V(0, 1000003ll, 1, 48000000.0, 1000000000000000.0, 768ll, 768u),
        V(0, 1000003ll, 2.5, 125000000.0, 1234567.0, 61728ll, 61728u),
        V(1, 4294967295ll, 1, 125000000.0, 1234567.0, 4294812975ll, 4294812975u),
        V(2, 4294967295ll, 1, 1000000.0, 1234567.0, 1235ll, 1235u),
        V(0, 1000003ll, 1, 1000000.0, 1000000000000000.0, 16ll, 16u),
        V(1, 9ll, 2.5, 125000000.0, 1000000000000000.0, 0ll, 0u),
        V(0, 65535ll, 256, 125000000.0, 1000000000000000.0, 39120ll, 39120u),
        V(0, 1000003ll, 3.3, 1000000.0, 3600000000000.0, 904731ll, 904731u),
        V(0, 1000003ll, 1.0625, 1000000.0, 3600000000000.0, 221742ll, 221742u),
        V(1, 9ll, 1.0625, 1000000.0, 1234567.0, 8ll, 8u),
        V(2, 9ll, 1.0625, 125000000.0, 555.5, 11ll, 7u),
    };
    for (const Vec& v : vectors) {
        Rig r(static_cast<TimerMode>(v.mode), v.top, v.freq);
        r.t.set_prescaler(v.prescaler);
        CHECK(r.clk.tick(v.nanos));
        CHECK(r.t.raw_counter() == v.raw);
        CHECK(r.t.counter() == v.counter);
    }
}

// One alarm on a fresh timer: the time of the first alarm and the times it fires in `run_us` microseconds (-1 for "nothing scheduled").
static void run_alarm(TimerMode mode, int64_t top, int64_t target, int run_us, double prescaler, int64_t set_to, double first, const double* fires, int fire_count) {
    Rig r(mode, top);
    r.t.set_prescaler(prescaler);
    if (set_to >= 0) r.t.set(set_to);
    Fires f;
    f.rig = &r;
    Timer32PeriodicAlarm a;
    a.init(&r.t, &on_fire, &f);
    a.set_target(target);
    a.set_enable(true);
    if (first < 0) {
        CHECK(!r.clk.has_alarm());
    } else {
        CHECK(r.clk.has_alarm());
        CHECK(r.clk.nanos_to_next_alarm() == first);
    }
    r.us(run_us);
    CHECK(f.n == fire_count);
    for (int i = 0; i < fire_count && i < 16; ++i) CHECK(f.at[i] == fires[i]);
    a.detach();
}

static void test_the_alarm_schedule() {
    {
        static const double fires[] = {4000, 14000, 24000};
        run_alarm(TimerMode::kIncrement, 9, 4, 25, 1, -1, 4000, fires, 3);
    }
    {  // the counter is sitting on the target: reached a full wrap later, never now
        static const double fires[] = {10000, 20000};
        run_alarm(TimerMode::kIncrement, 9, 0, 25, 1, -1, 10000, fires, 2);
    }
    {
        static const double fires[] = {9000, 19000};
        run_alarm(TimerMode::kIncrement, 9, 9, 25, 1, -1, 9000, fires, 2);
    }
    run_alarm(TimerMode::kIncrement, 9, 10, 25, 1, -1, -1, nullptr, 0);  // above TOP: never reached, never scheduled
    {
        static const double fires[] = {3000, 8000, 11000, 16000, 19000, 24000};
        run_alarm(TimerMode::kZigzag, 4, 3, 25, 1, -1, 3000, fires, 6);
    }
    {
        static const double fires[] = {5000, 8000, 13000, 16000, 21000, 24000};
        run_alarm(TimerMode::kZigzag, 4, 0, 25, 1, -1, 5000, fires, 6);
    }
    {
        static const double fires[] = {4000, 9000, 12000, 17000, 20000, 25000};
        run_alarm(TimerMode::kZigzag, 4, 4, 25, 1, -1, 4000, fires, 6);
    }
    {
        static const double fires[] = {6000, 16000};
        run_alarm(TimerMode::kDecrement, 9, 4, 25, 1, -1, 6000, fires, 2);
    }
    {
        static const double fires[] = {10000, 20000};
        run_alarm(TimerMode::kDecrement, 9, 0, 25, 1, -1, 10000, fires, 2);
    }
    {
        static const double fires[] = {100000};
        run_alarm(TimerMode::kIncrement, Timer32::kNoTop, 100, 250, 1, -1, 100000, fires, 1);
    }
    {  // a fractional prescaler spaces the alarms by 10 clock ticks of 2.5 each
        static const double fires[] = {10000, 35000, 60000};
        run_alarm(TimerMode::kIncrement, 9, 4, 60, 2.5, -1, 10000, fires, 3);
    }
    {  // set() onto the target: the next one is a wrap away
        static const double fires[] = {10000, 20000};
        run_alarm(TimerMode::kIncrement, 9, 4, 25, 1, 4, 10000, fires, 2);
    }
    {  // zigzag: a counter already past the target on the way up or down (the branch with a negative delta)
        static const double f4[] = {1000, 6000, 7000, 12000, 15000, 20000};
        run_alarm(TimerMode::kZigzag, 4, 3, 20, 1, 4, 1000, f4, 6);
        static const double f5[] = {5000, 6000, 11000, 14000, 19000};
        run_alarm(TimerMode::kZigzag, 4, 3, 20, 1, 5, 5000, f5, 5);
        static const double f6[] = {4000, 5000, 10000, 13000, 18000};
        run_alarm(TimerMode::kZigzag, 4, 3, 20, 1, 6, 4000, f6, 5);
        static const double f7[] = {3000, 4000, 9000, 12000, 17000, 20000};
        run_alarm(TimerMode::kZigzag, 4, 3, 20, 1, 7, 3000, f7, 6);
    }
    {  // a counter past the target in INCREMENT / DECREMENT
        static const double fi[] = {7000, 17000};
        run_alarm(TimerMode::kIncrement, 9, 4, 25, 1, 7, 7000, fi, 2);
        static const double fd[] = {8000, 18000};
        run_alarm(TimerMode::kDecrement, 9, 4, 25, 1, 2, 8000, fd, 2);
    }
    {  // the watchdog's case (target 0, DECREMENT, TOP 0xFFFFFFFF): a full 2^32-tick wrap away, and nothing in 5 us
        run_alarm(TimerMode::kDecrement, Timer32::kNoTop, 0, 5, 1, -1, 4294967296000.0, nullptr, 0);
    }
}

static void test_how_the_timer_moves_its_alarms() {
    {  // the order the timer tells them in is the order they were attached, and the clock fires equal times first-in-first-out
        Rig r(TimerMode::kIncrement, 9);
        struct Order {
            char seen[8];
            int n = 0;
        } order;
        struct Tag {
            Order* order;
            char tag;
        };
        Tag ta{&order, 'a'}, tb{&order, 'b'};
        auto record = [](void* ctx) -> bool {
            Tag* t = static_cast<Tag*>(ctx);
            t->order->seen[t->order->n++] = t->tag;
            return true;
        };
        Timer32PeriodicAlarm a, b;
        a.init(&r.t, record, &ta);
        b.init(&r.t, record, &tb);
        a.set_target(3);
        b.set_target(3);
        a.set_enable(true);
        b.set_enable(true);
        r.us(4);
        CHECK(order.n == 2 && order.seen[0] == 'a' && order.seen[1] == 'b');
        r.t.set(0);  // set() re-tells both, in the same order
        r.us(4);
        CHECK(order.n == 4 && order.seen[2] == 'a' && order.seen[3] == 'b');
        a.detach();
        b.detach();
    }
    {  // advance() tells the alarms: the counter moves, so the next alarm comes sooner
        Rig r(TimerMode::kIncrement, 9);
        Fires f;
        f.rig = &r;
        Timer32PeriodicAlarm a;
        a.init(&r.t, &on_fire, &f);
        a.set_target(5);
        a.set_enable(true);
        r.us(2);
        CHECK(r.clk.nanos_to_next_alarm() == 3000.0);
        r.t.advance(1);
        CHECK(r.clk.nanos_to_next_alarm() == 2000.0 && r.t.counter() == 3);
        r.us(3);
        CHECK(f.n == 1 && f.at[0] == 4000.0);
        a.detach();
    }
    {  // advance() wraps the base value into 0..TOP (INCREMENT), 0..2*TOP-1 (ZIGZAG) and leaves a TOP of 0xFFFFFFFF alone; a zigzag TOP of 0 has no modulus
        Rig r(TimerMode::kIncrement, 9);
        r.t.advance(-1);
        CHECK(r.t.counter() == 9u && r.t.raw_counter() == 9);
        Rig q(TimerMode::kIncrement, 9);
        q.t.advance(25);
        CHECK(q.t.counter() == 5u);
        Rig z(TimerMode::kZigzag, 4);
        z.t.advance(-1);
        CHECK(z.t.raw_counter() == 7 && z.t.counter() == 1u);
        z.t.advance(-1);
        CHECK(z.t.raw_counter() == 6 && z.t.counter() == 2u);
        Rig zs(TimerMode::kZigzag, 4);  // stopped: the base value is what is read, so the wrap into 0..2*TOP-1 is visible
        zs.t.set_enable(false);
        zs.t.advance(-1);
        CHECK(zs.t.raw_counter() == 7 && zs.t.counter() == 1u);
        Rig z0(TimerMode::kZigzag, 0);
        z0.t.advance(3);
        CHECK(z0.t.raw_counter() == 0 && z0.t.counter() == 0u);
        Rig d(TimerMode::kDecrement, 9);
        d.us(3);
        CHECK(d.t.counter() == 7u);
        d.t.advance(1);
        CHECK(d.t.counter() == 8u);
        d.t.advance(-2);
        CHECK(d.t.counter() == 6u);
    }
    {  // a target equal to the current one changes nothing, a new one moves the alarm; a prescaler write and reset() move it too
        Rig r(TimerMode::kIncrement, 9);
        Fires f;
        f.rig = &r;
        Timer32PeriodicAlarm a;
        a.init(&r.t, &on_fire, &f);
        a.set_target(5);
        a.set_enable(true);
        r.us(2);
        a.set_target(5);
        CHECK(r.clk.nanos_to_next_alarm() == 3000.0);
        a.set_target(6);
        CHECK(r.clk.nanos_to_next_alarm() == 4000.0);
        r.t.set_prescaler(2);
        CHECK(r.clk.nanos_to_next_alarm() == 8000.0 && r.t.counter() == 2);
        r.t.reset();
        CHECK(r.clk.nanos_to_next_alarm() == 12000.0);
        a.detach();
    }
    {  // enabling and disabling: an alarm is scheduled only while both it and its timer are enabled
        Rig r(TimerMode::kIncrement, 9);
        Fires f;
        f.rig = &r;
        Timer32PeriodicAlarm a;
        a.init(&r.t, &on_fire, &f);
        a.set_target(5);
        CHECK(!r.clk.has_alarm());
        a.set_enable(true);
        CHECK(r.clk.has_alarm() && a.scheduled());
        a.set_enable(false);
        CHECK(!r.clk.has_alarm() && !a.scheduled());
        r.t.set_enable(false);
        a.set_enable(true);
        CHECK(!r.clk.has_alarm());
        r.t.set_enable(true);
        CHECK(r.clk.has_alarm() && r.clk.nanos_to_next_alarm() == 5000.0);
        a.detach();
        CHECK(!r.clk.has_alarm());
    }
    {  // a frequency change moves the alarm; a target above TOP, or a TOP below the target, takes it away
        Rig r(TimerMode::kIncrement, 9);
        Fires f;
        f.rig = &r;
        Timer32PeriodicAlarm a;
        a.init(&r.t, &on_fire, &f);
        a.set_target(6);
        a.set_enable(true);
        r.us(2);
        CHECK(r.clk.nanos_to_next_alarm() == 4000.0);
        r.t.set_frequency(2e6);
        CHECK(r.clk.nanos_to_next_alarm() == 2000.0);
        a.set_target(20);
        CHECK(!r.clk.has_alarm());
        a.set_target(5);
        CHECK(r.clk.has_alarm());
        r.t.set_top(3);
        CHECK(!r.clk.has_alarm());
        a.detach();
    }
    {  // a callback that switches off its own alarm, or the timer, is not rescheduled; a disabled alarm is never scheduled by a retarget or a set()
        struct Self {
            Timer32PeriodicAlarm* alarm;
            Timer32* timer;
            bool kill_timer;
            int fired = 0;
        };
        auto cb = [](void* ctx) -> bool {
            Self* s = static_cast<Self*>(ctx);
            ++s->fired;
            if (s->kill_timer) s->timer->set_enable(false); else s->alarm->set_enable(false);
            return true;
        };
        for (int kill_timer = 0; kill_timer < 2; ++kill_timer) {
            Rig r(TimerMode::kIncrement, 9);
            Timer32PeriodicAlarm a;
            Self self{&a, &r.t, kill_timer != 0};
            a.init(&r.t, cb, &self);
            a.set_target(4);
            a.set_enable(true);
            r.us(25);
            CHECK(self.fired == 1 && !r.clk.has_alarm());
            a.detach();
        }
        Rig r(TimerMode::kIncrement, 9);
        Fires f;
        f.rig = &r;
        Timer32PeriodicAlarm a;
        a.init(&r.t, &on_fire, &f);
        a.set_target(4);
        r.t.set(1);
        CHECK(!r.clk.has_alarm() && !a.enable() && a.target() == 4);
        a.set_target(6);
        CHECK(!r.clk.has_alarm() && a.target() == 6);
        a.detach();
    }
    {  // a failing callback stops the clock at the alarm's own time and the alarm is not rescheduled
        Rig r(TimerMode::kIncrement, 9);
        Fires f;
        f.rig = &r;
        f.fail_on_first = true;
        Timer32PeriodicAlarm a;
        a.init(&r.t, &on_fire, &f);
        a.set_target(4);
        a.set_enable(true);
        CHECK(!r.clk.tick(10000.0));
        CHECK(r.clk.nanos() == 4000.0 && f.n == 1 && !r.clk.has_alarm() && !a.scheduled());
        a.detach();
    }
}

int main() {
    test_the_counter();
    test_set_advance_and_the_stopped_counter();
    test_long_runs_and_fractions_against_the_reference();
    test_the_alarm_schedule();
    test_how_the_timer_moves_its_alarms();
    if (failures == 0) std::printf("timer32: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
