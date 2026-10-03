// The RP2040 TIMER block in C++ (docs/records/0096-cpp-mcu-core.md, Phase 2): a faithful translation of
// peripherals/timer.py, which stays as the pure-Python reference and the oracle.
//
// What it owns: the microsecond counter's epoch, the latched high word, INTR/INTE/INTF, PAUSE, and four alarms
// whose due times are scheduled on the C++ `Clock` (core/clock.hpp) - so a firmware's TIMELR poll loop, the
// hottest register traffic of a boot (52% of the accesses of a MicroPython boot, 97.6% of CircuitPython's), never
// leaves C++. What it does not own: logging, and the interrupt line - both are reached through a `TimerHost` of
// plain function pointers (a Python trampoline today, the C++ NVIC/logger later), which also keeps this header free
// of anything Python.
//
// Every observable is kept, because replay of a recorded firmware session is the acceptance test: the number and
// order of interrupt-line calls (every INTR/INTE/INTF change and every alarm fire re-announces ALL FOUR lines, in
// order), which register accesses warn and with what, and the `raw_write_value` the alias-write path hands to INTR
// and ARMED.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_TIMER_HPP
#define RP2040PY_CORE_TIMER_HPP

#include <cstdint>

#include "clock.hpp"
#include "window_map.hpp"

namespace rp2040core {

// A function that reports an interrupt line level may fail (a Python trampoline can raise); `false` means the
// failure is pending with the caller, and whatever was running stops at once - as an exception would stop it.
using TimerIrqFn = bool (*)(void* ctx, uint32_t line, bool level);

// The messages the Python block logs through `BasePeripheral.warn`; the host formats them (it owns the logger).
enum TimerWarn : uint32_t {
    kTimerWarnRead = 0,            // "Unimplemented peripheral read from 0x{offset:x}"
    kTimerWarnReadAtomicArea = 1,  // "Unimplemented read from peripheral in the atomic operation region" (offset > 0x1000)
    kTimerWarnWrite = 2,           // "Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}"
    kTimerWarnPause = 3,           // "Unimplemented Timer Pause"
};
using TimerWarnFn = void (*)(void* ctx, uint32_t kind, uint32_t offset, int64_t value);

struct TimerHost {
    TimerIrqFn irq = nullptr;
    TimerWarnFn warn = nullptr;
    void* ctx = nullptr;
    uint32_t lines[4] = {0, 1, 2, 3};  // IRQ numbers of TIMER_0..3
};

namespace timer_regs {
constexpr uint32_t TIMEHR = 0x08, TIMELR = 0x0C, ALARM0 = 0x10, ALARM1 = 0x14, ALARM2 = 0x18, ALARM3 = 0x1C;
constexpr uint32_t ARMED = 0x20, TIMERAWH = 0x24, TIMERAWL = 0x28, PAUSE = 0x30;
constexpr uint32_t INTR = 0x34, INTE = 0x38, INTF = 0x3C, INTS = 0x40;
}  // namespace timer_regs

class TimerBlock {
public:
    static constexpr int kAlarms = 4;
    static constexpr uint32_t kAlarmBits = 0xF;

    TimerBlock() = default;
    TimerBlock(const TimerBlock&) = delete;  // the alarm nodes point into this object
    TimerBlock& operator=(const TimerBlock&) = delete;

    // Binds the block to its clock and host. Must be called once, before anything else.
    void init(Clock* clock, const TimerHost& host) noexcept {
        clock_ = clock;
        host_ = host;
        for (int i = 0; i < kAlarms; ++i) {
            slots_[i].block = this;
            slots_[i].index = i;
            alarms_[i].fire = &TimerBlock::on_alarm;
            alarms_[i].ctx = &slots_[i];
        }
    }

    // Unlinks every alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept {
        if (clock_ == nullptr) return;
        for (int i = 0; i < kAlarms; ++i) clock_->cancel(&alarms_[i]);
    }

