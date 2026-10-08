// Standalone checks of src/rp2040py/native/core/rtc.hpp (see tests/test_core_cpp.py for the flags).
//
// The end-to-end proof is tests/test_rtc_diff.py (the reference against the chip's RTC, thousands of random steps with every mutant of the reference caught); these are the directed checks that
// need no Python: the registers, their masks and reset values, the calendar against a bare `Clock` (every carry, the leap day and FORCE_NOTLEAPYEAR, values the datasheet calls illegal), the second
// following CLKDIV_M1 and clk_rtc, LOAD, RTC_ACTIVE, the RTC_0/RTC_1 latch, the match alarm as a level with INTE/INTF, a failing interrupt line, `reset()`, the warnings, the window and detach.
#include <cstdio>

#include "rtc.hpp"

using namespace rp2040core;
using namespace rp2040core::rtc_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

constexpr double SECOND = 1e9;
static const uint32_t kRegisters[] = {REG_CLKDIV_M1, REG_SETUP_0, REG_SETUP_1, REG_CTRL, REG_IRQ_SETUP_0, REG_IRQ_SETUP_1, REG_RTC_1, REG_RTC_0, REG_INTR, REG_INTE, REG_INTF, REG_INTS};
static const uint32_t kReadOnly[] = {REG_RTC_1, REG_RTC_0, REG_INTR, REG_INTS};

struct Env {
    uint32_t warn_kind[8] = {}, warn_offset[8] = {};
    int64_t warn_value[8] = {};
    int warns = 0;
    int irq_calls = 0;
    bool irq_level = false;
    bool irq_fails = false;
    int failed = 0;
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

static bool on_irq(void* ctx, bool level) {
    Env* env = static_cast<Env*>(ctx);
    ++env->irq_calls;
    env->irq_level = level;
    if (env->irq_fails) {
        env->failed = 1;
        return false;
    }
    return true;
}

static uint32_t date_word(uint32_t year, uint32_t month, uint32_t day) { return (year << 12) | (month << 8) | day; }
static uint32_t time_word(uint32_t dotw, uint32_t hour, uint32_t minute, uint32_t second) { return (dotw << 24) | (hour << 16) | (minute << 8) | second; }

struct Rig {
    Clock clk;
    RtcBlock block;
    Env env;
    Rig() {
        RtcHost host;
        host.warn = on_warn;
        host.irq = on_irq;
        host.ctx = &env;
        host.failed = &env.failed;
        block.init(&clk, host);
    }
    ~Rig() { block.detach(); }
    // What clocks_init and rtc_init do: clk_rtc = XOSC / 256, CLKDIV_M1 = clk_rtc - 1, so a second is a second.
    void start_clock(uint32_t clkdiv_m1 = 46874) {
        CHECK(block.clk_rtc_changed(46875.0));
        CHECK(block.write(REG_CLKDIV_M1, clkdiv_m1));
    }
    // rtc_set_datetime(): disable, SETUP_0/1, LOAD, ENABLE.
    void set_datetime(uint32_t year, uint32_t month, uint32_t day, uint32_t dotw, uint32_t hour, uint32_t minute, uint32_t second, uint32_t extra_ctrl = 0) {
        CHECK(block.write(REG_CTRL, 0));
        CHECK(block.write(REG_SETUP_0, date_word(year, month, day)));
        CHECK(block.write(REG_SETUP_1, time_word(dotw, hour, minute, second)));
        CHECK(block.write(REG_CTRL, CTRL_LOAD | extra_ctrl));
        CHECK(block.write(REG_CTRL, CTRL_RTC_ENABLE | extra_ctrl));
    }
    bool seconds(double n) { return clk.tick(n * SECOND); }
    bool at(uint32_t year, uint32_t month, uint32_t day, uint32_t dotw, uint32_t hour, uint32_t minute, uint32_t second) const {
        return block.year == year && block.month == month && block.day == day && block.dotw == dotw && block.hour == hour && block.minute == minute && block.second == second;
    }
};

static void test_reset_values_and_masks() {
    Rig r;
    for (uint32_t offset : kRegisters) CHECK(r.block.read(offset) == 0);
    CHECK(!r.clk.has_alarm() && r.env.warns == 0 && r.env.irq_calls == 0);
    CHECK(r.block.write(REG_CLKDIV_M1, 0xFFFFFFFFu) && r.block.read(REG_CLKDIV_M1) == 0xFFFF);
    CHECK(r.block.write(REG_SETUP_0, 0xFFFFFFFFu) && r.block.read(REG_SETUP_0) == 0x00FFFF1Fu);
    CHECK(r.block.write(REG_SETUP_1, 0xFFFFFFFFu) && r.block.read(REG_SETUP_1) == 0x071F3F3Fu);
    CHECK(r.block.write(REG_IRQ_SETUP_0, 0xFFFFFFFFu) && r.block.read(REG_IRQ_SETUP_0) == (0x17FFFF1Fu | IRQ_SETUP_0_MATCH_ACTIVE));  // MATCH_ACTIVE (29) is read-only, set by MATCH_ENA
    CHECK(r.block.write(REG_IRQ_SETUP_1, 0xFFFFFFFFu) && r.block.read(REG_IRQ_SETUP_1) == 0xF71F3F3Fu);
    CHECK(r.block.write(REG_INTE, 0xFFFFFFFFu) && r.block.read(REG_INTE) == 1);
    CHECK(r.block.write(REG_INTF, 0xFFFFFFFFu) && r.block.read(REG_INTF) == 1);
    CHECK(r.block.write(REG_CTRL, 0xFFFFFFFFu) && r.block.read(REG_CTRL) == (CTRL_FORCE_NOTLEAPYEAR | CTRL_RTC_ENABLE));  // LOAD (SC) reads 0, ACTIVE needs clk_rtc
}

static void test_read_only_registers_ignore_writes_silently() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 6, 6, 4, 10, 20, 30);
    const uint32_t rtc_0 = r.block.read(REG_RTC_0), rtc_1 = r.block.read(REG_RTC_1);
    for (uint32_t offset : kReadOnly) CHECK(r.block.write(offset, 0xFFFFFFFFu));
    CHECK(r.block.read(REG_RTC_0) == rtc_0 && r.block.read(REG_RTC_1) == rtc_1 && r.block.read(REG_INTR) == 0 && r.env.warns == 0);
}

