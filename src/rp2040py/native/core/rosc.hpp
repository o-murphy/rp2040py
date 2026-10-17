// The RP2040 ROSC block (datasheet 2.17) in C++ (docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md "ROSC"): a faithful translation of
// `peripherals/_rosc.py`, which stays as the pure-Python reference and the oracle (tests/test_rosc_diff.py). The reference's docstring carries the sources and says what is not modelled.
//
// What it owns: CTRL (ENABLE 23:12, FREQ_RANGE 11:0), FREQA / FREQB, DORMANT, DIV, PHASE, STATUS (BADWRITE, write 1 to clear), RANDOMBIT and COUNT, and the output frequency derived from DIV
// and FREQ_RANGE (the nominal 6.5 MHz scaled by 16 / divisor and the range's ratio). What it does not own, reached through a `RoscHost`: the logger (invalid writes and unimplemented
// offsets are reported as data, `RegWarn` style), the chip's clock tree (`frequency_changed(hz)`: the chip's `rosc_freq` follows and what runs from it is re-derived) and the random bit
// (`random_bit`). COUNT counts down at the ROSC frequency while the oscillator is enabled, against the chip's `Clock`: the value is derived lazily from the time that passed, so no alarm
// is needed, and it is folded in before anything that changes its rate (the frequency, ENABLE) or its value.
//
// Quirks of the reference that are kept (pinned by tests/test_rosc_diff.py and the C++ checks):
//   - the registers read back what was written, bar the cases the reference lists as not independently sourced: an invalid FREQ_RANGE leaves the previous range, an invalid DIV reads back as
//     div = 31, a wrong FREQA / FREQB password stores drive strengths of 0, a wrong PHASE password stores ENABLE = 1 and SHIFT = 0;
//   - an invalid ENABLE (anything but 0xD1E and 0xFAB, zero included) enables the oscillator and sets BADWRITE; BADWRITE is set by every invalid write the table lists, and each also warns;
//   - STATUS reads STABLE | DIV_RUNNING | ENABLED while enabled (the model is stable at once), BADWRITE, nothing else; RANDOMBIT holds its last value while the oscillator is disabled;
//   - DORMANT is stored and has no effect; the oscillator never really stops; the drive strengths have no effect on the frequency;
//   - `reset()` (the power-on reset, gated on PSM.WDSEL's ROSC bit by `RP2040.reset()`) puts every register back to power-on (enabled, div 16, LOW) and tells the chip if the frequency changed;
//     an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write; a write to RANDOMBIT is dropped; an alias write decodes against a *read* of the register.
//
// Failures: a host call that fails (the logger, the clock tree, the random source) parks its error with the host; the block returns `false` at once, leaving the state the reference's exception
// would have left. Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_ROSC_HPP
#define RP2040PY_CORE_ROSC_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"

