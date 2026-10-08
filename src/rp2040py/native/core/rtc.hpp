// The RP2040 RTC (datasheet 4.8) in C++ (docs/records/0096-cpp-mcu-core.md): a calendar counter that advances once per second of `clk_rtc / (CLKDIV_M1 + 1)`, and a match alarm on it. The pure-Python
// reference is `peripherals/_rtc.py` (its module docstring carries the datasheet and pico-sdk sources of every rule); the lockstep differential of `tests/test_rtc_diff.py` holds the two to the same
// behaviour.
//
//   - time is seven plain fields (year 12 bits ... second 6 bits) and is never checked: a field past its limit wraps at its next carry (second/minute >= 59, hour >= 23, month >= 12, day >= the
//     month's length, a month outside 1..12 has 31 days, a day of week >= 6 wraps to 0), as the reference does. A year divisible by 4 is a leap year unless CTRL.FORCE_NOTLEAPYEAR;
//   - the second is one alarm of the chip's C++ clock: it runs while the RTC is enabled *and* the chip told the block a non-zero `clk_rtc` (`clk_rtc_changed()`, from the CLOCKS block) - RTC_ACTIVE
//     (CTRL bit 1) reads the same condition, and MATCH_ACTIVE (IRQ_SETUP_0 bit 29) reads MATCH_ENA, both instant. CTRL.LOAD copies SETUP_0/1 into the counter at once, enabled or not, and starts a
//     new second; a change of CLKDIV_M1 or of clk_rtc does too, a CTRL write that only keeps the state does not;
//   - the raw interrupt (INTR bit 0) is a level: MATCH_ENA and every enabled field of IRQ_SETUP_0/1 equal to the counter. It is re-evaluated, and the host's `irq(level)` called with
//     ((raw | INTF) & INTE) & 1, after each second, a load, a write to IRQ_SETUP_0/1, INTE or INTF, and a reset;
//   - RTC_0 (offset 0x1C) read latches the date half; a read of RTC_1 returns the latch (the datasheet: "Reading RTC_0 latches the value of RTC_1");
//   - RTC_0, RTC_1, INTR and INTS are read-only: a write is ignored, silently. An unimplemented offset warns and reads 0xFFFFFFFF; an alias write decodes against a *read* of the register (so an alias
//     write to RTC_0 latches RTC_1), as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. The second is an alarm node the block holds, so the block must not move after `init()`, and `detach()` must run before it goes.
#ifndef RP2040PY_CORE_RTC_HPP
#define RP2040PY_CORE_RTC_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"

namespace rp2040core {

constexpr uint32_t kRtcWarnRead = kRegWarnRead, kRtcWarnReadAtomicArea = kRegWarnReadAtomicArea, kRtcWarnWrite = kRegWarnWrite;

using RtcIrqFn = bool (*)(void* ctx, bool level);  // the NVIC line (interrupt 25); false: the failure is parked with the host

struct RtcHost {
    RegWarnFn warn = nullptr;
    RtcIrqFn irq = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace rtc_regs {
constexpr uint32_t REG_CLKDIV_M1 = 0x00, REG_SETUP_0 = 0x04, REG_SETUP_1 = 0x08, REG_CTRL = 0x0C, REG_IRQ_SETUP_0 = 0x10, REG_IRQ_SETUP_1 = 0x14;
constexpr uint32_t REG_RTC_1 = 0x18, REG_RTC_0 = 0x1C, REG_INTR = 0x20, REG_INTE = 0x24, REG_INTF = 0x28, REG_INTS = 0x2C;
constexpr uint32_t CLKDIV_M1_MASK = 0xFFFF, SETUP_0_MASK = 0x00FFFF1Fu, SETUP_1_MASK = 0x071F3F3Fu;
constexpr uint32_t CTRL_FORCE_NOTLEAPYEAR = 1u << 8, CTRL_LOAD = 1u << 4, CTRL_RTC_ACTIVE = 1u << 1, CTRL_RTC_ENABLE = 1u << 0;
constexpr uint32_t IRQ_SETUP_0_MATCH_ACTIVE = 1u << 29, IRQ_SETUP_0_MATCH_ENA = 1u << 28, IRQ_SETUP_0_YEAR_ENA = 1u << 26, IRQ_SETUP_0_MONTH_ENA = 1u << 25, IRQ_SETUP_0_DAY_ENA = 1u << 24;
constexpr uint32_t IRQ_SETUP_0_MASK = 0x17FFFF1Fu;
constexpr uint32_t IRQ_SETUP_1_DOTW_ENA = 1u << 31, IRQ_SETUP_1_HOUR_ENA = 1u << 30, IRQ_SETUP_1_MIN_ENA = 1u << 29, IRQ_SETUP_1_SEC_ENA = 1u << 28;
constexpr uint32_t IRQ_SETUP_1_MASK = 0xF71F3F3Fu;
constexpr uint32_t INT_RTC = 1;

inline bool is_leap_year(uint32_t year, bool force_not_leap_year) noexcept { return year % 4 == 0 && !force_not_leap_year; }

inline uint32_t days_in_month(uint32_t month, bool leap) noexcept {
    if (month == 4 || month == 6 || month == 9 || month == 11) return 30;
    if (month == 2) return leap ? 29 : 28;
    return 31;
}
}  // namespace rtc_regs

class RtcBlock {
public:
    RtcBlock() = default;
    RtcBlock(const RtcBlock&) = delete;  // the alarm and the clock hold pointers into this object
    RtcBlock& operator=(const RtcBlock&) = delete;