static void test_nothing_counts_without_clk_rtc_and_active_follows_enable_and_the_clock() {
    Rig r;
    r.set_datetime(2024, 1, 1, 1, 0, 0, 0);
    CHECK(r.block.read(REG_CTRL) == CTRL_RTC_ENABLE && !r.clk.has_alarm());  // enabled, not active: the SDK's rtc_set_datetime() would wait
    CHECK(r.seconds(5) && r.at(2024, 1, 1, 1, 0, 0, 0));
    r.start_clock();
    CHECK(r.block.read(REG_CTRL) == (CTRL_RTC_ENABLE | CTRL_RTC_ACTIVE) && r.clk.has_alarm());
    CHECK(r.seconds(3) && r.block.second == 3);
    CHECK(r.block.write(REG_CTRL, 0) && r.block.read(REG_CTRL) == 0 && !r.clk.has_alarm());
    CHECK(r.seconds(5) && r.block.second == 3);  // stopped, not lost
    CHECK(r.block.write(REG_CTRL, CTRL_RTC_ENABLE) && r.seconds(1) && r.block.second == 4);
    CHECK(r.block.clk_rtc_changed(0.0) && !r.clk.has_alarm() && r.block.read(REG_CTRL) == CTRL_RTC_ENABLE);  // the generator stopped
    CHECK(r.seconds(5) && r.block.second == 4);
    CHECK(r.block.clk_rtc_changed(46875.0) && r.clk.has_alarm() && r.seconds(1) && r.block.second == 5);
}