namespace rp2040core {

// Warnings that carry data beyond the three of `RegWarn` (the host formats the message): `value` is the offending field.
constexpr uint32_t kRoscWarnInvalidFreqRange = 3, kRoscWarnInvalidEnable = 4, kRoscWarnInvalidFreqPasswd = 5, kRoscWarnInvalidDormant = 6, kRoscWarnInvalidDiv = 7, kRoscWarnInvalidPhasePasswd = 8;

using RoscFrequencyFn = bool (*)(void* ctx, double hz);       // the output frequency changed: the chip's `rosc_freq` follows and its clocks are re-derived; false: failure parked
using RoscBitFn = bool (*)(void* ctx, uint32_t* bit);          // one random bit; false: failure parked

struct RoscHost {
    RoscFrequencyFn frequency_changed = nullptr;
    void* frequency_ctx = nullptr;  // the context `frequency_changed` is called with; null: `ctx` (see `bind_frequency_listener`)
    RoscBitFn random_bit = nullptr;  // optional: without it the block uses its own seeded generator (the same bits on every run)
    void* random_ctx = nullptr;     // the context `random_bit` is called with; null: `ctx` (see `bind_random_source`)
    void* ctx = nullptr;
};

namespace rosc_regs {
constexpr uint32_t REG_CTRL = 0x00, REG_FREQA = 0x04, REG_FREQB = 0x08, REG_DORMANT = 0x0C, REG_DIV = 0x10, REG_PHASE = 0x14, REG_STATUS = 0x18, REG_RANDOMBIT = 0x1C, REG_COUNT = 0x20;
constexpr uint32_t CTRL_MASK = 0x00FFFFFFu, CTRL_ENABLE_LSB = 12, CTRL_ENABLE_BITS = 0x00FFF000u, CTRL_FREQ_RANGE_BITS = 0x00000FFFu;
constexpr uint32_t CTRL_ENABLE_DISABLE = 0xD1E, CTRL_ENABLE_ENABLE = 0xFAB;
constexpr uint32_t CTRL_RESET = (CTRL_ENABLE_ENABLE << CTRL_ENABLE_LSB) | 0xAA0u;
constexpr uint32_t FREQ_RANGE_RESET = 0xAA0, FREQ_RANGE_LOW = 0xFA4, FREQ_RANGE_MEDIUM = 0xFA5, FREQ_RANGE_HIGH = 0xFA7, FREQ_RANGE_TOOHIGH = 0xFA6;
constexpr uint32_t FREQ_PASSWD = 0x9696, FREQ_DS_BITS = 0x7777;
constexpr uint32_t DORMANT_VALUE = 0x636F6D61u, WAKE_VALUE = 0x77616B65u;
constexpr uint32_t DIV_BITS = 0xFFF, DIV_PASS = 0xAA0, DIV_RESET = DIV_PASS + 16, DIV_INVALID = DIV_PASS + 31;
constexpr uint32_t PHASE_PASSWD = 0xAA, PHASE_RESET = 0x8;
constexpr uint32_t STATUS_STABLE = 0x80000000u, STATUS_BADWRITE = 0x01000000u, STATUS_DIV_RUNNING = 0x00010000u, STATUS_ENABLED = 0x00001000u;
constexpr uint32_t COUNT_MASK = 0xFF;
constexpr double NOMINAL_HZ = 6.5e6, NOMINAL_DIV = 16.0;
}  // namespace rosc_regs

class RoscBlock : public RegisterBlock<RoscBlock> {
public:
    RoscBlock() = default;
    RoscBlock(const RoscBlock&) = delete;  // the window handler holds a pointer to this object
    RoscBlock& operator=(const RoscBlock&) = delete;

    void init(Clock* clock, const ChipPorts* ports, uint32_t block, const RoscHost& host) noexcept {
        port_.init(ports, block);
        clock_ = clock;
        host_ = host;
        clock_->add_rebase_listener(&rebase_listener_, &RoscBlock::on_rebase, this);  // the clock's origin moved: COUNT's epoch is an absolute tick count
    }

    // Whoever owns the clock tree asks to be told directly when the output frequency changes (core/clock_sinks.hpp), in place of the host's `frequency_changed`; `fn == nullptr` takes it back.
    void bind_frequency_listener(RoscFrequencyFn fn, void* ctx) noexcept {
        host_.frequency_changed = fn;
        host_.frequency_ctx = ctx;
    }

    // A host that wants the bit from somewhere else (a test's deterministic source, real entropy) hands its function in; `fn == nullptr` goes back to the block's own generator.
    void bind_random_source(RoscBitFn fn, void* ctx) noexcept {
        host_.random_bit = fn;
        host_.random_ctx = ctx;
    }

    // Takes the block off the clock's rebase list; the owner calls it before the block goes away.
    void detach() noexcept {
        Clock::remove_rebase_listener(&rebase_listener_);
    }


    // The registers and flags, public for the shell (the reference's private attributes are read and, by tests, written directly).
    uint32_t ctrl = rosc_regs::CTRL_RESET, freqa = 0, freqb = 0, dormant = rosc_regs::WAKE_VALUE, div = rosc_regs::DIV_RESET, phase = rosc_regs::PHASE_RESET;
    bool badwrite = false;
    bool enabled = true;  // "started automatically during RP2040 power up"
    uint32_t last_bit = 1;
    uint32_t count = 0;
    int64_t count_epoch = 0;  // the clock's time, in ticks, when COUNT was last set or folded
    double hz = rosc_regs::NOMINAL_HZ;  // the frequency this block last gave the chip

