// A 32-bit prescaled counter and its periodic alarms, in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of utils/timer32.py, which stays as the
// pure-Python reference. It is the counter of the PWM's eight channels, and of the watchdog and the PPB's SysTick, which will sit on it too.
//
// What it is: a counter that is not stepped but *computed* - `raw_counter` derives the value from the clock's time since the counter was last set (`base_value` at `base_nanos`), the
// frequency, the prescaler, the mode (INCREMENT, DECREMENT, ZIGZAG) and TOP. A `Timer32PeriodicAlarm` is a compare value on such a counter: it keeps one alarm on the chip's `Clock`
// pointed at the next time the counter reaches `target`, and moves it whenever the counter is changed (the timer tells its alarms, in the order they were attached).
//
// Failures follow the contract of core_host.hpp: the alarm's callback returns `false` when it failed; the alarm then returns `false` to the clock at once and does NOT reschedule -
// what the reference's exception leaves (the clock stops ticking at that alarm's time).
//
// Quirks of the reference that are kept (each pinned by tests/cpp/test_timer32.cpp and, through the PWM, by tests/test_pwm_diff.py):
//   - the counter starts at `base_nanos` 0, not at the clock's time; a timer made late counts from time zero until it is `set()`/`reset()`;
//   - setting the PRESCALER also writes `enabled`, from the *old* prescaler (`enabled = old != 0`): a prescaler write can switch a stopped timer on;
//   - `raw_counter` of a stopped timer (or one with no frequency or prescaler) is `base_value` untouched - no modulo, so it can lie outside 0..TOP;
//   - ZIGZAG with TOP == 0 has a period of 0: the counter reads 0 (the reference's guard against a modulo by zero; rp2040js computes NaN there, which `& 0xFFFFFFFF` turns into 0);
//   - an alarm whose target is above TOP never fires (with TOP != 0xFFFFFFFF); a target the counter is *sitting on* is reached one full wrap later, never "now" - a zero delay would
//     livelock the clock (it once froze the chip, via the watchdog);
//   - rounding is the JS `Math.round` (half towards +infinity), not Python's banker's rounding.
// What differs: the reference's integers are unbounded, here they are int64 and the counter's arithmetic is exact up to 2^53 ticks (the same bound a double has); `ticks % top_modulo` is
// computed on the integer and the fractional part separately, which is exact (it is what fmod returns) and needs no libm, so the header stays buildable for a bare wasm32 target.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. An alarm is a node the owner holds; it must not move after `init()` and must be `detach()`ed before it goes away.
#ifndef RP2040PY_CORE_TIMER32_HPP
#define RP2040PY_CORE_TIMER32_HPP

#include <cstdint>

#include "clock.hpp"

namespace rp2040core {

enum class TimerMode : uint32_t { kIncrement = 0, kDecrement = 1, kZigzag = 2 };

namespace timer32_detail {
// JS Math.round: half towards +infinity.
inline int64_t js_round(double value) noexcept {
    const double shifted = value + 0.5;
    int64_t whole = static_cast<int64_t>(shifted);  // truncates toward zero
    if (static_cast<double>(whole) > shifted) --whole;
    return whole;
}
// Python's `%` for an integer and a positive modulus: never negative.
inline int64_t floor_mod(int64_t value, int64_t modulus) noexcept {
    const int64_t r = value % modulus;
    return r < 0 ? r + modulus : r;
}
// Python's `ticks % modulus` for a non-negative float and a positive integer modulus: exact, split into the integer and the fractional part.
inline double float_mod(double ticks, int64_t modulus) noexcept {
    const int64_t whole = static_cast<int64_t>(ticks);
    const double fraction = ticks - static_cast<double>(whole);
    return static_cast<double>(whole % modulus) + fraction;
}
}  // namespace timer32_detail

class Timer32PeriodicAlarm;

class Timer32 {
public:
    static constexpr int64_t kNoTop = 0xFFFFFFFFll;

    Timer32() = default;
    Timer32(const Timer32&) = delete;  // the alarms hold a pointer to this object
    Timer32& operator=(const Timer32&) = delete;

    void init(Clock* clock, double base_freq) noexcept {
        clock_ = clock;
        base_freq_ = base_freq;
    }

    Clock* clock() const noexcept { return clock_; }

    // ---- the counter ------------------------------------------------------------------------------------------------
    void reset() noexcept {
        base_nanos_ = clock_->nanos();
        base_value_ = 0;
        updated();
    }