static void test_the_second_is_clkdiv_m1_plus_one_periods_of_clk_rtc() {
    Rig r;
    r.start_clock(46874 / 3);  // 15625 periods of 46875 Hz: a third of a second
    r.set_datetime(2024, 5, 5, 0, 0, 0, 0);
    CHECK(r.seconds(2) && r.block.second == 6);
    CHECK(r.clk.nanos_to_next_alarm() > 0.0 && r.clk.nanos_to_next_alarm() <= SECOND / 3.0 + 1.0);
    CHECK(r.block.write(REG_CLKDIV_M1, 1));  // two periods: 23437.5 seconds per real second
    CHECK(r.seconds(1) && r.block.minute == (6 + 23437) % 3600 / 60 % 60 && r.block.second == (6 + 23437) % 60);
}

static void test_the_second_restarts_on_a_new_rate_and_a_manual_second_and_set_ctrl_does_not_load() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 5, 5, 0, 0, 0, 0);
    CHECK(r.seconds(0.5));
    CHECK(r.block.clk_rtc_changed(46875.0) && r.clk.nanos_to_next_alarm() == SECOND / 2.0);  // the same rate: the second under way is left alone
    CHECK(r.block.clk_rtc_changed(46875.0 / 2.0) && r.clk.nanos_to_next_alarm() == 2.0 * SECOND);  // a new rate: a new second, of the new length
    CHECK(r.seconds(1) && r.block.second == 0);
    CHECK(r.block.second_elapsed() && r.block.second == 1 && r.clk.nanos_to_next_alarm() == 2.0 * SECOND);  // run by hand: counts, and starts the next second
    CHECK(r.block.clk_rtc_changed(46875.0));
    // the attribute write of the reference stores the bits only: no load, no interrupt, and a second under way stays
    r.block.write(REG_SETUP_1, time_word(0, 9, 9, 9));
    CHECK(r.seconds(0.25) && r.block.set_ctrl(CTRL_RTC_ENABLE | CTRL_FORCE_NOTLEAPYEAR | CTRL_LOAD) && r.block.second == 1 && r.block.force_not_leap_year && r.block.enable);
    CHECK(r.clk.nanos_to_next_alarm() == 0.75 * SECOND);
    CHECK(r.block.set_ctrl(0) && !r.block.enable && !r.block.force_not_leap_year && !r.clk.has_alarm());
    CHECK(r.block.set_ctrl(CTRL_RTC_ENABLE) && r.clk.has_alarm() && r.clk.nanos_to_next_alarm() == SECOND);
}

static void test_load_works_while_disabled_and_restarts_the_second() {
    Rig r;
    r.start_clock();
    r.set_datetime(2031, 7, 14, 1, 12, 34, 56);
    CHECK(r.block.write(REG_CTRL, 0));
    CHECK(r.block.write(REG_SETUP_1, time_word(2, 3, 4, 5)) && r.block.write(REG_CTRL, CTRL_LOAD));  // loaded while disabled
    CHECK(r.at(2031, 7, 14, 2, 3, 4, 5) && r.block.read(REG_CTRL) == 0);
    CHECK(r.seconds(3) && r.block.second == 5);  // not enabled: not counting
    CHECK(r.block.write(REG_CTRL, CTRL_RTC_ENABLE) && r.seconds(0.5));
    CHECK(r.block.write(REG_CTRL, CTRL_RTC_ENABLE | CTRL_LOAD));  // a load restarts the second: the half second already counted is gone
    CHECK(r.seconds(0.75) && r.block.second == 5);
    CHECK(r.seconds(0.5) && r.block.second == 6);
    const double next = r.clk.nanos_to_next_alarm();
    CHECK(r.block.write(REG_CTRL, CTRL_RTC_ENABLE) && r.clk.nanos_to_next_alarm() == next);  // a write that only keeps the state leaves the second alone
}

