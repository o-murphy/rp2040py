// The RP2040 PWM in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of peripherals/_pwm.py, which stays as the pure-Python reference and the oracle
// (tests/test_pwm_diff.py).
//
// What it owns: eight channels, each a `Timer32` (core/timer32.hpp: the counter, the prescaler, the zigzag/increment mode) with three compare alarms on it (A, B and the wrap), the
// register file (CSR, DIV, CTR, CC, TOP per channel; EN, INTR, INTE, INTF, INTS for the block), the double buffering of CC and TOP, the counting of B-input edges in the gated and
// edge-counting divider modes, and the block's two pin words: `gpio_value` and `gpio_direction`, which the chip's pins read to decide the level of a pin whose function is PWM. The alarms
// are nodes of the chip's C++ `Clock` (the block takes a `Clock*`, as the DMA and the ADC do), so a running PWM costs no Python.
//
// What it does not own, reached through a `PwmHost` of plain function pointers (see core_host.hpp): the interrupt line (`irq`), the DMA request of a wrapping channel (`dreq`, told the channel's
// index), the pin it just changed (`pin_changed`: the reference calls `gpio[i].check_for_updates()`), the level of a pin (`pin_read`: `gpio[i].input_value`, for the B input) and the logger.
//
// Failures follow the contract of core_host.hpp: a host call that fails makes the block return `false` at once, leaving the state the reference's exception would have left; a failure out of
// an alarm makes the alarm return `false` and the clock stops ticking.
//
// Quirks of the reference that are kept (pinned by the C++ checks and by tests/test_pwm_diff.py), several of them bugs that are fixed later, one commit each, with the oracle's mutants:
//   - the per-channel `en` reads as 0, so reading EN always gives 0 whatever is enabled (a copy of an rp2040js quirk); writing EN works;
//   - `gpio_on_input` suppresses every B-input edge while `gpio_direction` is nonzero (`and` for `&`, again from rp2040js) - after a reset it never is zero, so the gated and edge-counting
//     divider modes are dead unless something writes the word;
//   - in phase-correct mode the wrap does not drive A and B, so a phase-correct output only ever changes at the compare alarms;
//   - CC and TOP are double buffered: a write is held until the channel is enabled or wraps; a CC write is not masked (the reference keeps the 32-bit value; TOP keeps 16 bits);
//   - reset() restarts the channels and the direction word, not INTR/INTE/INTF or `gpio_value`;
//   - an unimplemented offset warns and reads 0xFFFFFFFF (a read above 0x1000 warns a second time); an unimplemented write warns.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_PWM_HPP
#define RP2040PY_CORE_PWM_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"
#include "timer32.hpp"

namespace rp2040core {

using PwmIrqFn = bool (*)(void* ctx, bool level);                       // false: failure parked
using PwmDreqFn = bool (*)(void* ctx, uint32_t channel);                // the DMA request of PWM channel `channel` (DREQ_PWM_WRAP0 + channel); false: failure parked
using PwmPinChangedFn = bool (*)(void* ctx, uint32_t pin);              // the block changed the word bit of `pin`: the host re-evaluates it; false: failure parked
using PwmPinReadFn = bool (*)(void* ctx, uint32_t pin, bool* level);    // the input level of `pin`; false: failure parked
constexpr uint32_t kPwmWarnRead = kRegWarnRead, kPwmWarnReadAtomicArea = kRegWarnReadAtomicArea, kPwmWarnWrite = kRegWarnWrite;
using PwmWarnFn = RegWarnFn;

struct PwmHost {
    PwmIrqFn irq = nullptr;
    PwmDreqFn dreq = nullptr;
    PwmPinChangedFn pin_changed = nullptr;
    PwmPinReadFn pin_read = nullptr;
    PwmWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace pwm_regs {
constexpr uint32_t CHN_CSR = 0x00, CHN_DIV = 0x04, CHN_CTR = 0x08, CHN_CC = 0x0C, CHN_TOP = 0x10;
constexpr uint32_t kChannelStride = 0x14, kChannels = 8;
constexpr uint32_t EN = 0xA0, INTR = 0xA4, INTE = 0xA8, INTF = 0xAC, INTS = 0xB0;
constexpr uint32_t INT_MASK = 0xFF;
constexpr uint32_t CSR_PH_ADV = 1u << 7, CSR_PH_RET = 1u << 6, CSR_DIVMODE_SHIFT = 4, CSR_DIVMODE_MASK = 0x3;
constexpr uint32_t CSR_B_INV = 1u << 3, CSR_A_INV = 1u << 2, CSR_PH_CORRECT = 1u << 1, CSR_EN = 1u << 0;
}  // namespace pwm_regs

enum class PwmDivMode : uint32_t { kFreeRunning = 0, kBGated = 1, kBRisingEdge = 2, kBFallingEdge = 3 };

class PwmBlock;

class PwmChannel {
public:
    PwmChannel() = default;
    PwmChannel(const PwmChannel&) = delete;  // the timer's alarms hold pointers into this object
    PwmChannel& operator=(const PwmChannel&) = delete;

