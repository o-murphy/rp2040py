// The CYW43439's gSPI bit shifter in C++ (docs/records/0096-cpp-mcu-core.md, Phase 3): a faithful translation of the *edge-level* part of
// external/cyw43/bus.py's `GSPIBus` - `_on_cs_change`, `_on_clock_rising`, `_on_clock_falling` and the listeners of `attach_gpio` - which
// stays as the pure-Python implementation and the oracle.
//
// What it owns: the bit-level state only - whether CS is asserted, the 32-bit shift register and its bit count, and the response being
// shifted out (a byte buffer with a bit index). What it does not own: the protocol. A completed 32-bit word, and a change of CS, are handed
// to the host (`GspiHost`, Python today) - `_word()` transform, header decode, write accumulation, register access, SDPCM framing and the
// NAT bridge all stay Python. A response comes back from the host as bytes (`start_response`), which is how the shifter learns what to drive.
//
// It is attached as *direct listeners* of the CLK and CS pins (core/pin.hpp), so an edge never enters Python: it samples the DATA pin with
// `state_code()` (the level the host drives, `data_pin.value == HIGH` in the Python) and drives it with `set_input_value()`. On the Pico W
// scan that turns ~4M Python listener calls into one host call per 32-bit word.
//
// Every observable of the Python edge methods is kept, in the same order: CS resets the shifter *before* the host hears about it; a rising
// edge samples DATA first (a failing source stops there) and then does nothing if idle or answering; a falling edge with a response active
// releases the buffer when its last bit has been taken, *before* the bit is driven; any failure (a pin call, the host) stops everything at
// once and is reported as `false`, the failure being pending with the caller - as an exception would stop the Python.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_GSPI_HPP
#define RP2040PY_CORE_GSPI_HPP

#include <cstdint>

#include "pin.hpp"

namespace rp2040core {

using GspiWordFn = bool (*)(void* ctx, uint32_t word);        // a complete 32-bit word was shifted in; false: the host failed
using GspiCsFn = bool (*)(void* ctx, bool selected);          // CS changed (already reset here); false: the host failed

struct GspiHost {
    GspiWordFn on_word = nullptr;
    GspiCsFn on_cs = nullptr;
    void* ctx = nullptr;
};

class GspiShifter {
public:
    // A read is at most 2047 bytes plus the backplane read pad; the protocol never produces more. Longer is refused, not truncated.
    static constexpr uint32_t kResponseCapacity = 4096;

    GspiShifter() = default;
    GspiShifter(const GspiShifter&) = delete;  // the pins hold a pointer to this object
    GspiShifter& operator=(const GspiShifter&) = delete;

    // Binds the shifter to its pins (each pin is position 0 of its own bank, one `GPIOPin` each) and registers it as a direct listener of CLK
    // and CS. `data` is the pin it samples (the level the host drives) and drives (the chip's side).
    bool attach(PinBank* clk, PinBank* data, PinBank* cs, const GspiHost& host) noexcept {
        clk_ = clk;
        data_ = data;
        cs_ = cs;
        host_ = host;
        clear();
        if (!clk_->add_direct_listener(0, &GspiShifter::clk_listener, this)) return false;
        if (!cs_->add_direct_listener(0, &GspiShifter::cs_listener, this)) {
            clk_->remove_direct_listener(0, &GspiShifter::clk_listener, this);
            return false;
        }
        return true;
    }

    // Unregisters from the pins; the owner calls it before the shifter (or a pin) goes away.
    void detach() noexcept {
        if (clk_ != nullptr) clk_->remove_direct_listener(0, &GspiShifter::clk_listener, this);
        if (cs_ != nullptr) cs_->remove_direct_listener(0, &GspiShifter::cs_listener, this);
        clk_ = data_ = cs_ = nullptr;
    }

    // The host's answer to a header: the bytes to shift out, MSB first, one bit per falling clock edge. An empty response means "nothing to
    // say" (the Python treats an empty `_response_bytes` the same way). False if it does not fit.
    bool start_response(const uint8_t* bytes, uint32_t length) noexcept {
        if (length > kResponseCapacity) return false;
        for (uint32_t i = 0; i < length; ++i) response_[i] = bytes[i];
        response_length_ = length;
        response_bit_ = 0;
        return true;
    }

    bool selected() const noexcept { return selected_; }
    uint32_t bits_in_word() const noexcept { return bits_in_word_; }
    uint32_t shift_register() const noexcept { return shift_reg_; }
    bool responding() const noexcept { return response_length_ != 0; }

    // --- the three edge methods of bus.py -----------------------------------------------------------------------------------

    bool on_cs_change(bool selected) noexcept {
        clear();
        selected_ = selected;
        return host_.on_cs == nullptr || host_.on_cs(host_.ctx, selected);
    }

    // `sampled_bit` is `data_pin.value == HIGH`, evaluated by the caller *before* the call (as the Python listener does).
    bool on_clock_rising(bool sampled_bit) noexcept {
        if (!selected_ || response_length_ != 0) return true;  // idle, or we are the one driving: nothing to sample
        shift_reg_ = (shift_reg_ << 1) | (sampled_bit ? 1u : 0u);
        ++bits_in_word_;
        if (bits_in_word_ < 32) return true;
        const uint32_t word = shift_reg_;
        shift_reg_ = 0;
        bits_in_word_ = 0;
        return host_.on_word == nullptr || host_.on_word(host_.ctx, word);
    }

    // Returns the bit to drive onto DATA, if any, through `*bit` (true: there is one).
    bool next_response_bit(bool* bit) noexcept {
        if (!selected_ || response_length_ == 0) return false;
        const uint32_t byte_index = response_bit_ >> 3;
        const uint32_t bit_in_byte = response_bit_ & 7u;
        if (byte_index >= response_length_) return false;
        *bit = (response_[byte_index] & (0x80u >> bit_in_byte)) != 0;
        ++response_bit_;
        if (response_bit_ >= response_length_ * 8u) {
            response_length_ = 0;
            response_bit_ = 0;
        }
        return true;
    }

private:
    void clear() noexcept {
        selected_ = false;
        shift_reg_ = 0;
        bits_in_word_ = 0;
        response_length_ = 0;
        response_bit_ = 0;
    }

    static bool cs_listener(void* ctx, uint32_t, int new_state, int) noexcept {
        // Active low: CS is asserted (selected) whenever the RP2040 does not drive it HIGH.
        return static_cast<GspiShifter*>(ctx)->on_cs_change(new_state != kPinHigh);
    }

    static bool clk_listener(void* ctx, uint32_t, int new_state, int old_state) noexcept {
        GspiShifter* self = static_cast<GspiShifter*>(ctx);
        if (new_state == kPinHigh && old_state != kPinHigh) {
            int level = 0;
            if (!self->data_->state_code(0, &level)) return false;
            return self->on_clock_rising(level == kPinHigh);
        }
        if (new_state != kPinHigh && old_state == kPinHigh) {
            bool bit = false;
            if (self->next_response_bit(&bit)) return self->data_->set_input_value(0, bit);
        }
        return true;
    }

    PinBank* clk_ = nullptr;
    PinBank* data_ = nullptr;
    PinBank* cs_ = nullptr;
    GspiHost host_;
    bool selected_ = false;
    uint32_t shift_reg_ = 0;
    uint32_t bits_in_word_ = 0;
    uint8_t response_[kResponseCapacity];
    uint32_t response_length_ = 0;
    uint32_t response_bit_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_GSPI_HPP