    void set(int64_t value, bool zig_zag_down = false) noexcept {
        base_value_ = zig_zag_down ? top_value_ * 2 - value : value;
        base_nanos_ = clock_->nanos();
        updated();
    }

    // Adds to the counter (negative in DECREMENT mode counts the other way). The base value is kept inside the counter's range - retarding past 0 wraps to TOP - and the alarms are
    // told, because the time to each target has changed (rp2040js 1.4.0's fix of the PWM's phase strobes, which the reference follows).
    void advance(int64_t delta) noexcept {
        base_value_ += delta;
        if (top_value_ != kNoTop) {
            const int64_t top_modulo = mode_ == TimerMode::kZigzag ? top_value_ * 2 : top_value_ + 1;
            if (top_modulo != 0) base_value_ = timer32_detail::floor_mod(base_value_, top_modulo);
        }
        updated();
    }

    int64_t raw_counter() const noexcept {
        if (base_freq_ == 0.0 || prescaler_ == 0.0 || !enabled_) return base_value_;
        const bool zigzag = mode_ == TimerMode::kZigzag;
        const double ticks = ((clock_->nanos() - base_nanos_) / 1e9) * (base_freq_ / prescaler_);
        const int64_t top_modulo = zigzag ? top_value_ * 2 : top_value_ + 1;
        if (top_modulo == 0) return 0;  // ZIGZAG with TOP == 0: one state
        const double delta = mode_ == TimerMode::kDecrement ? static_cast<double>(top_modulo) - timer32_detail::float_mod(ticks, top_modulo) : ticks;
        int64_t current = timer32_detail::js_round(static_cast<double>(base_value_) + delta);
        if (top_value_ != kNoTop) current = timer32_detail::floor_mod(current, top_modulo);
        return current;
    }

    uint32_t counter() const noexcept {
        int64_t current = raw_counter();
        if (mode_ == TimerMode::kZigzag && current > top_value_) current = top_value_ * 2 - current;
        return static_cast<uint32_t>(current);  // the reference's `& 0xFFFFFFFF`, two's complement for a negative value
    }

    int64_t top() const noexcept { return top_value_; }
    void set_top(int64_t value) noexcept {
        const int64_t now_counter = counter();
        top_value_ = value;
        set(now_counter <= top_value_ ? now_counter : 0);
    }

    double frequency() const noexcept { return base_freq_; }
    void set_frequency(double value) noexcept {
        base_value_ = counter();
        base_nanos_ = clock_->nanos();
        base_freq_ = value;
        updated();
    }

    double prescaler() const noexcept { return prescaler_; }
    void set_prescaler(double value) noexcept {
        base_value_ = counter();
        base_nanos_ = clock_->nanos();
        enabled_ = prescaler_ != 0.0;  // from the OLD prescaler, as in the reference
        prescaler_ = value;
        updated();
    }

    double to_nanos(int64_t cycles) const noexcept { return (static_cast<double>(cycles) * 1e9) / (base_freq_ / prescaler_); }

    bool enable() const noexcept { return enabled_; }
    void set_enable(bool value) noexcept {
        if (value == enabled_) return;
        if (value) {
            base_nanos_ = clock_->nanos();
        } else {
            base_value_ = counter();
        }
        enabled_ = value;
        updated();
    }

    TimerMode mode() const noexcept { return mode_; }
    uint32_t mode_raw() const noexcept { return static_cast<uint32_t>(mode_); }  // for the shells: the enum as a plain number
    void set_mode_raw(uint32_t value) noexcept { set_mode(static_cast<TimerMode>(value)); }
    void set_mode(TimerMode value) noexcept {
        if (value == mode_) return;
        const int64_t now_counter = counter();
        mode_ = value;
        set(now_counter);
    }

    // The raw state, for the shells and the checks.
    int64_t base_value() const noexcept { return base_value_; }
    double base_nanos() const noexcept { return base_nanos_; }

    // Attaches an alarm to be told (in attach order) whenever the counter is changed. Called by `Timer32PeriodicAlarm::init`.
    void attach(Timer32PeriodicAlarm* alarm) noexcept;

private:
    void updated() noexcept;