static void test_the_calendar_carries() {
    Rig r;
    r.start_clock();
    r.set_datetime(2025, 6, 6, 5, 22, 59, 59);
    CHECK(r.seconds(1) && r.at(2025, 6, 6, 5, 23, 0, 0));  // 22 -> 23 is no carry of the day
    r.set_datetime(2025, 11, 30, 0, 23, 59, 59);
    CHECK(r.seconds(1) && r.at(2025, 12, 1, 1, 0, 0, 0));  // November -> December, no new year
    r.set_datetime(2025, 5, 31, 0, 23, 59, 59);
    CHECK(r.seconds(1) && r.at(2025, 6, 1, 1, 0, 0, 0));
    r.set_datetime(2025, 12, 31, 3, 23, 59, 58);
    CHECK(r.seconds(1) && r.at(2025, 12, 31, 3, 23, 59, 59));
    CHECK(r.seconds(1) && r.at(2026, 1, 1, 4, 0, 0, 0));
    r.set_datetime(2025, 3, 31, 6, 23, 59, 59);
    CHECK(r.seconds(1) && r.at(2025, 4, 1, 0, 0, 0, 0));  // Sat -> Sun
    r.set_datetime(2025, 4, 30, 1, 23, 59, 59);
    CHECK(r.seconds(1) && r.at(2025, 5, 1, 2, 0, 0, 0));  // April has 30 days
    r.set_datetime(2025, 8, 30, 1, 23, 59, 59);
    CHECK(r.seconds(1) && r.at(2025, 8, 31, 2, 0, 0, 0));
    r.set_datetime(2025, 1, 1, 4, 10, 20, 30);
    CHECK(r.seconds(1) && r.block.dotw == 4);  // the weekday is not computed from the date
    r.set_datetime(4095, 12, 31, 0, 23, 59, 59);
    CHECK(r.seconds(1) && r.at(0, 1, 1, 1, 0, 0, 0));  // the year is 12 bits
}

static void test_leap_year_unless_forced_off() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 2, 28, 3, 23, 59, 59);
    CHECK(r.seconds(1) && r.at(2024, 2, 29, 4, 0, 0, 0));
    CHECK(r.seconds(86400) && r.block.month == 3 && r.block.day == 1);
    r.set_datetime(2025, 2, 28, 3, 23, 59, 59);
    CHECK(r.seconds(1) && r.block.month == 3 && r.block.day == 1);
    r.set_datetime(2100, 2, 28, 3, 23, 59, 59);  // 4.8.2: divisible by 4 is a leap year; the century years are for FORCE_NOTLEAPYEAR to deal with
    CHECK(r.seconds(1) && r.block.month == 2 && r.block.day == 29);
    r.set_datetime(2100, 2, 28, 3, 23, 59, 59, CTRL_FORCE_NOTLEAPYEAR);
    CHECK(r.seconds(1) && r.block.month == 3 && r.block.day == 1);
    CHECK(r.block.read(REG_CTRL) == (CTRL_FORCE_NOTLEAPYEAR | CTRL_RTC_ENABLE | CTRL_RTC_ACTIVE));
}

static void test_illegal_values_are_not_checked() {
    Rig r;
    r.start_clock();
    r.set_datetime(0, 0, 0, 7, 31, 63, 63);
    CHECK(r.seconds(3));
    CHECK(r.block.year == 0 && r.block.month == 0 && r.block.day == 1 && r.block.dotw == 0 && r.block.hour == 0 && r.block.minute == 0 && r.block.second == 2);
    r.set_datetime(2025, 15, 30, 6, 23, 59, 59);  // a month outside 1..12 has 31 days
    CHECK(r.seconds(1) && r.block.month == 15 && r.block.day == 31);
    CHECK(r.seconds(86400) && r.block.month == 1 && r.block.day == 1 && r.block.year == 2026);
}

