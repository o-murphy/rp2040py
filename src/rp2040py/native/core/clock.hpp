// The simulated clock and alarm scheduler of the C++ MCU core (docs/records/0096-cpp-mcu-core.md, Phase 2; docs/records/0100-integer-clock.md).
//
// A faithful translation of clock/_simulation_clock.py (the pure-Python reference, kept as the oracle): time
// is an INTEGER count of ticks, advanced by `tick_ticks(delta)`, and alarms are a list sorted by due time that
// `tick` fires in order, setting the clock to each alarm's own time just before its callback runs.
//
// A tick is 1/16 ns (`kTicksPerNano`): an `int64_t` then holds 18 years of simulated time, exactly, where the `double` nanoseconds this
// replaced stopped resolving 8 ns beyond 104 days (the `rp2_dma` failure of the official suite). A 125 MHz cycle is exactly 128 ticks.
// The `double`-nanosecond entry points (`tick(delta)`, `link(alarm, delta)`, `nanos()`) stay for the blocks that think in
// nanoseconds: a delta is converted to ticks once, by `nanos_to_ticks` (round half up, saturating, NaN as 0), the same in the Python
// clocks. A per-cycle period that is not a whole number of ticks (133 MHz is 120.30 ticks) is carried as a remainder instead of rounded each time:
// `advance_cycles`.
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

#include <cmath>
#include <cstdint>
#include "inline_hints.hpp"

namespace rp2040core {

constexpr int64_t kTicksPerNano = 16;
constexpr int64_t kMaxTicks = INT64_MAX / 2;  // "never": times saturate here instead of wrapping (the clock records it, `saturated()`)
// An idle chip fast-forwards: with nothing waiting on the outside world the clock jumps from alarm to alarm, and a periodic one (the RTC's second) makes that millions of simulated seconds per
// wall second - the whole `int64` in half a minute (found by the MicroPython `micropython/` tests, record 0100). So the origin MOVES: when the time passes kRebaseAt the clock subtracts it from `now`,
// from every linked alarm and - through the listeners - from every absolute tick count a block holds, and carries the total apart (`rebased_ticks()`), so `nanos()` still never goes back.
constexpr int64_t kRebaseAt = int64_t{1} << 58;  // 9e6 s, a little over 100 days
constexpr int64_t kNoHorizon = INT64_MAX;  // no alarm linked: time may run on without ever having to fire one
constexpr int64_t kLazyDelta = int64_t{1} << 40;  // the largest step `defer_ticks` takes on its fast path: 2^40 ticks, ~69 s of simulated time (16 ticks per ns), far above any instruction's
using RebaseFn = void (*)(void* ctx, int64_t delta);

class Clock;

// A holder of an absolute tick count (a block's epoch) owns one of these and registers it with the clock; like an `Alarm` it is linked by pointer, the clock never allocates, and a clock that goes first
// (the garbage collector frees a cycle in no particular order) lets go of every listener, so the holder's later `remove_rebase_listener()` is a no-op instead of a write through a dangling pointer.
struct RebaseListener {
    RebaseListener* next = nullptr;
    Clock* owner = nullptr;
    RebaseFn fn = nullptr;
    void* ctx = nullptr;
    RebaseListener() = default;
    RebaseListener(const RebaseListener&) = delete;
    RebaseListener& operator=(const RebaseListener&) = delete;
    ~RebaseListener();  // a holder that goes (freed by the garbage collector, without a detach) takes itself off its clock
};

// A delta in nanoseconds as ticks: round half up (but a positive delta is at least one tick), saturating at +-kMaxTicks, NaN as 0 - exactly `_simulation_clock.nanos_to_ticks`.
inline int64_t nanos_to_ticks(double nanos) noexcept {
    if (nanos != nanos) return 0;
    const double ticks = nanos * static_cast<double>(kTicksPerNano);
    if (ticks >= static_cast<double>(kMaxTicks)) return kMaxTicks;
    if (ticks <= -static_cast<double>(kMaxTicks)) return -kMaxTicks;
    const int64_t rounded = static_cast<int64_t>(std::floor(ticks + 0.5));
    // A delay that is positive stays at least one tick: an alarm that re-arms itself with a vanishing positive delay must still move time forward, or the clock would fire it
    // again at the very same instant, for ever (the double clock moved by the sliver, however small).
    return rounded == 0 && ticks > 0.0 ? 1 : rounded;
}

using AlarmFireFn = bool (*)(void* ctx);  // false: stop ticking, a failure is pending with the caller

class Clock;

struct Alarm {
    Alarm* next = nullptr;
    Clock* owner = nullptr;   // the clock whose list this alarm is linked into (null when not linked)
    int64_t ticks = 0;        // absolute due time while linked
    bool scheduled = false;   // linked into a clock's list
    AlarmFireFn fire = nullptr;
    void* ctx = nullptr;
};

class Clock {
public:
    double frequency = 125e6;