    Clock* clock_ = nullptr;
    double base_freq_ = 0.0;
    int64_t base_value_ = 0;
    double base_nanos_ = 0.0;
    int64_t top_value_ = kNoTop;
    double prescaler_ = 1.0;
    TimerMode mode_ = TimerMode::kIncrement;
    bool enabled_ = true;
    Timer32PeriodicAlarm* first_ = nullptr;
    Timer32PeriodicAlarm* last_ = nullptr;
};

// A compare value on a Timer32: `callback` runs each time the counter reaches `target`.
class Timer32PeriodicAlarm {
public:
    using Callback = bool (*)(void* ctx);  // false: failure parked with the owner

    Timer32PeriodicAlarm() = default;
    Timer32PeriodicAlarm(const Timer32PeriodicAlarm&) = delete;  // the timer and the clock hold pointers to this object
    Timer32PeriodicAlarm& operator=(const Timer32PeriodicAlarm&) = delete;

    void init(Timer32* timer, Callback callback, void* ctx) noexcept {
        timer_ = timer;
        callback_ = callback;
        ctx_ = ctx;
        clock_alarm_.fire = &Timer32PeriodicAlarm::on_fire;
        clock_alarm_.ctx = this;
        timer->attach(this);
    }

    // Unlinks the alarm from the clock; the owner calls it before the alarm goes away.
    void detach() noexcept {
        if (timer_ != nullptr) timer_->clock()->cancel(&clock_alarm_);
    }

    bool enable() const noexcept { return enabled_; }
    void set_enable(bool value) noexcept {
        if (value == enabled_) return;
        enabled_ = value;
        if (value && timer_->enable()) {
            schedule();
        } else {
            cancel();
        }
    }

    int64_t target() const noexcept { return target_value_; }
    void set_target(int64_t value) noexcept {
        if (value == target_value_) return;
        target_value_ = value;
        if (enabled_ && timer_->enable()) {
            cancel();
            schedule();
        }
    }

    bool scheduled() const noexcept { return clock_alarm_.scheduled; }

    // The timer tells its alarms that the counter changed.
    void update() noexcept {
        cancel();
        if (enabled_ && timer_->enable()) schedule();
    }

    Timer32PeriodicAlarm* next_attached = nullptr;  // the timer's list

private:
    static bool on_fire(void* ctx) noexcept { return static_cast<Timer32PeriodicAlarm*>(ctx)->handle_alarm(); }

    bool handle_alarm() noexcept {
        if (!callback_(ctx_)) return false;  // a failed callback does not reschedule
        if (enabled_ && timer_->enable()) schedule();
        return true;
    }

    void schedule() noexcept {
        const int64_t top = timer_->top();
        const TimerMode mode = timer_->mode();
        const int64_t raw = timer_->raw_counter();

        int64_t cycle_delta = target_value_ - raw;
        if (mode == TimerMode::kZigzag && cycle_delta < 0) {
            if (cycle_delta < -top) {
                cycle_delta += 2 * top;
            } else {
                cycle_delta = top * 2 - target_value_ - raw;
            }
        }

        if (top != Timer32::kNoTop) {
            if (cycle_delta <= 0) cycle_delta += top + 1;
            if (target_value_ > top) return;  // skip: this target is never reached
        }

        if (mode == TimerMode::kDecrement) cycle_delta = top + 1 - cycle_delta;

        int64_t cycles_to_alarm = static_cast<int64_t>(static_cast<uint32_t>(cycle_delta));
        if (cycles_to_alarm == 0) cycles_to_alarm = top + 1;  // "now" is not a future alarm: a zero delay livelocks the clock
        timer_->clock()->schedule(&clock_alarm_, timer_->to_nanos(cycles_to_alarm));
    }

    void cancel() noexcept { timer_->clock()->cancel(&clock_alarm_); }

    Timer32* timer_ = nullptr;
    Callback callback_ = nullptr;
    void* ctx_ = nullptr;
    int64_t target_value_ = 0;
    bool enabled_ = false;
    Alarm clock_alarm_;
};

inline void Timer32::attach(Timer32PeriodicAlarm* alarm) noexcept {
    alarm->next_attached = nullptr;
    if (last_ != nullptr) {
        last_->next_attached = alarm;
    } else {
        first_ = alarm;
    }
    last_ = alarm;
}

inline void Timer32::updated() noexcept {
    for (Timer32PeriodicAlarm* alarm = first_; alarm != nullptr; alarm = alarm->next_attached) alarm->update();
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_TIMER32_HPP