    // The state, public for the shell (the reference's attributes are read and, by tests, written directly).
    Timer32 timer;
    Timer32PeriodicAlarm alarm_a, alarm_b, alarm_bottom;
    uint32_t csr = 0, div = 0, cc = 0, top = 0;
    bool last_b_value = false, counting_up = true, cc_updated = false, top_updated = false;
    double tick_counter = 0.0;
    PwmDivMode div_mode = PwmDivMode::kFreeRunning;
    int64_t pin_a1 = 0, pin_b1 = 0, pin_a2 = -1, pin_b2 = -1;  // GPIO pin indices; -1: the channel has no second pair (channel 7)
    uint32_t index = 0;
    uint32_t div_mode_raw() const noexcept { return static_cast<uint32_t>(div_mode); }  // for the shells: the enum as a plain number
    void set_div_mode_raw(uint32_t value) noexcept { div_mode = static_cast<PwmDivMode>(value & 0x3u); }

    // Same order as the reference's constructor: the timer, the three alarms (attached to the timer in that order), the pins, then the alarms enabled.
    void init(PwmBlock* pwm, Clock* clock, uint32_t channel, double clock_freq) noexcept {
        pwm_ = pwm;
        index = channel;
        timer.init(clock, clock_freq);
        alarm_a.init(&timer, &PwmChannel::on_alarm_a, this);
        alarm_b.init(&timer, &PwmChannel::on_alarm_b, this);
        alarm_bottom.init(&timer, &PwmChannel::on_wrap, this);
        pin_a1 = static_cast<int64_t>(channel) * 2;
        pin_b1 = pin_a1 + 1;
        pin_a2 = channel < 7 ? 16 + static_cast<int64_t>(channel) * 2 : -1;
        pin_b2 = channel < 7 ? 16 + static_cast<int64_t>(channel) * 2 + 1 : -1;
        alarm_a.set_enable(true);
        alarm_b.set_enable(true);
        alarm_bottom.set_enable(true);
    }

    void detach() noexcept {
        alarm_a.detach();
        alarm_b.detach();
        alarm_bottom.detach();
    }

    uint32_t read_register(uint32_t offset) const noexcept {
        using namespace pwm_regs;
        switch (offset) {
            case CHN_CSR: return csr;
            case CHN_DIV: return div;
            case CHN_CTR: return timer.counter();
            case CHN_CC: return cc;
            case CHN_TOP: return top;
            default: return 0;  // "Shouldn't get here"
        }
    }

    inline bool write_register(uint32_t offset, uint32_t value) noexcept;
    inline bool reset() noexcept;
    inline bool set_a(bool value) noexcept;
    inline bool set_b(bool value) noexcept;
    inline bool gpio_b_value(bool* level) noexcept;
    inline bool gpio_b_changed() noexcept;
    inline bool update_enable() noexcept;

    // The reference's `en` getter: always 0 (rp2040js defines only a setter, so reading it gives `undefined`, which the shift in the EN register read turns into 0).
    uint32_t en() const noexcept { return 0; }
    inline bool set_en(bool value) noexcept;