static void test_reading_rtc_0_latches_rtc_1() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 12, 31, 2, 23, 59, 59);
    CHECK(r.block.read(REG_RTC_1) == 0);  // nothing latched yet
    CHECK(r.block.read(REG_RTC_0) == time_word(2, 23, 59, 59));
    CHECK(r.seconds(1));  // the date rolls over between the two reads
    CHECK(r.block.read(REG_RTC_1) == date_word(2024, 12, 31));
    CHECK(r.block.read(REG_RTC_0) == time_word(3, 0, 0, 0) && r.block.read(REG_RTC_1) == date_word(2025, 1, 1));
    CHECK(r.block.write_atomic(REG_RTC_0, 0, kAtomicSet) && r.block.latched_date == date_word(2025, 1, 1));  // the alias decodes against a read, which latches
}

static void test_the_alarm_is_a_level_while_the_enabled_fields_match() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 6, 6, 4, 10, 20, 57);
    r.block.write(REG_IRQ_SETUP_0, 0);
    r.block.write(REG_IRQ_SETUP_1, IRQ_SETUP_1_SEC_ENA);  // second == 0 of every minute
    r.block.write(REG_INTE, 1);
    r.block.write(REG_IRQ_SETUP_0, IRQ_SETUP_0_MATCH_ENA);
    CHECK(r.block.read(REG_IRQ_SETUP_0) == (IRQ_SETUP_0_MATCH_ENA | IRQ_SETUP_0_MATCH_ACTIVE) && r.block.read(REG_INTR) == 0 && !r.env.irq_level);
    CHECK(r.seconds(3));  // 10:21:00
    CHECK(r.block.read(REG_INTR) == 1 && r.block.read(REG_INTS) == 1 && r.env.irq_level);
    CHECK(r.block.write(REG_IRQ_SETUP_0, 0));  // the handler: rtc_disable_alarm()
    CHECK(r.block.read(REG_IRQ_SETUP_0) == 0 && r.block.read(REG_INTR) == 0 && !r.env.irq_level);
    CHECK(r.block.write(REG_IRQ_SETUP_0, IRQ_SETUP_0_MATCH_ENA) && r.block.read(REG_INTR) == 1 && r.env.irq_level);  // re-enabled in the same second: it matches still
    CHECK(r.block.write(REG_IRQ_SETUP_0, 0));
    CHECK(r.seconds(1) && r.block.write(REG_IRQ_SETUP_0, IRQ_SETUP_0_MATCH_ENA) && r.block.read(REG_INTR) == 0);  // 10:21:01
    CHECK(r.seconds(59) && r.block.read(REG_INTR) == 1 && r.env.irq_level);  // and once a minute
}

static void test_each_enabled_field_must_match_and_none_matches_every_second() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 6, 6, 4, 10, 20, 30);
    struct Field { uint32_t reg, enable, value; };
    const Field fields[] = {{REG_IRQ_SETUP_0, IRQ_SETUP_0_YEAR_ENA, 2024u << 12}, {REG_IRQ_SETUP_0, IRQ_SETUP_0_MONTH_ENA, 6u << 8}, {REG_IRQ_SETUP_0, IRQ_SETUP_0_DAY_ENA, 6u},
                            {REG_IRQ_SETUP_1, IRQ_SETUP_1_DOTW_ENA, 4u << 24}, {REG_IRQ_SETUP_1, IRQ_SETUP_1_HOUR_ENA, 10u << 16}, {REG_IRQ_SETUP_1, IRQ_SETUP_1_MIN_ENA, 20u << 8},
                            {REG_IRQ_SETUP_1, IRQ_SETUP_1_SEC_ENA, 30u}};
    for (const Field& f : fields) {
        for (int wrong = 0; wrong < 2; ++wrong) {
            r.block.write(REG_IRQ_SETUP_0, 0);
            r.block.write(REG_IRQ_SETUP_1, 0);
            r.block.write(f.reg, f.enable | (wrong ? (f.value ^ (f.value & (~f.value + 1))) : f.value));  // wrong: the lowest set bit cleared
            r.block.write(REG_IRQ_SETUP_0, r.block.read(REG_IRQ_SETUP_0) | IRQ_SETUP_0_MATCH_ENA);
            CHECK(r.block.read(REG_INTR) == (wrong ? 0u : 1u));
        }
    }
    r.block.write(REG_IRQ_SETUP_0, IRQ_SETUP_0_MATCH_ENA);
    r.block.write(REG_IRQ_SETUP_1, 0);
    CHECK(r.block.read(REG_INTR) == 1);  // no field enabled: every second matches
    r.block.write(REG_IRQ_SETUP_0, 0);
    CHECK(r.block.read(REG_INTR) == 0);  // MATCH_ENA clear: nothing
}

