// Standalone checks of src/rp2040py/native/core/watchdog.hpp (see tests/test_core_cpp.py for the flags).
//
// The end-to-end proof is tests/test_watchdog_diff.py (the reference against the chip's WATCHDOG, thousands of random steps with every mutant of the reference caught); these are the directed
// checks that need no Python: the registers and their reset values, the countdown against a bare `Clock` (LOAD, the 2 MHz decrement, the alarm at 0, a stopped tick), TRIGGER and the timeout with
// their reasons, a failing handler, the scratch registers and `reset()`, the warnings, the bus window adapter and detach.
#include <cstdio>

#include "watchdog.hpp"

using namespace rp2040core;
using namespace rp2040core::watchdog_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Env {
    uint32_t warn_kind[8] = {}, warn_offset[8] = {};
    int64_t warn_value[8] = {};
    int warns = 0;
    int triggers = 0;
    double trigger_time[8] = {};
    bool handler_fails = false;
    int failed = 0;
    Clock* clock = nullptr;
    int tick_calls = 0;
    double tick_values[8] = {};
    bool tick_fails = false;
};

static void on_warn(void* ctx, uint32_t kind, uint32_t offset, int64_t value) {
    Env* env = static_cast<Env*>(ctx);
    if (env->warns < 8) {
        env->warn_kind[env->warns] = kind;
        env->warn_offset[env->warns] = offset;
        env->warn_value[env->warns] = value;
    }
    ++env->warns;
}

static bool on_trigger(void* ctx) {
    Env* env = static_cast<Env*>(ctx);
    if (env->triggers < 8) env->trigger_time[env->triggers] = env->clock->nanos();
    ++env->triggers;
    if (env->handler_fails) {
        env->failed = 1;
        return false;
    }
    return true;
}

static bool on_tick(void* ctx, double hz) {
    Env* env = static_cast<Env*>(ctx);
    if (env->tick_calls < 8) env->tick_values[env->tick_calls] = hz;
    ++env->tick_calls;
    if (env->tick_fails) {
        env->failed = 1;
        return false;
    }
    return true;
}

struct Rig {
    Clock clk;
    WatchdogBlock block;
    Env env;
    Rig() {
        env.clock = &clk;
        WatchdogHost host;
        host.warn = on_warn;
        host.trigger = on_trigger;
        host.tick_changed = on_tick;
        host.ctx = &env;
        host.failed = &env.failed;
        block.init(&clk, host);
        block.clk_ref_changed(12e6);  // the crystal: clk_ref from XOSC
    }
    ~Rig() { block.detach(); }
    // What pico-sdk's clocks_init does: the tick generator at 12 cycles of the 12 MHz clk_ref per tick - one microsecond.
    void start_tick() { CHECK(block.write(REG_TICK, 12 | TICK_ENABLE)); }
    bool us(double n) { return clk.tick(n * 1000.0); }
};

static void test_reset_values() {
    Rig r;
    CHECK(r.block.read(REG_CTRL) == (PAUSE_DBG0 | PAUSE_DBG1 | PAUSE_JTAG));  // ENABLE resets to 0, the three PAUSE bits to 1
    CHECK(r.block.read(REG_REASON) == 0 && r.block.read(REG_TICK) == TICK_ENABLE);  // TICK.ENABLE resets to 1, CYCLES to 0: the generator is not running
    for (uint32_t offset = SCRATCH0; offset <= SCRATCH7; offset += 4) CHECK(r.block.read(offset) == 0);
    CHECK(!r.clk.has_alarm() && r.env.warns == 0 && r.block.tick_hz == 0.0 && r.env.tick_calls == 0);
}

static void test_tick_keeps_cycles_and_enable() {
    Rig r;
    CHECK(r.block.write(REG_TICK, 12 | TICK_ENABLE) && r.block.read(REG_TICK) == (12 | TICK_RUNNING | TICK_ENABLE));
    CHECK(r.block.write(REG_TICK, 0xFFFFFFFFu) && r.block.read(REG_TICK) == (0x1FF | TICK_RUNNING | TICK_ENABLE));  // CYCLES is 8:0; RUNNING and COUNT are read-only
    CHECK(r.block.write(REG_TICK, 0x1FF) && r.block.read(REG_TICK) == 0x1FF);  // ENABLE clear: the generator stops
    CHECK(r.block.write(REG_TICK, TICK_ENABLE) && r.block.read(REG_TICK) == TICK_ENABLE);  // CYCLES 0: not running either
}

