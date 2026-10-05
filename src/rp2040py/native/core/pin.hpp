// The RP2040 pin layer in C++ (docs/records/0096-cpp-mcu-core.md, Phase 3): a faithful translation of the `GPIOPin` of
// native/_gpio_pin.pyx, which is itself a port of _gpio_pin.py (the pure-Python reference and the oracle).
//
// A pin is ten words of state (FUNCSEL/overrides in `ctrl`, the pad word, three IRQ words, the last announced level, the externally
// driven input and whether it is driven at all). Its *level* is a pure function of that state and of other blocks' registers - SIO's
// output enable/value, each PIO's pin directions/values, the PWM's - which this header reaches through `PinHost::source` (a function
// pointer, a Python trampoline today) unless a *direct* pointer has been bound for that source (SIO's fields, once SIO is C++: no call at
// all). What it owns: all of the above for up to 32 pins (the 30 GPIO pins in one bank, the 6 QSPI pins in another). What it does not
// own: the listeners (a Python `set`, iterated by the host in `on_change`, so their order is the host's - unchanged by construction), the
// IO interrupt line and the PWM/PIO reactions to an input change - all reached through host function pointers.
//
// Every observable of the Cython class is kept, in the same order: `check_for_updates()` announces a change only when the *state code*
// (LOW/HIGH/INPUT/PULL_UP/PULL_DOWN/BUS_KEEPER) changed and records it *before* calling out, so a failing listener leaves the pin
// already updated; `apply_input_value()` updates the IRQ status, then tells the IO interrupt if the pin's irq value changed, then the
// PWM/PIO reactions. A pin constructed for the QSPI bank has `always_output_enabled`, and note the Cython quirk kept as is: an input
// change on QSPI pin *n* reaches the same host reactions as GPIO pin *n* (the host gets only an index).
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract. Every function that can call out returns
// `false` when the host reported failure (a Python trampoline can raise): the failure is pending with the caller, and whatever was
// running stops at once - as an exception would stop it.
#ifndef RP2040PY_CORE_PIN_HPP
#define RP2040PY_CORE_PIN_HPP

#include <cstdint>

namespace rp2040core {

enum PinState : int {  // GPIOPinState's integer values
    kPinLow = 0,
    kPinHigh = 1,
    kPinInput = 2,
    kPinPullUp = 3,
    kPinPullDown = 4,
    kPinBusKeeper = 5,
};

constexpr uint32_t kFuncPwm = 4, kFuncSio = 5, kFuncPio0 = 6, kFuncPio1 = 7;

namespace pin_bits {
constexpr uint32_t kIrqLevelLow = 1u << 0, kIrqLevelHigh = 1u << 1, kIrqEdgeLow = 1u << 2, kIrqEdgeHigh = 1u << 3;
constexpr uint32_t kPadPulldown = 1u << 2, kPadPullup = 1u << 3, kPadInputEnable = 1u << 6;
constexpr uint32_t kDefaultCtrl = 0x1F;          // FUNCSEL = NULL, no overrides
constexpr uint32_t kDefaultPad = 0b0110110;      // PADS_BANK0's reset value
}  // namespace pin_bits

// Where a pin's output level/direction can come from: the registers of other blocks.
enum PinSource : uint32_t {
    kSrcSioOe = 0,
    kSrcSioValue = 1,
    kSrcPio0Oe = 2,
    kSrcPio0Value = 3,
    kSrcPio1Oe = 4,
    kSrcPio1Value = 5,
    kSrcPwmDirection = 6,
    kSrcPwmValue = 7,
    kPinSources = 8,
};

using PinSourceFn = bool (*)(void* ctx, uint32_t source, uint32_t* out);                       // false: failed
using PinChangeFn = bool (*)(void* ctx, uint32_t pin, int new_state, int old_state);           // a listener ran; false: it failed
using PinIoIrqFn = bool (*)(void* ctx);                                                        // a pin's IRQ value changed: re-evaluate the IO interrupt
using PinInputFn = bool (*)(void* ctx, uint32_t pin, bool function_is_pwm);                    // an input level changed: PWM, then PIO wait checks

struct PinHost {
    PinSourceFn source = nullptr;
    PinChangeFn on_change = nullptr;
    PinIoIrqFn io_interrupt = nullptr;
    PinInputFn input_changed = nullptr;
    void* ctx = nullptr;
};

// A consumer that must answer in the same cycle (the CYW43 gSPI shifter on the falling clock edge) registers a direct listener: a function
// pointer called from `check_for_updates()` *before* the host's `on_change` (the Python listener set), so it never crosses into Python.
constexpr uint32_t kMaxDirectListeners = 4;
struct PinListener {
    bool (*fn)(void* ctx, uint32_t pin, int new_state, int old_state) = nullptr;  // false: it failed
    void* ctx = nullptr;
};

struct Pin {
    uint32_t index = 0;  // the pin's number: the bit it owns in every source register and what the host is told
    uint32_t ctrl = 0;  // IO_BANK0.GPIOn_CTRL
    uint32_t pad_value = 0;  // PADS_BANK0.GPIOn
    uint32_t irq_enable_mask = 0;
    uint32_t irq_force_mask = 0;
    uint32_t irq_status = 0;
    int last_state = kPinInput;
    bool raw_input_value = false;  // what the outside world drives (or its last value)
    bool driven = false;           // something is driving it (else the pad's pull decides)
    bool always_output_enabled = false;
    PinListener direct[kMaxDirectListeners];
    uint32_t direct_count = 0;
};

inline bool apply_override(bool value, uint32_t override_type) noexcept {
    switch (override_type & 3u) {
        case 1: return !value;
        case 2: return false;
        case 3: return true;
        default: return value;
    }
}

class PinBank {
public:
    static constexpr uint32_t kMaxPins = 32;