    uint32_t int_status() const noexcept { return (int_raw_ & int_enable_) | int_force_; }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }
    bool alarm_armed(int index) const noexcept { return armed_[index]; }

    // ---- registers: every "handled" return is false for an offset this block does not implement ----------

    bool read32(uint32_t offset, uint32_t* out) noexcept {
        using namespace timer_regs;
        const double time = micros();
        switch (offset) {
            case TIMEHR: *out = static_cast<uint32_t>(latched_high_); return true;
            case TIMELR:
                latched_high_ = high_word(time);
                *out = to_uint32(time);
                return true;
            case TIMERAWH: *out = static_cast<uint32_t>(high_word(time)); return true;
            case TIMERAWL: *out = to_uint32(time); return true;
            case ALARM0: case ALARM1: case ALARM2: case ALARM3:
                *out = target_[(offset - ALARM0) / 4];
                return true;
            case PAUSE: *out = paused_ ? 1u : 0u; return true;
            case INTR: *out = int_raw_; return true;
            case INTE: *out = int_enable_; return true;
            case INTF: *out = int_force_; return true;
            case INTS: *out = int_status(); return true;
            case ARMED: {
                uint32_t result = 0;
                for (int i = 0; i < kAlarms; ++i) {
                    if (armed_[i]) result |= 1u << i;
                }
                *out = result;
                return true;
            }
            default: return false;
        }
    }

    // What write32() reports. Plain ints, not an enum class, so the Cython shell can compare them directly.
    static constexpr int kWriteHandled = 0;
    static constexpr int kWriteUnhandled = 1;
    static constexpr int kWriteFailed = 2;

    // `value` is the value after any atomic-alias decode; `raw` is the value as the guest wrote it (INTR and ARMED
    // act on the raw bits, exactly as the Python block does through `raw_write_value`).
    int write32(uint32_t offset, int64_t value, int64_t raw) noexcept {
        using namespace timer_regs;
        switch (offset) {
            case ALARM0: case ALARM1: case ALARM2: case ALARM3: {
                const int index = static_cast<int>((offset - ALARM0) / 4);
                // The due time is computed from the unmasked value (as the Python block does); the register keeps 32 bits.
                const uint32_t delta_micros = to_uint32(static_cast<double>(value) - micros());
                armed_[index] = true;
                target_[index] = static_cast<uint32_t>(value);
                clock_->schedule(&alarms_[index], static_cast<double>(delta_micros) * 1000.0);
                return kWriteHandled;
            }
            case ARMED:
                for (int i = 0; i < kAlarms; ++i) {
                    if (raw & (int64_t{1} << i)) disarm(i);
                }
                return kWriteHandled;
            case PAUSE:
                paused_ = (value & 1) != 0;
                if (paused_ && host_.warn) host_.warn(host_.ctx, kTimerWarnPause, offset, value);
                return kWriteHandled;
            case INTR:
                int_raw_ &= ~static_cast<uint32_t>(raw);
                return check_interrupts() ? kWriteHandled : kWriteFailed;
            case INTE:
                int_enable_ = static_cast<uint32_t>(value) & kAlarmBits;
                return check_interrupts() ? kWriteHandled : kWriteFailed;
            case INTF:
                int_force_ = static_cast<uint32_t>(value) & kAlarmBits;
                return check_interrupts() ? kWriteHandled : kWriteFailed;
            default: return kWriteUnhandled;
        }
    }

    // Registers and all four alarms back to power-on; the count restarts from zero by moving the epoch to now
    // (the simulation clock itself is never rewound). Returns false if an interrupt-line call failed.
    bool reset() noexcept {
        epoch_nanos_ = clock_->nanos();
        latched_high_ = 0;
        int_raw_ = int_enable_ = int_force_ = 0;
        paused_ = false;
        for (int i = 0; i < kAlarms; ++i) {
            disarm(i);
            target_[i] = 0;
        }
        for (int i = 0; i < kAlarms; ++i) {
            if (!host_.irq(host_.ctx, host_.lines[i], false)) return false;
        }
        return true;
    }

    // ---- the register protocol as the bus (and the Python facade) uses it ---------------------------------

    // A read of any offset: an unimplemented one warns and reads as 0xFFFFFFFF, as `BasePeripheral` does.
    uint32_t read(uint32_t offset) noexcept {
        uint32_t value;
        if (read32(offset, &value)) return value;
        if (host_.warn) {
            host_.warn(host_.ctx, kTimerWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kTimerWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // A write through the atomic aliases: `BasePeripheral.write_uint32_atomic`, step for step - remember the raw
    // value, decode the alias against a *read* of the register (with that read's side effects), then write.
    void write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) {
            const int64_t current = static_cast<int64_t>(read(offset));
            switch (atomic_type) {
                case kAtomicXor: value = current ^ raw; break;
                case kAtomicSet: value = current | raw; break;
                case kAtomicClear: value = current & ~raw; break;
                default: break;  // not reachable from an address: the Python block logs a warning and writes raw
            }
        }
        if (write32(offset, value, raw) == kWriteUnhandled && host_.warn) {
            host_.warn(host_.ctx, kTimerWarnWrite, offset, value);
        }
    }

    // ---- window handler entry points: what the bus's C++ window registry calls ---------------------------

    static uint32_t window_read32(void* ctx, uint32_t offset) {
        return static_cast<TimerBlock*>(ctx)->read(offset);
    }
    static void window_write32(void* ctx, uint32_t offset, int64_t raw_value, uint32_t atomic_type) {
        static_cast<TimerBlock*>(ctx)->write_atomic(offset, raw_value, atomic_type);
    }
    WindowHandler window_handler() noexcept { return WindowHandler{&TimerBlock::window_read32, &TimerBlock::window_write32, this}; }

private:
    struct Slot {
        TimerBlock* block = nullptr;
        int index = 0;
    };

    // This block's own count in microseconds since its epoch - not the simulation's clock. Every read and every
    // alarm arming goes through here, so the two cannot disagree about what "now" is.
    double micros() const noexcept { return (clock_->nanos() - epoch_nanos_) / 1000.0; }
    static int64_t high_word(double time) noexcept { return static_cast<int64_t>(time / 4294967296.0); }  // time >= 0
    static uint32_t to_uint32(double value) noexcept { return static_cast<uint32_t>(static_cast<int64_t>(value)); }

    void disarm(int index) noexcept {
        clock_->cancel(&alarms_[index]);
        armed_[index] = false;
    }

    // Re-announces all four lines, in order, whatever changed - the Python block's exact call pattern.
    bool check_interrupts() noexcept {
        const uint32_t status = int_status();
        for (int i = 0; i < kAlarms; ++i) {
            if (!host_.irq(host_.ctx, host_.lines[i], (status & (1u << i)) != 0)) return false;
        }
        return true;
    }

    static bool on_alarm(void* ctx) noexcept {
        Slot* slot = static_cast<Slot*>(ctx);
        TimerBlock* self = slot->block;
        self->disarm(slot->index);
        self->int_raw_ |= 1u << slot->index;
        return self->check_interrupts();
    }

    Clock* clock_ = nullptr;
    TimerHost host_;
    double epoch_nanos_ = 0.0;
    int64_t latched_high_ = 0;
    uint32_t int_raw_ = 0, int_enable_ = 0, int_force_ = 0;
    bool paused_ = false;
    int64_t raw_write_value_ = 0;
    bool armed_[kAlarms] = {false, false, false, false};
    uint32_t target_[kAlarms] = {0, 0, 0, 0};
    Alarm alarms_[kAlarms];
    Slot slots_[kAlarms];
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_TIMER_HPP