    // Back to power-on; tells the chip when that changes the frequency. False: a failure is pending.
    bool reset() noexcept {
        using namespace rosc_regs;
        ctrl = CTRL_RESET;
        freqa = freqb = 0;
        dormant = WAKE_VALUE;
        div = DIV_RESET;
        phase = PHASE_RESET;
        badwrite = false;
        enabled = true;
        last_bit = 1;
        count = 0;
        count_epoch = 0;
        return apply_frequency();
    }

    double frequency() const noexcept { return frequency_of(div, ctrl & rosc_regs::CTRL_FREQ_RANGE_BITS); }

    // The remaining COUNT: the written value less the ROSC cycles that passed since, while the oscillator is enabled.
    uint32_t count_now() const noexcept {
        if (count != 0 && enabled) {
            const double elapsed = static_cast<double>(clock_->ticks() - count_epoch) / static_cast<double>(kTicksPerNano);  // the difference is taken in ticks
            const int64_t done = static_cast<int64_t>(elapsed * hz / 1e9);
            return done >= static_cast<int64_t>(count) ? 0u : count - static_cast<uint32_t>(done);
        }
        return count;
    }

    // False: a failure is pending.
    // The alias decode asks the fallible read.
    bool decode_read(uint32_t offset, uint32_t* out) noexcept { return read(offset, out); }

    bool read(uint32_t offset, uint32_t* out) noexcept {
        using namespace rosc_regs;
        switch (offset) {
            case REG_CTRL: *out = ctrl; return true;
            case REG_FREQA: *out = freqa; return true;
            case REG_FREQB: *out = freqb; return true;
            case REG_DORMANT: *out = dormant; return true;
            case REG_DIV: *out = div; return true;
            case REG_PHASE: *out = phase; return true;
            case REG_STATUS: *out = (enabled ? (STATUS_STABLE | STATUS_DIV_RUNNING | STATUS_ENABLED) : 0u) | (badwrite ? STATUS_BADWRITE : 0u); return true;
            case REG_RANDOMBIT:
                if (enabled) {
                    uint32_t bit = 0;
                    if (host_.random_bit != nullptr) {
                        if (!host_.random_bit(host_.random_ctx != nullptr ? host_.random_ctx : host_.ctx, &bit)) return false;
                    } else {
                        bit = next_own_bit();
                    }
                    last_bit = bit & 1u;
                }
                *out = last_bit;
                return true;
            case REG_COUNT: *out = count_now(); return true;
            default: break;
        }
        port_.unimplemented_read(offset);
        *out = 0xFFFFFFFFu;
        return true;
    }

    uint32_t read(uint32_t offset) noexcept {
        uint32_t value = 0;
        (void)read(offset, &value);
        return value;
    }

    // `value` is the value after any atomic-alias decode. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace rosc_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        switch (offset) {
            case REG_CTRL: {
                const uint32_t masked = v & CTRL_MASK;
                uint32_t freq_range = masked & CTRL_FREQ_RANGE_BITS;
                if (range_ratio(freq_range) == 0.0) {
                    bad_write(kRoscWarnInvalidFreqRange, offset, freq_range);
                    freq_range = ctrl & CTRL_FREQ_RANGE_BITS;  // the range stays as it was
                }
                const uint32_t enable_value = (masked & CTRL_ENABLE_BITS) >> CTRL_ENABLE_LSB;
                if (enable_value != CTRL_ENABLE_ENABLE && enable_value != CTRL_ENABLE_DISABLE) bad_write(kRoscWarnInvalidEnable, offset, enable_value);  // "An invalid setting will enable the oscillator"
                const bool now_enabled = enable_value != CTRL_ENABLE_DISABLE;
                if (now_enabled != enabled) {
                    rebase_count();
                    enabled = now_enabled;
                }
                ctrl = (masked & CTRL_ENABLE_BITS) | freq_range;
                return apply_frequency();
            }
            case REG_FREQA:
            case REG_FREQB: {
                uint32_t drive = v & FREQ_DS_BITS;
                if (((v >> 16) & 0xFFFFu) != FREQ_PASSWD) {
                    bad_write(kRoscWarnInvalidFreqPasswd, offset, (v >> 16) & 0xFFFFu);
                    drive = 0;  // "Any other value in this field will set all drive strengths to 0"
                }
                const uint32_t stored = (v & 0xFFFF0000u) | drive;
                if (offset == REG_FREQA) freqa = stored; else freqb = stored;
                return true;
            }
            case REG_DORMANT: {
                uint32_t stored = v;
                if (v != DORMANT_VALUE && v != WAKE_VALUE) {
                    bad_write(kRoscWarnInvalidDormant, offset, v);
                    stored = WAKE_VALUE;  // "An invalid write will also select WAKE"
                }
                dormant = stored;
                return true;
            }
            case REG_DIV: {
                uint32_t stored = v & DIV_BITS;
                if (stored < DIV_PASS || stored > DIV_PASS + 31) {
                    bad_write(kRoscWarnInvalidDiv, offset, stored);
                    stored = DIV_INVALID;  // "any other value sets div=31"
                }
                div = stored;
                return apply_frequency();
            }
            case REG_PHASE: {
                const uint32_t bits = v & DIV_BITS;
                uint32_t enable = (bits >> 3) & 1u, shift = bits & 3u;
                if (((bits >> 4) & 0xFFu) != PHASE_PASSWD) {
                    bad_write(kRoscWarnInvalidPhasePasswd, offset, (bits >> 4) & 0xFFu);
                    enable = 1;  // "any other value enables the output with shift=0"
                    shift = 0;
                }
                phase = (bits & 0xFF0u) | (enable << 3) | (bits & 4u) | shift;
                return true;
            }
            case REG_STATUS:
                if (v & STATUS_BADWRITE) badwrite = false;  // write 1 to clear
                return true;
            case REG_COUNT:
                count = v & COUNT_MASK;  // "To start the counter write a non-zero value"
                count_epoch = clock_->ticks();
                return true;
            case REG_RANDOMBIT: return true;  // read only
            default: break;
        }
        port_.unimplemented_write(offset, value);
        return true;
    }



