// The RP2040 WATCHDOG block (datasheet 4.7) in C++ (docs/records/0096-cpp-mcu-core.md): the reset-cause bookkeeping (REASON, eight scratch registers), the countdown on a `Timer32` and the tick
// generator's registers. The pure-Python reference is `peripherals/_watchdog.py`; the lockstep differential of `tests/test_watchdog_diff.py` holds the two to the same behaviour.
//
//   - the countdown is a DECREMENT `Timer32` at 2 MHz (1 MHz tick, decremented twice per tick: errata RP2040-E1) with one compare alarm at 0; LOAD (24 bits) sets the counter; CTRL.ENABLE and
//     TICK.ENABLE together run it (`timer.enable` and `alarm.enable` follow `enable && tick_enable`); CTRL reads ENABLE from the timer, the three PAUSE bits (reset 1, stored) and TIME (the counter);
//   - the alarm reaching 0 sets REASON = TIMER and calls the host's `trigger` (the reference's `on_watchdog_trigger`: the chip's reset, installed by the device); CTRL.TRIGGER sets REASON = FORCE
//     and calls the same, before the enables are updated - a failing trigger leaves them as they were;
//   - TICK keeps CYCLES (8:0, reset 0) and ENABLE (reset 1) and reads RUNNING (10) with ENABLE; COUNT (19:11) reads 0. The tick does not retune the counter, and the TIMER does not wait for it
//     (both written up in 0098);
//   - REASON is read-only: a write to it is ignored, silently. LOAD is write-only in the datasheet (type WF) and a read of it warns "unimplemented", as the reference's does (the value a read
//     should return is not stated);
//   - `reset()` clears REASON and the scratch registers only (a RUN-pin/power-on reset; a watchdog reset must not call it - 0089); an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a
//     write; an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. An alarm is a node the block holds, so the block must not move after `init()`, and `detach()` must run before it goes.
#ifndef RP2040PY_CORE_WATCHDOG_HPP
#define RP2040PY_CORE_WATCHDOG_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"
#include "timer32.hpp"

namespace rp2040core {

constexpr uint32_t kWatchdogWarnRead = kRegWarnRead, kWatchdogWarnReadAtomicArea = kRegWarnReadAtomicArea, kWatchdogWarnWrite = kRegWarnWrite;

using WatchdogTriggerFn = bool (*)(void* ctx);  // the chip's reset; false: the failure is parked with the host

struct WatchdogHost {
    RegWarnFn warn = nullptr;
    WatchdogTriggerFn trigger = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace watchdog_regs {
constexpr uint32_t REG_CTRL = 0x00, REG_LOAD = 0x04, REG_REASON = 0x08, SCRATCH0 = 0x0C, SCRATCH7 = 0x28, REG_TICK = 0x2C;
constexpr uint32_t TRIGGER = 1u << 31, ENABLE = 1u << 30, PAUSE_DBG1 = 1u << 26, PAUSE_DBG0 = 1u << 25, PAUSE_JTAG = 1u << 24, TIME_MASK = 0xFFFFFFu;
constexpr uint32_t LOAD_MASK = 0xFFFFFFu;
constexpr uint32_t REASON_FORCE = 1u << 1, REASON_TIMER = 1u << 0;
constexpr uint32_t TICK_RUNNING = 1u << 10, TICK_ENABLE = 1u << 9, CYCLES_MASK = 0x1FFu;
constexpr double TICK_FREQUENCY = 2000000.0;  // 1 MHz, decremented twice per tick (RP2040-E1)
}  // namespace watchdog_regs

class WatchdogBlock {
public:
    WatchdogBlock() = default;
    WatchdogBlock(const WatchdogBlock&) = delete;  // the alarm and the clock hold pointers into this object
    WatchdogBlock& operator=(const WatchdogBlock&) = delete;

    Timer32 timer;
    Timer32PeriodicAlarm alarm;
    uint32_t scratch[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    uint32_t reason = 0;
    uint32_t tick_cycles = 0;
    bool enable = false;
    bool tick_enable = true;
    bool pause_dbg0 = true, pause_dbg1 = true, pause_jtag = true;

    // Binds the block to the chip's clock and the host, and runs the reference's constructor: the timer counts down at TICK_FREQUENCY, stopped, with an alarm at 0, disabled.
    void init(Clock* clock, const WatchdogHost& host) noexcept {
        host_ = host;
        timer.init(clock, watchdog_regs::TICK_FREQUENCY);
        timer.set_mode(TimerMode::kDecrement);
        timer.set_enable(false);
        alarm.init(&timer, &WatchdogBlock::on_alarm, this);
        alarm.set_target(0);
        alarm.set_enable(false);
    }

    // Unlinks the alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept { alarm.detach(); }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // What the timeout does: REASON = TIMER, then the chip's reset. The alarm calls it; the shell exposes it as `alarm.callback` (the tests call it by hand).
    bool fire_timeout() noexcept {
        reason = watchdog_regs::REASON_TIMER;
        return trigger();
    }

    bool reset() noexcept {
        reason = 0;
        for (uint32_t& word : scratch) word = 0;
        return true;
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace watchdog_regs;
        if (offset == REG_CTRL) {
            return (timer.enable() ? ENABLE : 0u) | (pause_dbg0 ? PAUSE_DBG0 : 0u) | (pause_dbg1 ? PAUSE_DBG1 : 0u) | (pause_jtag ? PAUSE_JTAG : 0u) | (timer.counter() & TIME_MASK);
        }
        if (offset == REG_REASON) return reason;
        if (offset >= SCRATCH0 && offset <= SCRATCH7 && (offset & 3) == 0) return scratch[(offset - SCRATCH0) >> 2];
        if (offset == REG_TICK) return tick_cycles | (tick_enable ? (TICK_RUNNING | TICK_ENABLE) : 0u);
        if (host_.warn) {
            host_.warn(host_.ctx, kWatchdogWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kWatchdogWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace watchdog_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        if (offset == REG_CTRL) {
            if (v & TRIGGER) {
                reason = REASON_FORCE;
                if (!trigger()) return false;
            }
            enable = (v & ENABLE) != 0;
            timer.set_enable(enable && tick_enable);
            alarm.set_enable(enable && tick_enable);
            pause_dbg0 = (v & PAUSE_DBG0) != 0;
            pause_dbg1 = (v & PAUSE_DBG1) != 0;
            pause_jtag = (v & PAUSE_JTAG) != 0;
        } else if (offset == REG_LOAD) {
            timer.set(v & LOAD_MASK);
        } else if (offset == REG_REASON) {
            // read-only: no effect
        } else if (offset >= SCRATCH0 && offset <= SCRATCH7 && (offset & 3) == 0) {
            scratch[(offset - SCRATCH0) >> 2] = v;
        } else if (offset == REG_TICK) {
            tick_enable = (v & TICK_ENABLE) != 0;
            tick_cycles = v & CYCLES_MASK;
            timer.set_enable(enable && tick_enable);
            alarm.set_enable(enable && tick_enable);
        } else if (host_.warn) {
            host_.warn(host_.ctx, kWatchdogWarnWrite, offset, value);
        }
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        if (failed()) return false;
        return write(offset, value);
    }

    WindowHandler window_handler() noexcept { return BlockWindow<WatchdogBlock>::handler(this); }

private:
    static bool on_alarm(void* ctx) noexcept { return static_cast<WatchdogBlock*>(ctx)->fire_timeout(); }

    bool trigger() noexcept { return host_.trigger == nullptr || host_.trigger(host_.ctx); }

    WatchdogHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_WATCHDOG_HPP