static void test_the_tick_is_clk_ref_divided_by_cycles() {
    CHECK(tick_frequency(true, 12, 12e6) == 1e6);
    CHECK(tick_frequency(true, 12, 6.5e6) == 6.5e6 / 12);  // clk_ref still on the ring oscillator: 541.67 kHz, not 1 MHz
    CHECK(tick_frequency(true, 1, 12e6) == 12e6);
    CHECK(tick_frequency(false, 12, 12e6) == 0.0 && tick_frequency(true, 0, 12e6) == 0.0 && tick_frequency(true, 12, 0.0) == 0.0);
    Rig r;
    r.start_tick();
    CHECK(r.block.tick_hz == 1e6 && r.env.tick_calls == 1 && r.env.tick_values[0] == 1e6);
    r.block.write(REG_TICK, 12 | TICK_ENABLE);  // unchanged: nobody is told again
    CHECK(r.env.tick_calls == 1);
    r.block.write(REG_TICK, 6 | TICK_ENABLE);
    CHECK(r.block.tick_hz == 2e6 && r.env.tick_calls == 2 && r.env.tick_values[1] == 2e6);
    CHECK(r.block.clk_ref_changed(6.5e6));  // firmware switches clk_ref: the tick follows
    CHECK(r.block.tick_hz == 6.5e6 / 6 && r.env.tick_calls == 3);
    CHECK(r.block.clk_ref_changed(6.5e6) && r.env.tick_calls == 3);
    r.block.write(REG_TICK, 6);  // ENABLE clear
    CHECK(r.block.tick_hz == 0.0 && r.env.tick_calls == 4 && r.env.tick_values[3] == 0.0 && r.block.read(REG_TICK) == 6);
}

static void test_a_failing_tick_listener_is_a_failure_of_the_write_or_the_clock_change() {
    Rig r;
    r.env.tick_fails = true;
    CHECK(!r.block.write(REG_TICK, 12 | TICK_ENABLE));
    CHECK(r.block.tick_hz == 1e6 && r.block.read(REG_TICK) == (12 | TICK_RUNNING | TICK_ENABLE));  // the block's own state was already updated
    r.env.tick_fails = false;
    r.env.failed = 0;
    r.env.tick_fails = true;
    CHECK(!r.block.clk_ref_changed(6e6));
}

static void test_the_countdown_decrements_twice_per_tick_and_fires_at_zero() {
    Rig r;
    r.start_tick();
    CHECK(r.block.write(REG_LOAD, 100));  // 100 counts at 2 MHz: 50 us
    CHECK(r.block.write(REG_CTRL, ENABLE | PAUSE_DBG0));
    CHECK(r.block.read(REG_CTRL) == (ENABLE | PAUSE_DBG0 | 100));
    CHECK(r.clk.has_alarm() && r.clk.nanos_to_next_alarm() == 50000.0);
    CHECK(r.us(10) && (r.block.read(REG_CTRL) & TIME_MASK) == 80);  // 10 us = 20 counts
    CHECK(r.us(40));
    CHECK(r.env.triggers == 1 && r.env.trigger_time[0] == 50000.0 && r.block.read(REG_REASON) == REASON_TIMER);
}

static void test_load_keeps_24_bits() {
    Rig r;
    CHECK(r.block.write(REG_LOAD, 0xFFFFFFFFu) && r.block.read(REG_CTRL) == (PAUSE_DBG0 | PAUSE_DBG1 | PAUSE_JTAG | 0xFFFFFF) - 0);
    r.block.write(REG_LOAD, 0x1000005u);
    CHECK((r.block.read(REG_CTRL) & TIME_MASK) == 5);
}

static void test_the_countdown_follows_the_tick_rate() {
    Rig r;
    r.block.clk_ref_changed(6e6);  // 6 MHz / 12 = 500 kHz: a count every microsecond (two per tick)
    r.start_tick();
    r.block.write(REG_LOAD, 100);
    r.block.write(REG_CTRL, ENABLE);
    CHECK(r.clk.nanos_to_next_alarm() == 100000.0);  // 100 counts at 1 MHz (500 kHz * 2)
    r.block.clk_ref_changed(12e6);  // the crystal: the tick doubles, the countdown with it
    CHECK(r.clk.nanos_to_next_alarm() == 50000.0);
    r.block.write(REG_TICK, TICK_ENABLE);  // CYCLES 0: the tick stops, and with it the countdown
    CHECK(!r.clk.has_alarm() && !r.block.alarm.enable() && !r.block.timer.enable());
    CHECK(r.block.read(REG_CTRL) & ENABLE);  // CTRL.ENABLE is a register: it reads as written, running or not
}