    Clock() = default;
    Clock(const Clock&) = delete;
    Clock& operator=(const Clock&) = delete;

    // The clock may go away before the alarms linked into it (the garbage collector frees a cycle in no particular order): every
    // still-linked alarm is told it has no clock any more, so a later `cancel_alarm()` on it is a no-op instead of a write
    // through a dangling pointer. The alarm's owner needs no keep-alive reference to the clock for this.
    ~Clock() { reset(); }

    // Back to a freshly constructed clock: time 0, default frequency, no alarm linked (each one released as the destructor does).
    void reset() noexcept {
        while (head_ != nullptr) {
            Alarm* alarm = head_;
            head_ = alarm->next;
            alarm->next = nullptr;
            alarm->owner = nullptr;
            alarm->scheduled = false;
        }
        while (rebase_head_ != nullptr) {  // let go of the listeners, as of the alarms
            RebaseListener* listener = rebase_head_;
            rebase_head_ = listener->next;
            listener->next = nullptr;
            listener->owner = nullptr;
        }
        now_ = 0;
        pending_ = 0;
        horizon_ = kNoHorizon;
        frequency = 125e6;
        cycle_remainder_ = 0.0;
        saturated_ = false;
        rebased_ticks_ = 0.0;
    }

    // A holder of an absolute tick count (a block's epoch, a device's last edge) registers a listener here to have `delta` subtracted when the origin moves. Registering twice is a no-op.
    void add_rebase_listener(RebaseListener* listener, RebaseFn fn, void* ctx) noexcept {
        if (listener->owner == this) return;
        listener->fn = fn;
        listener->ctx = ctx;
        listener->owner = this;
        listener->next = rebase_head_;
        rebase_head_ = listener;
    }
    // Takes the listener off its clock, if it is still on one.
    static void remove_rebase_listener(RebaseListener* listener) noexcept {
        Clock* clock = listener->owner;
        if (clock == nullptr) return;
        RebaseListener** link = &clock->rebase_head_;
        while (*link != nullptr && *link != listener) link = &(*link)->next;
        if (*link != nullptr) *link = listener->next;
        listener->next = nullptr;
        listener->owner = nullptr;
    }

    // Moves the origin: `delta` is subtracted from the time, from every linked alarm (a "never" alarm stays at the end) and, through the listeners, from every absolute count a block holds. Not from inside an
    // alarm's callback (the list is being walked); `tick_ticks` does it when it has finished.
    void rebase(int64_t delta) noexcept {
        settle();
        if (delta <= 0 || delta > now_) return;
        now_ -= delta;
        rebased_ticks_ += static_cast<double>(delta);
        for (Alarm* alarm = head_; alarm != nullptr; alarm = alarm->next) {
            if (alarm->ticks < kMaxTicks) alarm->ticks -= delta;
        }
        for (RebaseListener* listener = rebase_head_; listener != nullptr; listener = listener->next) listener->fn(listener->ctx, delta);
    }