static void test_the_interrupt_is_masked_by_inte_and_forced_by_intf() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 6, 6, 4, 10, 20, 30);
    r.block.write(REG_IRQ_SETUP_0, IRQ_SETUP_0_MATCH_ENA);
    CHECK(r.block.read(REG_INTR) == 1 && r.block.read(REG_INTS) == 0 && !r.env.irq_level);
    r.block.write(REG_INTE, 1);
    CHECK(r.block.read(REG_INTS) == 1 && r.env.irq_level);
    r.block.write(REG_INTE, 0);
    CHECK(r.block.read(REG_INTS) == 0 && !r.env.irq_level);
    r.block.write(REG_IRQ_SETUP_0, 0);
    r.block.write(REG_INTF, 1);
    CHECK(r.block.read(REG_INTR) == 0 && r.block.read(REG_INTS) == 0 && !r.env.irq_level);
    r.block.write(REG_INTE, 1);
    CHECK(r.block.read(REG_INTS) == 1 && r.env.irq_level);
}

static void test_a_failing_interrupt_line_is_a_failure_of_the_write_the_load_or_the_second() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 6, 6, 4, 10, 20, 58);
    r.env.irq_fails = true;
    CHECK(!r.block.write(REG_INTE, 1) && r.block.inte == 1);  // the register is written, then the line fails
    r.env.failed = 0;
    CHECK(!r.block.write(REG_IRQ_SETUP_1, IRQ_SETUP_1_SEC_ENA | 59u));
    r.env.failed = 0;
    CHECK(!r.clk.tick(SECOND));  // the second's alarm: the counter has moved on, and the next second is scheduled
    CHECK(r.block.second == 59 && r.clk.has_alarm());
    r.env.failed = 0;
    r.env.irq_fails = false;
    CHECK(r.block.write(REG_CTRL, CTRL_RTC_ENABLE | CTRL_LOAD) && r.block.second == 58);
    r.env.irq_fails = true;
    CHECK(!r.block.write(REG_CTRL, CTRL_RTC_ENABLE | CTRL_LOAD));  // a load tells the line
    CHECK(r.block.set_ctrl(CTRL_RTC_ENABLE));                       // the attribute write does not
}

static void test_reset_clears_the_block_and_keeps_clk_rtc() {
    Rig r;
    r.start_clock();
    r.set_datetime(2024, 6, 6, 4, 10, 20, 30);
    r.block.write(REG_IRQ_SETUP_0, IRQ_SETUP_0_MATCH_ENA);
    r.block.write(REG_INTE, 1);
    r.block.write(REG_INTF, 1);
    r.block.read(REG_RTC_0);
    CHECK(r.env.irq_level);
    CHECK(r.block.reset());
    for (uint32_t offset : kRegisters) CHECK(r.block.read(offset) == 0);
    CHECK(!r.env.irq_level && !r.clk.has_alarm() && r.block.clk_rtc() == 46875.0 && r.at(0, 0, 0, 0, 0, 0, 0));
}