static void test_the_counter_runs_only_while_enable_and_tick_enable_are_set() {
    Rig r;
    r.start_tick();
    r.block.write(REG_LOAD, 1000);
    r.block.write(REG_CTRL, ENABLE);
    CHECK(r.clk.has_alarm());
    r.block.write(REG_TICK, 0);  // tick stopped: the countdown stops and the alarm goes (CTRL.ENABLE still reads as written)
    CHECK(!r.clk.has_alarm() && (r.block.read(REG_CTRL) & ENABLE) && !r.block.alarm.enable() && !r.block.timer.enable());
    CHECK(r.us(1000) && r.env.triggers == 0);
    r.block.write(REG_TICK, 12 | TICK_ENABLE);
    CHECK(r.clk.has_alarm() && (r.block.read(REG_CTRL) & ENABLE));
    r.block.write(REG_CTRL, 0);  // disabled
    CHECK(!r.clk.has_alarm() && !r.block.alarm.enable() && !r.block.timer.enable());
    // ENABLE written while the tick is stopped runs nothing, and the tick started later with ENABLE clear runs nothing either
    r.block.write(REG_TICK, 0);
    r.block.write(REG_CTRL, ENABLE);
    CHECK(!r.clk.has_alarm() && !r.block.alarm.enable() && !r.block.timer.enable());
    r.block.write(REG_CTRL, 0);
    r.block.write(REG_TICK, 12 | TICK_ENABLE);
    CHECK(!r.clk.has_alarm() && !r.block.alarm.enable() && !r.block.timer.enable());
}

static void test_the_pause_bits_are_stored() {
    Rig r;
    CHECK(r.block.write(REG_CTRL, 0));
    CHECK((r.block.read(REG_CTRL) & (PAUSE_DBG0 | PAUSE_DBG1 | PAUSE_JTAG)) == 0);
    CHECK(r.block.write(REG_CTRL, PAUSE_DBG0));
    CHECK((r.block.read(REG_CTRL) & (PAUSE_DBG0 | PAUSE_DBG1 | PAUSE_JTAG)) == PAUSE_DBG0);
    CHECK(r.block.write(REG_CTRL, PAUSE_DBG1));
    CHECK((r.block.read(REG_CTRL) & (PAUSE_DBG0 | PAUSE_DBG1 | PAUSE_JTAG)) == PAUSE_DBG1);
    CHECK(r.block.write(REG_CTRL, PAUSE_JTAG));
    CHECK((r.block.read(REG_CTRL) & (PAUSE_DBG0 | PAUSE_DBG1 | PAUSE_JTAG)) == PAUSE_JTAG);
}

static void test_trigger_sets_force_and_calls_the_handler_before_the_enables() {
    Rig r;
    CHECK(r.block.write(REG_CTRL, TRIGGER | ENABLE));
    CHECK(r.env.triggers == 1 && r.block.read(REG_REASON) == REASON_FORCE);
    CHECK(r.block.read(REG_CTRL) & ENABLE);
    CHECK((r.block.read(REG_CTRL) & TRIGGER) == 0);  // self-clearing: it never reads back
}

static void test_a_failing_handler_leaves_the_enables_as_they_were() {
    Rig r;
    r.env.handler_fails = true;
    CHECK(!r.block.write(REG_CTRL, TRIGGER | ENABLE));
    CHECK(r.block.read(REG_REASON) == REASON_FORCE && !(r.block.read(REG_CTRL) & ENABLE) && (r.block.read(REG_CTRL) & PAUSE_DBG0));
    CHECK(!r.block.write_atomic(REG_CTRL, TRIGGER, kAtomicSet));
}

static void test_a_failing_timeout_stops_the_clock_at_the_alarm() {
    Rig r;
    r.start_tick();
    r.block.write(REG_LOAD, 100);
    r.block.write(REG_CTRL, ENABLE);
    r.env.handler_fails = true;
    CHECK(!r.us(100));  // the alarm's handler failed: the clock stops there
    CHECK(r.clk.nanos() == 50000.0 && r.env.triggers == 1 && r.block.read(REG_REASON) == REASON_TIMER);
}

static void test_scratch_registers_and_reset() {
    Rig r;
    for (uint32_t i = 0; i < 8; ++i) CHECK(r.block.write(SCRATCH0 + 4 * i, 0xA5A50000u + i));
    for (uint32_t i = 0; i < 8; ++i) CHECK(r.block.read(SCRATCH0 + 4 * i) == 0xA5A50000u + i && r.block.scratch[i] == 0xA5A50000u + i);
    r.block.write(REG_CTRL, TRIGGER);
    CHECK(r.block.read(REG_REASON) == REASON_FORCE);
    CHECK(r.block.reset());
    CHECK(r.block.read(REG_REASON) == 0);
    for (uint32_t i = 0; i < 8; ++i) CHECK(r.block.read(SCRATCH0 + 4 * i) == 0);
}