    // Every reader of the time below first brings the clock up to date (`settle`): the batch loop of a CPU running alone does not tick the clock after each instruction, it DEFERS the time (`defer_ticks`) and the
    // clock settles it when somebody looks, or when an alarm is due. The only way to learn the time, or to measure a delay from it, is through this class, so no caller can see a stale time.
    int64_t ticks() const noexcept {
        settle();
        return now_;
    }
    // The time in nanoseconds, for display and for the blocks that think in them (inexact beyond 2^53 / 16 ns; differences of it should be taken in ticks).
    double nanos() const noexcept {
        settle();
        return (static_cast<double>(now_) + rebased_ticks_) / static_cast<double>(kTicksPerNano);
    }
    // The ticks the origin has been moved by so far (a double: it is for display, and `nanos()` stays monotonic across a move).
    double rebased_ticks() const noexcept { return rebased_ticks_; }
    bool has_alarm() const noexcept { return head_ != nullptr; }
    // Time until the next alarm is due, or 0 when there is none (has_alarm() tells the two apart).
    int64_t ticks_to_next_alarm() const noexcept {
        settle();
        return head_ != nullptr ? head_->ticks - now_ : 0;
    }
    double nanos_to_next_alarm() const noexcept { return static_cast<double>(ticks_to_next_alarm()) / static_cast<double>(kTicksPerNano); }
    // Whether a time was ever clamped to kMaxTicks (an alarm "never" due, or a jump beyond 146 years) since the last reset.
    bool saturated() const noexcept { return saturated_; }

    // Links `alarm` to fire `delta` nanoseconds from now. An alarm with the same due time as ones already in
    // the list goes AFTER them (FIFO): a zero-delay producer that reschedules itself must never cut in front
    // of a pending zero-delay consumer, or it can starve it (docs/records/0044).
    void link_ticks(Alarm* alarm, int64_t delta) noexcept {
        settle();
        alarm->ticks = add_ticks(now_, delta);
        Alarm* item = head_;
        Alarm* last = nullptr;
        while (item != nullptr && item->ticks <= alarm->ticks) {
            last = item;
            item = item->next;
        }
        if (last != nullptr) {
            last->next = alarm;
        } else {
            head_ = alarm;
        }
        alarm->next = item;
        alarm->owner = this;
        alarm->scheduled = true;
        refresh_horizon();
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
                item->owner = nullptr;
                refresh_horizon();
                return true;
            }
            last = item;
            item = item->next;
        }
        return false;
    }

    void link(Alarm* alarm, double delta_nanos) noexcept { link_ticks(alarm, to_ticks(delta_nanos)); }

    // (Re)schedules: an alarm already linked is moved, not duplicated.
    void schedule_ticks(Alarm* alarm, int64_t delta) noexcept {
        if (alarm->scheduled) cancel(alarm);
        link_ticks(alarm, delta);
    }
    void schedule(Alarm* alarm, double delta_nanos) noexcept { schedule_ticks(alarm, to_ticks(delta_nanos)); }

    void cancel(Alarm* alarm) noexcept {
        unlink(alarm);
        alarm->scheduled = false;
    }

    // Advances time by `delta`, firing every alarm that comes due, in order, each at its own time. The list
    // is re-read after every callback (a callback may schedule or cancel anything). Returns false if a
    // callback asked to stop; the clock is then at that alarm's time and the rest of `delta` is not applied.
    bool tick_ticks(int64_t delta) noexcept {
        settle();
        const bool ok = tick_now(delta);
        refresh_horizon();
        return ok;
    }

    // The batch loop's way of letting time pass: records `delta` ticks as run but not yet applied. Returns what `tick_ticks` would: false if an alarm that came due asked to stop. Nothing is applied
    // until an alarm is due (`pending_` reaches `horizon_`, the time to the next alarm) or somebody reads the time, so a long stretch of instructions that touches no timing-dependent block costs one
    // addition and one comparison each. Equivalent to `tick_ticks(delta)` after every instruction: an alarm fires at the end of the very instruction that makes it due, and every read sees the
    // time as of the end of the previous one.
    RP2040PY_ALWAYS_INLINE bool defer_ticks(int64_t delta) noexcept {
        if (delta < 0 || delta >= kLazyDelta) return tick_ticks(delta);
        pending_ += delta;
        if (pending_ < horizon_) return true;
        return flush();
    }

    // The ticks deferred by `defer_ticks` and not applied yet (0 once settled).
    int64_t pending_ticks() const noexcept { return pending_; }

    bool tick(double delta_nanos) noexcept { return tick_ticks(to_ticks(delta_nanos)); }

    // Advances by `cycles` periods of `ticks_per_cycle` ticks each (not a whole number in general: 120.30 at 133 MHz) with the fraction CARRIED between calls, so
    // the long-run rate is exact however the cycles are split into calls - the CPU's time, one call per instruction.
    bool advance_cycles(int64_t cycles, double ticks_per_cycle) noexcept {
        cycle_remainder_ += static_cast<double>(cycles) * ticks_per_cycle;
        const double whole = std::floor(cycle_remainder_);
        cycle_remainder_ -= whole;
        return tick_ticks(whole >= static_cast<double>(kMaxTicks) ? kMaxTicks : static_cast<int64_t>(whole));
    }