private:
    // The block's own source of RANDOMBIT: xorshift32, seeded the same on every run, so a simulation is reproducible. (The pure-Python reference seeds Python's generator; no test compares the two sequences.)
    uint32_t next_own_bit() noexcept {
        rng_ ^= rng_ << 13;
        rng_ ^= rng_ >> 17;
        rng_ ^= rng_ << 5;
        return rng_ & 1u;
    }
    uint32_t rng_ = 0x2040;
    static void on_rebase(void* ctx, int64_t delta) noexcept {
        auto* self = static_cast<RoscBlock*>(ctx);
        self->count_epoch -= delta;
        self->rebase_count();  // folded into COUNT: the epoch stays at the clock's time instead of drifting away from it by `delta` every move (it would overflow)
    }

    // 2.17.3's ratios of the ranges to LOW (0 for a value that is not a range); 0xAA0, the reset value, is LOW.
    static double range_ratio(uint32_t freq_range) noexcept {
        using namespace rosc_regs;
        switch (freq_range) {
            case FREQ_RANGE_RESET:
            case FREQ_RANGE_LOW: return 1.0;
            case FREQ_RANGE_MEDIUM: return 1.33;
            case FREQ_RANGE_HIGH: return 2.0;
            case FREQ_RANGE_TOOHIGH: return 4.0;
            default: return 0.0;
        }
    }

    // The same expression, in the same order, as `_rosc._frequency`: `div = 0` divides by 32.
    static double frequency_of(uint32_t div_value, uint32_t freq_range) noexcept {
        using namespace rosc_regs;
        const uint32_t d = div_value - DIV_PASS;
        const double divisor = static_cast<double>(d != 0 ? d : 32u);
        return NOMINAL_HZ * NOMINAL_DIV / divisor * range_ratio(freq_range);
    }

    void bad_write(uint32_t kind, uint32_t offset, uint32_t field) noexcept {
        badwrite = true;
        port_.warn(kind, offset, field);
    }

    void rebase_count() noexcept {
        count = count_now();
        count_epoch = clock_->ticks();
    }

    // Tells the chip when the output frequency changed. False: a failure is pending.
    bool apply_frequency() noexcept {
        const double f = frequency();
        if (f != hz) {
            rebase_count();
            hz = f;
            if (!host_.frequency_changed(host_.frequency_ctx != nullptr ? host_.frequency_ctx : host_.ctx, f)) return false;
        }
        return true;
    }

    RoscHost host_;
    RebaseListener rebase_listener_;
    Clock* clock_ = nullptr;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_ROSC_HPP