    void update_double_buffered() noexcept {
        if (cc_updated) {
            alarm_b.set_target(static_cast<int64_t>(cc >> 16));
            alarm_a.set_target(static_cast<int64_t>(cc & 0xFFFFu));
            cc_updated = false;
        }
        if (top_updated) {
            timer.set_top(static_cast<int64_t>(top));
            top_updated = false;
        }
    }

private:
    static bool on_alarm_a(void* ctx) noexcept { return static_cast<PwmChannel*>(ctx)->set_a(false); }
    static bool on_alarm_b(void* ctx) noexcept { return static_cast<PwmChannel*>(ctx)->set_b(false); }
    static bool on_wrap(void* ctx) noexcept { return static_cast<PwmChannel*>(ctx)->wrap(); }
    inline bool wrap() noexcept;
    inline bool set_b_direction(bool output) noexcept;

    PwmBlock* pwm_ = nullptr;
};

class PwmBlock {
public:
    PwmBlock() = default;
    PwmBlock(const PwmBlock&) = delete;  // the channels' alarms and the window handler hold pointers to this object
    PwmBlock& operator=(const PwmBlock&) = delete;

    PwmChannel channels[pwm_regs::kChannels];
    uint32_t int_raw = 0, int_enable = 0, int_force = 0;
    uint32_t gpio_value = 0, gpio_direction = 0;

    // Binds the block to its clock and host; `clock_freq` is the system clock the counters run on (the reference reads `rp2040.clk_sys` when it builds the channels).
    void init(Clock* clock, const PwmHost& host, double clock_freq) noexcept {
        clock_ = clock;
        host_ = host;
        clock_freq_ = clock_freq;
        for (uint32_t i = 0; i < pwm_regs::kChannels; ++i) channels[i].init(this, clock, i, clock_freq);
    }

    // Unlinks every alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept {
        if (clock_ == nullptr) return;
        for (PwmChannel& channel : channels) channel.detach();
    }

    Clock* clock() const noexcept { return clock_; }
    bool failed() const noexcept { return host_failed(host_.failed); }
    double clock_freq() const noexcept { return clock_freq_; }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // A new system clock: every counter keeps its value and continues at the new rate.
    void set_clock_freq(double freq) noexcept {
        clock_freq_ = freq;
        for (PwmChannel& channel : channels) channel.timer.set_frequency(freq);
    }

    uint32_t int_status() const noexcept { return (int_raw & int_enable) | int_force; }

    bool check_interrupts() noexcept { return host_.irq(host_.ctx, int_status() != 0); }

    bool channel_interrupt(uint32_t index) noexcept {
        int_raw |= 1u << index;
        if (!check_interrupts()) return false;
        return host_.dreq(host_.ctx, index);  // also the DMA request of the channel
    }

    bool gpio_set(int64_t index, bool value) noexcept {
        const uint32_t bit = 1u << index;
        const uint32_t next = value ? (gpio_value | bit) : (gpio_value & ~bit);
        if (gpio_value != next) {
            gpio_value = next;
            return host_.pin_changed(host_.ctx, static_cast<uint32_t>(index));
        }
        return true;
    }

    bool gpio_set_dir(int64_t index, bool output) noexcept {
        const uint32_t bit = 1u << index;
        const uint32_t next = output ? (gpio_direction | bit) : (gpio_direction & ~bit);
        if (gpio_direction != next) {
            gpio_direction = next;
            return host_.pin_changed(host_.ctx, static_cast<uint32_t>(index));
        }
        return true;
    }

    bool gpio_read(int64_t index, bool* level) noexcept { return host_.pin_read(host_.ctx, static_cast<uint32_t>(index), level); }