private:
    // Applies the deferred ticks. False if an alarm that came due asked to stop (the failure is pending with the caller, as for `tick_ticks`).
    bool flush() noexcept {
        const int64_t delta = pending_;
        pending_ = 0;
        const bool ok = tick_now(delta);
        refresh_horizon();
        return ok;
    }
    // For the readers: a failure of an alarm that fires here is parked by its owner (the flag the CPU checks after every host call), so the verdict is not needed.
    RP2040PY_ALWAYS_INLINE void settle() const noexcept {
        if (pending_ != 0) const_cast<Clock*>(this)->flush();
    }
    // The time the clock may run, from `now_`, before an alarm is due.
    void refresh_horizon() noexcept { horizon_ = head_ != nullptr ? head_->ticks - now_ : kNoHorizon; }

    RP2040PY_ALWAYS_INLINE bool tick_now(int64_t delta) noexcept {
        ++depth_;
        const int64_t target = add_ticks(now_, delta);
        while (head_ != nullptr && head_->ticks <= target) {
            Alarm* alarm = head_;
            head_ = alarm->next;
            alarm->owner = nullptr;
            alarm->scheduled = false;  // it is no longer in the list; a callback may re-arm it
            if (alarm->ticks > now_) now_ = alarm->ticks;  // a callback may have ticked on itself: time never goes back
            if (!alarm->fire(alarm->ctx)) {
                --depth_;
                return false;
            }
        }
        if (delta < 0 || target > now_) now_ = target;  // (a callback that ticked on itself may have gone past it already)
        --depth_;
        if (depth_ == 0 && now_ >= kRebaseAt) rebase(now_);  // the outermost tick only: nothing is walking the list
        return true;
    }
    // `nanos_to_ticks`, recording a clamp.
    int64_t to_ticks(double nanos) noexcept {
        const int64_t ticks = nanos_to_ticks(nanos);
        if (ticks == kMaxTicks || ticks == -kMaxTicks) saturated_ = true;
        return ticks;
    }

    int64_t add_ticks(int64_t a, int64_t b) noexcept {
        if (b > 0 && a > kMaxTicks - b) {
            saturated_ = true;
            return kMaxTicks;
        }
        return a + b;
    }

    int64_t now_ = 0;
    int64_t pending_ = 0;           // ticks deferred by `defer_ticks`, not yet applied to `now_` (always below `horizon_` while at rest)
    int64_t horizon_ = kNoHorizon;  // `head_->ticks - now_`: how far the deferred time may grow before the first alarm is due
    double cycle_remainder_ = 0.0;
    double rebased_ticks_ = 0.0;
    bool saturated_ = false;
    int depth_ = 0;  // nested `tick_ticks` calls: the origin only moves at depth 0
    RebaseListener* rebase_head_ = nullptr;
    Alarm* head_ = nullptr;
};

// Unlinks `alarm` from whatever clock it is linked into, if that clock still exists. What an owner's `detach()` calls: unlike
// `clock->cancel(alarm)` it is safe when the clock was destroyed first.
inline RebaseListener::~RebaseListener() { Clock::remove_rebase_listener(this); }

inline void cancel_alarm(Alarm* alarm) noexcept {
    if (alarm->owner != nullptr) alarm->owner->cancel(alarm);
    alarm->scheduled = false;
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_CLOCK_HPP