static void test_unimplemented_offsets_warn() {
    Rig r;
    CHECK(r.block.read(0x30) == 0xFFFFFFFFu && r.env.warns == 1 && r.env.warn_kind[0] == kRtcWarnRead && r.env.warn_offset[0] == 0x30);
    CHECK(r.block.read(0x1000) == 0xFFFFFFFFu && r.env.warns == 2 && r.env.warn_offset[1] == 0x1000);  // exactly 0x1000 is not "in the atomic area" (> 0x1000)
    CHECK(r.block.read(0x1034) == 0xFFFFFFFFu && r.env.warns == 4 && r.env.warn_kind[3] == kRtcWarnReadAtomicArea);
    CHECK(r.block.write(0x40, 0x1234) && r.env.warns == 5 && r.env.warn_kind[4] == kRtcWarnWrite && r.env.warn_value[4] == 0x1234);
}

static void test_alias_writes_decode_against_a_read_and_remember_the_raw_value() {
    Rig r;
    r.start_clock();
    r.block.write(REG_CLKDIV_M1, 0x0F);
    CHECK(r.block.write_atomic(REG_CLKDIV_M1, 0xF0, kAtomicSet) && r.block.read(REG_CLKDIV_M1) == 0xFF && r.block.raw_write_value() == 0xF0);
    CHECK(r.block.write_atomic(REG_CLKDIV_M1, 0x0F, kAtomicClear) && r.block.read(REG_CLKDIV_M1) == 0xF0);
    CHECK(r.block.write_atomic(REG_CLKDIV_M1, 0xFF, kAtomicXor) && r.block.read(REG_CLKDIV_M1) == 0x0F);
    r.env.failed = 1;  // a failure parked before the write
    CHECK(!r.block.write_atomic(REG_CLKDIV_M1, 0x1, kAtomicSet));
}

static void test_a_new_block_the_window_handler_and_detach() {
    {
        Rig r;
        r.start_clock();
        WindowHandler handler = r.block.window_handler();
        handler.write32(handler.ctx, REG_CLKDIV_M1, 0x123, kAtomicNormal);
        CHECK(handler.read32(handler.ctx, REG_CLKDIV_M1) == 0x123);
        r.set_datetime(2024, 1, 1, 1, 0, 0, 0);
        CHECK(r.clk.has_alarm());
    }  // detach() unlinked the second before the clock went away
    RtcBlock fresh;
    CHECK(fresh.clkdiv_m1 == 0 && !fresh.enable && !fresh.running() && fresh.ctrl() == 0 && fresh.raw_write_value() == 0 && fresh.clk_rtc() == 0.0);
}

int main() {
    test_reset_values_and_masks();
    test_read_only_registers_ignore_writes_silently();
    test_nothing_counts_without_clk_rtc_and_active_follows_enable_and_the_clock();
    test_the_second_is_clkdiv_m1_plus_one_periods_of_clk_rtc();
    test_the_second_restarts_on_a_new_rate_and_a_manual_second_and_set_ctrl_does_not_load();
    test_load_works_while_disabled_and_restarts_the_second();
    test_the_calendar_carries();
    test_leap_year_unless_forced_off();
    test_illegal_values_are_not_checked();
    test_reading_rtc_0_latches_rtc_1();
    test_the_alarm_is_a_level_while_the_enabled_fields_match();
    test_each_enabled_field_must_match_and_none_matches_every_second();
    test_the_interrupt_is_masked_by_inte_and_forced_by_intf();
    test_a_failing_interrupt_line_is_a_failure_of_the_write_the_load_or_the_second();
    test_reset_clears_the_block_and_keeps_clk_rtc();
    test_unimplemented_offsets_warn();
    test_alias_writes_decode_against_a_read_and_remember_the_raw_value();
    test_a_new_block_the_window_handler_and_detach();
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all rtc checks passed\n");
    return 0;
}
