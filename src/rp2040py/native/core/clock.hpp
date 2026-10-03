// The simulated clock and alarm scheduler of the C++ MCU core (docs/records/0096-cpp-mcu-core.md, Phase 2).
//
// A faithful translation of clock/_simulation_clock.py (the pure-Python reference, kept as the oracle): time
// is a double count of nanoseconds, advanced by `tick(delta)`, and alarms are a list sorted by due time that
// `tick` fires in order, setting the clock to each alarm's own time just before its callback runs.
//
// Allocation-free by construction: an `Alarm` is a node the CALLER owns (the Cython `ClockAlarm` embeds one),
// linked into the clock's list by pointer; the clock never allocates or frees. That puts one rule on the
// caller - an alarm must be unlinked (cancel) before its storage goes away - and the Cython wrapper keeps an
// alarm alive for exactly as long as it is linked.
//
// A callback is a plain function pointer + `ctx`, so a Python callable (through a Cython trampoline) and a C++
// block's own function are the same thing to the clock. It returns whether ticking may continue: `false` means
// "I failed and the failure is pending with the caller" - the Python trampoline cannot throw through this
// frame - and `tick` returns at once, leaving the clock at that alarm's time (exactly where an exception raised
// from the callback leaves the pure-Python clock).
//
// Header-only, C++17, no exceptions/RTTI/STL. Single-threaded by contract.
#ifndef RP2040PY_CORE_CLOCK_HPP
#define RP2040PY_CORE_CLOCK_HPP

namespace rp2040core {

using AlarmFireFn = bool (*)(void* ctx);  // false: stop ticking, a failure is pending with the caller

struct Alarm {
    Alarm* next = nullptr;
    double nanos = 0.0;       // absolute due time while linked
    bool scheduled = false;   // linked into a clock's list
    AlarmFireFn fire = nullptr;
    void* ctx = nullptr;
};

class Clock {
public:
    double frequency = 125e6;

    double nanos() const noexcept { return now_; }
    bool has_alarm() const noexcept { return head_ != nullptr; }
    // Time until the next alarm is due, or 0 when there is none (has_alarm() tells the two apart).
    double nanos_to_next_alarm() const noexcept { return head_ != nullptr ? head_->nanos - now_ : 0.0; }

    // Links `alarm` to fire `delta` nanoseconds from now. An alarm with the same due time as ones already in
    // the list goes AFTER them (FIFO): a zero-delay producer that reschedules itself must never cut in front
    // of a pending zero-delay consumer, or it can starve it (docs/records/0044).
    void link(Alarm* alarm, double delta) noexcept {
        alarm->nanos = now_ + delta;
        Alarm* item = head_;
        Alarm* last = nullptr;
        while (item != nullptr && item->nanos <= alarm->nanos) {
            last = item;
            item = item->next;
        }
        if (last != nullptr) {
            last->next = alarm;
        } else {
            head_ = alarm;
        }
        alarm->next = item;
        alarm->scheduled = true;
    }

    // Removes `alarm` from the list if it is there; returns whether it was. Does not touch `scheduled`.
    bool unlink(Alarm* alarm) noexcept {
        Alarm* item = head_;
        Alarm* last = nullptr;
        while (item != nullptr) {
            if (item == alarm) {
                if (last != nullptr) {
                    last->next = item->next;
                } else {
                    head_ = item->next;
                }
                return true;
            }
            last = item;
            item = item->next;
        }
        return false;
    }

    // (Re)schedules: an alarm already linked is moved, not duplicated.
    void schedule(Alarm* alarm, double delta) noexcept {
        if (alarm->scheduled) cancel(alarm);
        link(alarm, delta);
    }

    void cancel(Alarm* alarm) noexcept {
        unlink(alarm);
        alarm->scheduled = false;
    }

    // Advances time by `delta`, firing every alarm that comes due, in order, each at its own time. The list
    // is re-read after every callback (a callback may schedule or cancel anything). Returns false if a
    // callback asked to stop; the clock is then at that alarm's time and the rest of `delta` is not applied.
    bool tick(double delta) noexcept {
        const double target = now_ + delta;
        while (head_ != nullptr && head_->nanos <= target) {
            Alarm* alarm = head_;
            head_ = alarm->next;
            alarm->scheduled = false;  // it is no longer in the list; a callback may re-arm it
            now_ = alarm->nanos;
            if (!alarm->fire(alarm->ctx)) return false;
        }
        now_ = target;
        return true;
    }

private:
    double now_ = 0.0;
    Alarm* head_ = nullptr;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_CLOCK_HPP