    uint32_t clkdiv_m1 = 0, setup0 = 0, setup1 = 0, irq_setup0 = 0, irq_setup1 = 0, inte = 0, intf = 0;
    bool force_not_leap_year = false, enable = false;
    uint32_t year = 0, month = 0, day = 0, dotw = 0, hour = 0, minute = 0, second = 0;
    uint32_t latched_date = 0;  // what RTC_1 reads: the date half as of the last read of RTC_0

    void init(Clock* clock, const RtcHost& host) noexcept {
        clock_ = clock;
        host_ = host;
        alarm_.fire = &RtcBlock::on_second;
        alarm_.ctx = this;
    }

    // Unlinks the second from the clock; the owner calls it before the block goes away.
    void detach() noexcept { cancel_alarm(&alarm_); }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }
    double clk_rtc() const noexcept { return clk_rtc_; }

    // The counter is counting: enabled, and clk_rtc runs.
    bool running() const noexcept { return enable && clk_rtc_ > 0.0; }
    bool match_ena() const noexcept { return (irq_setup0 & rtc_regs::IRQ_SETUP_0_MATCH_ENA) != 0; }
    uint32_t ctrl() const noexcept {
        using namespace rtc_regs;
        return (force_not_leap_year ? CTRL_FORCE_NOTLEAPYEAR : 0u) | (running() ? CTRL_RTC_ACTIVE : 0u) | (enable ? CTRL_RTC_ENABLE : 0u);
    }
    // The attribute write of the reference (`rtc.ctrl = ...`): the stored bits, no load, no interrupt.
    bool set_ctrl(uint32_t value) noexcept {
        using namespace rtc_regs;
        force_not_leap_year = (value & CTRL_FORCE_NOTLEAPYEAR) != 0;
        enable = (value & CTRL_RTC_ENABLE) != 0;
        retime(false);
        return true;
    }
    uint32_t date() const noexcept { return (year << 12) | (month << 8) | day; }
    uint32_t time() const noexcept { return (dotw << 24) | (hour << 16) | (minute << 8) | second; }

    bool raw_interrupt() const noexcept {
        using namespace rtc_regs;
        if (!match_ena()) return false;
        const uint32_t s0 = irq_setup0, s1 = irq_setup1;
        if ((s0 & IRQ_SETUP_0_YEAR_ENA) && ((s0 >> 12) & 0xFFFu) != year) return false;
        if ((s0 & IRQ_SETUP_0_MONTH_ENA) && ((s0 >> 8) & 0xFu) != month) return false;
        if ((s0 & IRQ_SETUP_0_DAY_ENA) && (s0 & 0x1Fu) != day) return false;
        if ((s1 & IRQ_SETUP_1_DOTW_ENA) && ((s1 >> 24) & 0x7u) != dotw) return false;
        if ((s1 & IRQ_SETUP_1_HOUR_ENA) && ((s1 >> 16) & 0x1Fu) != hour) return false;
        if ((s1 & IRQ_SETUP_1_MIN_ENA) && ((s1 >> 8) & 0x3Fu) != minute) return false;
        return !((s1 & IRQ_SETUP_1_SEC_ENA) && (s1 & 0x3Fu) != second);
    }

    // clk_rtc is now `hz` (called by the chip's clock tree; 0: the generator is stopped): the second follows it.
    bool clk_rtc_changed(double hz) noexcept {
        if (hz == clk_rtc_) return true;
        clk_rtc_ = hz;
        retime(true);
        return true;
    }

    // One second of the counter in simulated nanoseconds: (CLKDIV_M1 + 1) periods of clk_rtc.
    double second_nanos() const noexcept { return static_cast<double>(clkdiv_m1 + 1u) * 1e9 / clk_rtc_; }

    // The raw level, masked and forced, to the NVIC line.
    bool check_interrupts() noexcept {
        const uint32_t raw = raw_interrupt() ? 1u : 0u;
        return host_.irq == nullptr || host_.irq(host_.ctx, (((raw | intf) & inte) & rtc_regs::INT_RTC) != 0);
    }