    PinBank() = default;
    PinBank(const PinBank&) = delete;
    PinBank& operator=(const PinBank&) = delete;

    // `count` pins (<= kMaxPins), numbered `first_index`, `first_index + 1`, ... (a bank of one pin per Python `GPIOPin` until the pins
    // share a bank). Mirrors `GPIOPin.__init__`: `_last_state` is computed with ctrl = 0 and pad = 0 *before* the real
    // defaults are written (deterministically INPUT for an ordinary pin, LOW for one that is always output-enabled).
    bool init(uint32_t count, const PinHost& host, const bool* always_output_enabled = nullptr, uint32_t first_index = 0) noexcept {
        count_ = count > kMaxPins ? kMaxPins : count;
        host_ = host;
        for (uint32_t i = 0; i < count_; ++i) {
            Pin& p = pins_[i];
            p = Pin();
            p.index = first_index + i;
            p.always_output_enabled = always_output_enabled != nullptr && always_output_enabled[i];
            int code = 0;
            if (!state_code(i, &code)) return false;
            p.last_state = code;
            p.ctrl = pin_bits::kDefaultCtrl;
            p.pad_value = pin_bits::kDefaultPad;
        }
        return true;
    }

    uint32_t count() const noexcept { return count_; }
    Pin& pin(uint32_t i) noexcept { return pins_[i]; }
    Pin* pin_ptr(uint32_t i) noexcept { return &pins_[i]; }  // for Cython, which cannot take the address of a reference result
    const Pin& pin(uint32_t i) const noexcept { return pins_[i]; }

    // A direct pointer replaces the host call for that source (e.g. SIO's own `gpio_output_enable`); nullptr goes back to the host.
    void bind_source(uint32_t source, const uint32_t* direct) noexcept {
        if (source < kPinSources) direct_[source] = direct;
    }

    // Direct listeners are called in registration order, before the host's `on_change`. They must not add or remove listeners from inside a
    // call. Returns false if the table is full (add) or the listener is not registered (remove).
    bool add_direct_listener(uint32_t i, bool (*fn)(void*, uint32_t, int, int), void* ctx) noexcept {
        Pin& p = pins_[i];
        if (p.direct_count >= kMaxDirectListeners) return false;
        p.direct[p.direct_count].fn = fn;
        p.direct[p.direct_count].ctx = ctx;
        ++p.direct_count;
        return true;
    }
    bool remove_direct_listener(uint32_t i, bool (*fn)(void*, uint32_t, int, int), void* ctx) noexcept {
        Pin& p = pins_[i];
        for (uint32_t n = 0; n < p.direct_count; ++n) {
            if (p.direct[n].fn == fn && p.direct[n].ctx == ctx) {
                for (uint32_t m = n + 1; m < p.direct_count; ++m) p.direct[m - 1] = p.direct[m];
                --p.direct_count;
                p.direct[p.direct_count] = PinListener();
                return true;
            }
        }
        return false;
    }

    // --- levels -----------------------------------------------------------------------------------------------------------

    bool raw_output_enable(uint32_t i, uint32_t fsel, bool* out) const noexcept {
        const Pin& p = pins_[i];
        if (p.always_output_enabled) {
            *out = true;
            return true;
        }
        uint32_t source = 0;
        if (!source_for(fsel, /*value=*/false, &source)) {
            *out = false;
            return true;
        }
        uint32_t bits = 0;
        if (!read_source(source, &bits)) return false;
        *out = ((bits >> p.index) & 1u) != 0;
        return true;
    }