    // A pin whose function is PWM changed its input level. The reference gates this on `gpio_direction and 1 << index`, which is true whenever the word is nonzero: every edge is dropped
    // then (and after a reset it is never zero).
    bool gpio_on_input(uint32_t index) noexcept {
        if (gpio_direction != 0u) return true;
        for (PwmChannel& channel : channels) {
            if (channel.pin_b1 == static_cast<int64_t>(index) || channel.pin_b2 == static_cast<int64_t>(index)) {
                if (!channel.gpio_b_changed()) return false;
            }
        }
        return true;
    }

    // False: a failure is pending (the state is already what the reference's exception would have left).
    bool reset() noexcept {
        gpio_direction = 0xFFFFFFFFu;
        for (PwmChannel& channel : channels) {
            if (!channel.reset()) return false;
        }
        return true;
    }

    // ---- registers --------------------------------------------------------------------------------------------

    // Reads a register. The EN read is always 0 (see `PwmChannel::en`).
    uint32_t read(uint32_t offset) noexcept {
        using namespace pwm_regs;
        if (offset < EN) return channels[offset / kChannelStride].read_register(offset % kChannelStride);
        switch (offset) {
            case EN:
                return (channels[7].en() << 7) | (channels[6].en() << 6) | (channels[5].en() << 5) | (channels[4].en() << 4) | (channels[3].en() << 3) |
                       (channels[2].en() << 2) | (channels[1].en() << 1) | (channels[0].en() << 0);
            case INTR: return int_raw;
            case INTE: return int_enable;
            case INTF: return int_force;
            case INTS: return int_status();
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kPwmWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kPwmWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace pwm_regs;
        const uint32_t word = static_cast<uint32_t>(value);
        if (offset < EN) return channels[offset / kChannelStride].write_register(offset % kChannelStride, word);
        switch (offset) {
            case EN:
                for (int i = 7; i >= 0; --i) {
                    if (!channels[i].set_en((word & (1u << i)) != 0)) return false;
                }
                return true;
            case INTR:
                int_raw &= ~(word & INT_MASK);
                return check_interrupts();
            case INTE:
                int_enable = word & INT_MASK;
                return check_interrupts();
            case INTF:
                int_force = word & INT_MASK;
                return check_interrupts();
            default: break;
        }
        if (host_.warn) host_.warn(host_.ctx, kPwmWarnWrite, offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step - remember the raw value, decode the alias against a *read* of the register (with that read's side effects), then write.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        return write(offset, value);
    }

    WindowHandler window_handler() noexcept { return BlockWindow<PwmBlock>::handler(this); }

private:
    PwmHost host_;
    Clock* clock_ = nullptr;
    double clock_freq_ = 0.0;
    int64_t raw_write_value_ = 0;
};

// ---- the channel, now that the block is complete ---------------------------------------------------------------------------------

inline bool PwmChannel::set_a(bool value) noexcept {
    if (csr & pwm_regs::CSR_A_INV) value = !value;
    if (!pwm_->gpio_set(pin_a1, value)) return false;
    if (pin_a2 >= 0) return pwm_->gpio_set(pin_a2, value);
    return true;
}

inline bool PwmChannel::set_b(bool value) noexcept {
    if (csr & pwm_regs::CSR_B_INV) value = !value;
    if (!pwm_->gpio_set(pin_b1, value)) return false;
    if (pin_b2 >= 0) return pwm_->gpio_set(pin_b2, value);
    return true;
}

inline bool PwmChannel::set_b_direction(bool output) noexcept {
    if (!pwm_->gpio_set_dir(pin_b1, output)) return false;
    if (pin_b2 >= 0) return pwm_->gpio_set_dir(pin_b2, output);
    return true;
}

// The B input, ORed over the channel's two pins; the second is read only when the first is low.
inline bool PwmChannel::gpio_b_value(bool* level) noexcept {
    bool value = false;
    if (!pwm_->gpio_read(pin_b1, &value)) return false;
    if (!value && pin_b2 > 0) {
        if (!pwm_->gpio_read(pin_b2, &value)) return false;
    }
    *level = value;
    return true;
}

inline bool PwmChannel::update_enable() noexcept {
    bool run = (csr & pwm_regs::CSR_EN) != 0;
    if (run && div_mode != PwmDivMode::kFreeRunning) {
        if (div_mode == PwmDivMode::kBGated) {
            bool level = false;
            if (!gpio_b_value(&level)) return false;
            run = level;
        } else {
            run = false;
        }
    }
    timer.set_enable(run);
    return true;
}

inline bool PwmChannel::gpio_b_changed() noexcept {
    bool value = false;
    if (!gpio_b_value(&value)) return false;
    if (value == last_b_value) return true;
    last_b_value = value;

    if (div_mode == PwmDivMode::kBGated) {
        if (!update_enable()) return false;
    } else if (div_mode == PwmDivMode::kBRisingEdge) {
        if (value) tick_counter += 1;
    } else if (div_mode == PwmDivMode::kBFallingEdge) {
        if (!value) tick_counter += 1;
    }

    if (tick_counter >= timer.prescaler()) {
        timer.advance(1);
        tick_counter -= timer.prescaler();
    }
    return true;
}

inline bool PwmChannel::wrap() noexcept {
    if (!pwm_->channel_interrupt(index)) return false;
    update_double_buffered();
    if (!(csr & pwm_regs::CSR_PH_CORRECT)) {
        if (!set_a(alarm_a.target() > 0)) return false;
        if (!set_b(alarm_b.target() > 0)) return false;
    }
    return true;
}

inline bool PwmChannel::write_register(uint32_t offset, uint32_t value) noexcept {
    using namespace pwm_regs;
    switch (offset) {
        case CHN_CSR: {
            if ((value & CSR_EN) && !(csr & CSR_EN)) update_double_buffered();
            // PH_ADV and PH_RET are self-clearing strobes: they act on the written value, never appear in the stored CSR, and move a *running* counter, so they need the enable in the same write.
            csr = value & ~(CSR_PH_ADV | CSR_PH_RET);
            if (value & CSR_EN) {
                if (value & CSR_PH_ADV) timer.advance(1);
                if (value & CSR_PH_RET) timer.advance(-1);
            }
            div_mode = static_cast<PwmDivMode>((csr >> CSR_DIVMODE_SHIFT) & CSR_DIVMODE_MASK);
            if (!set_b_direction(div_mode == PwmDivMode::kFreeRunning)) return false;
            if (!update_enable()) return false;
            bool level = false;
            if (!gpio_b_value(&level)) return false;
            last_b_value = level;
            timer.set_mode((value & CSR_PH_CORRECT) ? TimerMode::kZigzag : TimerMode::kIncrement);
            return true;
        }
        case CHN_DIV: {
            div = value & 0x000FFFFFu;
            const uint32_t int_value = (value >> 4) & 0xFFu;
            const uint32_t frac_value = value & 0xFu;
            timer.set_prescaler(static_cast<double>(int_value ? int_value : 256u) + static_cast<double>(frac_value) / 16.0);
            return true;
        }
        case CHN_CTR: timer.set(static_cast<int64_t>(value & 0xFFFFu)); return true;
        case CHN_CC:
            cc = value;
            cc_updated = true;
            return true;
        case CHN_TOP:
            top = value & 0xFFFFu;
            top_updated = true;
            return true;
        default: return true;
    }
}

inline bool PwmChannel::reset() noexcept {
    using namespace pwm_regs;
    if (!write_register(CHN_CSR, 0)) return false;
    if (!write_register(CHN_DIV, 0x01u << 4)) return false;
    if (!write_register(CHN_CTR, 0)) return false;
    if (!write_register(CHN_CC, 0)) return false;
    if (!write_register(CHN_TOP, 0xFFFFu)) return false;
    counting_up = true;
    timer.set_enable(false);
    timer.reset();
    return true;
}

inline bool PwmChannel::set_en(bool value) noexcept {
    if (value && !(csr & pwm_regs::CSR_EN)) update_double_buffered();
    if (value) {
        csr |= pwm_regs::CSR_EN;
    } else {
        csr &= ~pwm_regs::CSR_EN;
    }
    return update_enable();
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PWM_HPP