    // RESETS.RESET_RTC: every register 0, the counter 0, the latch 0, the divider stopped; clk_rtc is the CLOCKS block's and is kept.
    bool reset() noexcept {
        clkdiv_m1 = setup0 = setup1 = irq_setup0 = irq_setup1 = inte = intf = 0;
        force_not_leap_year = enable = false;
        year = month = day = dotw = hour = minute = second = 0;
        latched_date = 0;
        retime(true);
        return check_interrupts();
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace rtc_regs;
        switch (offset) {
            case REG_CLKDIV_M1: return clkdiv_m1;
            case REG_SETUP_0: return setup0;
            case REG_SETUP_1: return setup1;
            case REG_CTRL: return ctrl();
            case REG_IRQ_SETUP_0: return irq_setup0 | (match_ena() ? IRQ_SETUP_0_MATCH_ACTIVE : 0u);
            case REG_IRQ_SETUP_1: return irq_setup1;
            case REG_RTC_1: return latched_date;
            case REG_RTC_0: latched_date = date(); return time();
            case REG_INTR: return raw_interrupt() ? 1u : 0u;
            case REG_INTE: return inte;
            case REG_INTF: return intf;
            case REG_INTS: return ((raw_interrupt() ? 1u : 0u) | intf) & inte;
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kRtcWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kRtcWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace rtc_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        switch (offset) {
            case REG_CLKDIV_M1:
                clkdiv_m1 = v & CLKDIV_M1_MASK;
                retime(true);
                return true;
            case REG_SETUP_0: setup0 = v & SETUP_0_MASK; return true;
            case REG_SETUP_1: setup1 = v & SETUP_1_MASK; return true;
            case REG_CTRL: {
                force_not_leap_year = (v & CTRL_FORCE_NOTLEAPYEAR) != 0;
                enable = (v & CTRL_RTC_ENABLE) != 0;
                const bool load = (v & CTRL_LOAD) != 0;
                if (load) load_setup();
                retime(load);
                return !load || check_interrupts();
            }
            case REG_IRQ_SETUP_0: irq_setup0 = v & IRQ_SETUP_0_MASK; return check_interrupts();
            case REG_IRQ_SETUP_1: irq_setup1 = v & IRQ_SETUP_1_MASK; return check_interrupts();
            case REG_INTE: inte = v & INT_RTC; return check_interrupts();
            case REG_INTF: intf = v & INT_RTC; return check_interrupts();
            case REG_RTC_1:
            case REG_RTC_0:
            case REG_INTR:
            case REG_INTS: return true;  // read only
            default: break;
        }
        if (host_.warn) host_.warn(host_.ctx, kRtcWarnWrite, offset, value);
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

    // A second has gone by: the alarm calls it; the shell exposes it as `second_elapsed()` (the tests call it by hand).
    bool second_elapsed() noexcept {
        advance_second();
        retime(true);
        return check_interrupts();
    }

    WindowHandler window_handler() noexcept { return BlockWindow<RtcBlock>::handler(this); }

private:
    static bool on_second(void* ctx) noexcept { return static_cast<RtcBlock*>(ctx)->second_elapsed(); }

    // The divider runs while `running()`; `restart` begins a new second (a load, a change of rate or of divider), otherwise a second already under way is left alone.
    void retime(bool restart) noexcept {
        if (!running()) {
            clock_->cancel(&alarm_);
        } else if (restart || !alarm_.scheduled) {
            clock_->schedule(&alarm_, second_nanos());
        }
    }

    void advance_second() noexcept {
        using namespace rtc_regs;
        if (second < 59) {
            ++second;
            return;
        }
        second = 0;
        if (minute < 59) {
            ++minute;
            return;
        }
        minute = 0;
        if (hour < 23) {
            ++hour;
            return;
        }
        hour = 0;
        dotw = dotw >= 6 ? 0u : dotw + 1u;
        if (day < days_in_month(month, is_leap_year(year, force_not_leap_year))) {
            ++day;
            return;
        }
        day = 1;
        if (month < 12) {
            ++month;
            return;
        }
        month = 1;
        year = (year + 1u) & 0xFFFu;
    }

    void load_setup() noexcept {
        year = (setup0 >> 12) & 0xFFFu;
        month = (setup0 >> 8) & 0xFu;
        day = setup0 & 0x1Fu;
        dotw = (setup1 >> 24) & 0x7u;
        hour = (setup1 >> 16) & 0x1Fu;
        minute = (setup1 >> 8) & 0x3Fu;
        second = setup1 & 0x3Fu;
    }

    Clock* clock_ = nullptr;
    RtcHost host_;
    Alarm alarm_;
    double clk_rtc_ = 0.0;  // clk_rtc in Hz, pushed by the chip: 0 = stopped
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_RTC_HPP