    bool raw_output_value(uint32_t i, uint32_t fsel, bool* out) const noexcept {
        const Pin& p = pins_[i];
        uint32_t source = 0;
        if (!source_for(fsel, /*value=*/true, &source)) {
            *out = false;
            return true;
        }
        uint32_t bits = 0;
        if (!read_source(source, &bits)) return false;
        *out = ((bits >> p.index) & 1u) != 0;
        return true;
    }

    // The level after pull resistors and the external drive, before the pad's input enable and the input override.
    bool eff_raw_input(uint32_t i) const noexcept {
        const Pin& p = pins_[i];
        if (p.driven) return p.raw_input_value;
        const bool pull_up = (p.pad_value & pin_bits::kPadPullup) != 0;
        const bool pull_down = (p.pad_value & pin_bits::kPadPulldown) != 0;
        if (pull_up && !pull_down) return true;
        if (pull_down && !pull_up) return false;
        return p.raw_input_value;
    }

    // `GPIOPinState` as an integer; false: a source failed.
    bool state_code(uint32_t i, int* out) const noexcept {
        const Pin& p = pins_[i];
        const uint32_t fsel = p.ctrl & 0x1F;
        bool raw_oe = false;
        if (!raw_output_enable(i, fsel, &raw_oe)) return false;
        if (apply_override(raw_oe, (p.ctrl >> 12) & 3u)) {
            bool raw_ov = false;
            if (!raw_output_value(i, fsel, &raw_ov)) return false;
            *out = apply_override(raw_ov, (p.ctrl >> 8) & 3u) ? kPinHigh : kPinLow;
            return true;
        }
        const bool pd = (p.pad_value & pin_bits::kPadPulldown) != 0;
        const bool pu = (p.pad_value & pin_bits::kPadPullup) != 0;
        *out = (pd && pu) ? kPinBusKeeper : pd ? kPinPullDown : pu ? kPinPullUp : kPinInput;
        return true;
    }

    bool raw_interrupt(uint32_t i) const noexcept {
        const Pin& p = pins_[i];
        return ((p.irq_status & p.irq_enable_mask) | p.irq_force_mask) != 0;
    }
    bool irq_value(uint32_t i) const noexcept { return apply_override(raw_interrupt(i), (pins_[i].ctrl >> 28) & 3u); }
    bool input_value(uint32_t i) const noexcept {
        const Pin& p = pins_[i];
        return apply_override(eff_raw_input(i) && (p.pad_value & pin_bits::kPadInputEnable) != 0, (p.ctrl >> 16) & 3u);
    }

    // IO_BANK0's GPIOn_STATUS; false: a source failed.
    bool status(uint32_t i, uint32_t* out) const noexcept {
        const Pin& p = pins_[i];
        const uint32_t fsel = p.ctrl & 0x1F;
        const bool raw_int = raw_interrupt(i);
        const bool irq_v = apply_override(raw_int, (p.ctrl >> 28) & 3u);
        const bool eff_in = eff_raw_input(i);
        const bool in_v = input_value(i);
        bool roe = false;
        bool rov = false;
        if (!raw_output_enable(i, fsel, &roe)) return false;
        const bool oe = apply_override(roe, (p.ctrl >> 12) & 3u);
        if (!raw_output_value(i, fsel, &rov)) return false;
        const bool ov = apply_override(rov, (p.ctrl >> 8) & 3u);
        *out = (irq_v ? 1u << 26 : 0u) | (raw_int ? 1u << 24 : 0u) | (in_v ? 1u << 19 : 0u) | (eff_in ? 1u << 17 : 0u) |
               (oe ? 1u << 13 : 0u) | (roe ? 1u << 12 : 0u) | (ov ? 1u << 9 : 0u) | (rov ? 1u << 8 : 0u);
        return true;
    }

    // One bit per pin: what SIO's GPIO_IN returns (rp2040.gpio_values).
    uint32_t input_values() const noexcept {
        uint32_t result = 0;
        for (uint32_t i = 0; i < count_; ++i)
            if (input_value(i)) result |= 1u << i;
        return result;
    }
    // Is any pin's IRQ value set: the level of the IO_BANK0 interrupt line.
    bool any_irq_value() const noexcept {
        for (uint32_t i = 0; i < count_; ++i)
            if (irq_value(i)) return true;
        return false;
    }