static void test_reset_is_a_power_on_reset_of_the_whole_block() {
    Rig r;
    r.start_tick();
    r.block.write(REG_LOAD, 1000);
    r.block.write(REG_CTRL, ENABLE);  // PAUSE bits cleared too
    r.block.write(SCRATCH0, 5);
    CHECK(r.clk.has_alarm() && r.block.tick_hz == 1e6);
    const int calls = r.env.tick_calls;
    CHECK(r.block.reset());
    CHECK(r.block.read(REG_CTRL) == (PAUSE_DBG0 | PAUSE_DBG1 | PAUSE_JTAG));  // disabled, the countdown at 0, PAUSE set again
    CHECK(r.block.read(REG_TICK) == TICK_ENABLE && r.block.tick_cycles == 0 && r.block.tick_enable);  // ENABLE set, CYCLES 0: not running
    CHECK(r.block.tick_hz == 0.0 && r.env.tick_calls == calls + 1 && r.env.tick_values[calls] == 0.0);  // the TIMER and SysTick are told it stopped
    CHECK(!r.clk.has_alarm() && !r.block.timer.enable() && !r.block.alarm.enable() && r.block.read(SCRATCH0) == 0);
    r.block.write(REG_TICK, 12 | TICK_ENABLE);  // running again
    r.env.tick_fails = true;
    CHECK(!r.block.reset());  // the listener is told the tick stopped and fails: reported
}

static void test_unimplemented_offsets_warn() {
    Rig r;
    CHECK(r.block.read(0x30) == 0xFFFFFFFFu);
    CHECK(r.env.warns == 1 && r.env.warn_kind[0] == kWatchdogWarnRead && r.env.warn_offset[0] == 0x30);
    CHECK(r.block.read(0x3000) == 0xFFFFFFFFu);
    CHECK(r.env.warns == 3 && r.env.warn_kind[2] == kWatchdogWarnReadAtomicArea);
    CHECK(r.block.write(0x34, 9));
    CHECK(r.env.warns == 4 && r.env.warn_kind[3] == kWatchdogWarnWrite && r.env.warn_offset[3] == 0x34 && r.env.warn_value[3] == 9);
    CHECK(r.block.write(REG_REASON, 0xFF) && r.env.warns == 4 && r.block.read(REG_REASON) == 0);  // REASON is read-only: the write is ignored, silently
    CHECK(r.block.read(REG_LOAD) == 0xFFFFFFFFu && r.env.warns == 5);  // LOAD is write-only: a read warns
    CHECK(r.block.read(0x0D) == 0xFFFFFFFFu);  // not word aligned: no register
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    Rig r;
    CHECK(r.block.write_atomic(SCRATCH0, 0x0F, kAtomicSet) && r.block.raw_write_value() == 0x0F && r.block.read(SCRATCH0) == 0x0F);
    CHECK(r.block.write_atomic(SCRATCH0, 0x03, kAtomicClear) && r.block.read(SCRATCH0) == 0x0C);
    CHECK(r.block.write_atomic(SCRATCH0, 0xFF, kAtomicXor) && r.block.read(SCRATCH0) == 0xF3);
    r.start_tick();
    CHECK(r.block.write_atomic(REG_CTRL, ENABLE, kAtomicSet));  // the SDK's hw_set_bits(CTRL, ENABLE): the read has the PAUSE bits, so they are written back as 1
    CHECK(r.block.read(REG_CTRL) & ENABLE);
}

static void test_a_new_block_the_window_handler_and_detach() {
    {
        Rig r;
        WindowHandler handler = r.block.window_handler();
        handler.write32(handler.ctx, SCRATCH7, 7, kAtomicNormal);
        CHECK(handler.read32(handler.ctx, SCRATCH7) == 7);
        r.start_tick();
        r.block.write(REG_LOAD, 100);
        r.block.write(REG_CTRL, ENABLE);
        CHECK(r.clk.has_alarm());
    }  // detach() unlinked the alarm before the clock went away
    WatchdogBlock fresh;
    CHECK(fresh.reason == 0 && fresh.tick_cycles == 0 && fresh.tick_enable && !fresh.enable && fresh.pause_dbg0 && fresh.pause_dbg1 && fresh.pause_jtag && fresh.raw_write_value() == 0);
}

int main() {
    test_reset_values();
    test_tick_keeps_cycles_and_enable();
    test_the_tick_is_clk_ref_divided_by_cycles();
    test_a_failing_tick_listener_is_a_failure_of_the_write_or_the_clock_change();
    test_the_countdown_follows_the_tick_rate();
    test_the_countdown_decrements_twice_per_tick_and_fires_at_zero();
    test_load_keeps_24_bits();
    test_the_counter_runs_only_while_enable_and_tick_enable_are_set();
    test_the_pause_bits_are_stored();
    test_trigger_sets_force_and_calls_the_handler_before_the_enables();
    test_a_failing_handler_leaves_the_enables_as_they_were();
    test_a_failing_timeout_stops_the_clock_at_the_alarm();
    test_scratch_registers_and_reset();
    test_reset_is_a_power_on_reset_of_the_whole_block();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_a_new_block_the_window_handler_and_detach();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all watchdog checks passed\n");
    return 0;
}