    // --- updates ----------------------------------------------------------------------------------------------------------

    // Announces a change of the pin's state code to the host - once, recording it first.
    bool check_for_updates(uint32_t i) noexcept {
        int s = 0;
        if (!state_code(i, &s)) return false;
        Pin& p = pins_[i];
        const int last = p.last_state;
        if (s == last) return true;
        p.last_state = s;
        for (uint32_t n = 0; n < p.direct_count; ++n)
            if (!p.direct[n].fn(p.direct[n].ctx, p.index, s, last)) return false;
        return host_.on_change == nullptr || host_.on_change(host_.ctx, p.index, s, last);
    }

    bool set_input_value(uint32_t i, bool value) noexcept {
        pins_[i].driven = true;
        return apply_input_value(i, value);
    }
    bool release_input(uint32_t i) noexcept {
        pins_[i].driven = false;
        return apply_input_value(i, eff_raw_input(i));
    }
    bool refresh_input(uint32_t i) noexcept { return apply_input_value(i, pins_[i].raw_input_value); }

    bool apply_input_value(uint32_t i, bool value) noexcept {
        Pin& p = pins_[i];
        p.raw_input_value = value;
        const bool prev_irq_value = irq_value(i);
        if (value && (p.pad_value & pin_bits::kPadInputEnable)) {
            p.irq_status |= pin_bits::kIrqEdgeHigh | pin_bits::kIrqLevelHigh;
            p.irq_status &= ~pin_bits::kIrqLevelLow;
        } else {
            p.irq_status |= pin_bits::kIrqEdgeLow | pin_bits::kIrqLevelLow;
            p.irq_status &= ~pin_bits::kIrqLevelHigh;
        }
        if (irq_value(i) != prev_irq_value && !io_interrupt()) return false;
        return host_.input_changed == nullptr || host_.input_changed(host_.ctx, p.index, (p.ctrl & 0x1F) == kFuncPwm);
    }

    // INTR write: acknowledge edge interrupts (`value` is the raw written nibble).
    bool update_irq_value(uint32_t i, uint32_t value) noexcept {
        Pin& p = pins_[i];
        if ((value & pin_bits::kIrqEdgeLow) && (p.irq_status & pin_bits::kIrqEdgeLow)) {
            p.irq_status &= ~pin_bits::kIrqEdgeLow;
            if (!io_interrupt()) return false;
        }
        if ((value & pin_bits::kIrqEdgeHigh) && (p.irq_status & pin_bits::kIrqEdgeHigh)) {
            p.irq_status &= ~pin_bits::kIrqEdgeHigh;
            if (!io_interrupt()) return false;
        }
        return true;
    }

    // `GPIOPin.reset(io, pads)`: the chip's own registers back to power-on, what is wired to the pin left alone. `last_state` is
    // recomputed *after* the registers are back, so no spurious edge is announced for a transition the pin never made.
    bool reset(uint32_t i, bool io, bool pads) noexcept {
        Pin& p = pins_[i];
        if (io) {
            p.ctrl = pin_bits::kDefaultCtrl;
            p.irq_enable_mask = 0;
            p.irq_force_mask = 0;
            p.irq_status = 0;
        }
        if (pads) p.pad_value = pin_bits::kDefaultPad;
        int code = 0;
        if (!state_code(i, &code)) return false;
        p.last_state = code;
        return true;
    }

    bool io_interrupt() const noexcept { return host_.io_interrupt == nullptr || host_.io_interrupt(host_.ctx); }

private:
    static bool source_for(uint32_t fsel, bool value, uint32_t* out) noexcept {
        switch (fsel) {
            case kFuncPwm: *out = value ? kSrcPwmValue : kSrcPwmDirection; return true;
            case kFuncSio: *out = value ? kSrcSioValue : kSrcSioOe; return true;
            case kFuncPio0: *out = value ? kSrcPio0Value : kSrcPio0Oe; return true;
            case kFuncPio1: *out = value ? kSrcPio1Value : kSrcPio1Oe; return true;
            default: return false;
        }
    }

    bool read_source(uint32_t source, uint32_t* out) const noexcept {
        if (direct_[source] != nullptr) {
            *out = *direct_[source];
            return true;
        }
        if (host_.source == nullptr) {  // nothing wired to supply it: that block is absent, its pins read 0
            *out = 0;
            return true;
        }
        return host_.source(host_.ctx, source, out);
    }

    Pin pins_[kMaxPins];
    uint32_t count_ = 0;
    PinHost host_;
    const uint32_t* direct_[kPinSources] = {};
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PIN_HPP
