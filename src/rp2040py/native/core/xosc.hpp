// The RP2040 XOSC block (datasheet 2.16.7) in C++ (docs/records/0096-cpp-mcu-core.md): the crystal oscillator's control, status, dormant, startup and count registers. The pure-Python reference is
// `peripherals/_xosc.py`; the lockstep differential of `tests/test_psm_xosc_resets_diff.py` holds the two to the same behaviour, and the reference's comments carry the sources.
//
//   - CTRL keeps bits 23:0 with FREQ_RANGE (11:0) fixed at 0xAA0: another value sets STATUS.BADWRITE and warns; ENABLE (23:12) 0xFAB enables (unless dormant; the model is stable at once), 0xD1E disables, any
//     other non-zero value sets BADWRITE and warns without changing the state (the datasheet says an invalid setting enables; that step is not independently sourced and the reference keeps its behaviour);
//   - STATUS = STABLE (31) | BADWRITE (24, write 1 to clear) | ENABLED (12); DORMANT: 0x636F6D61 stops the oscillator, any other write selects WAKE (0x77616B65, the reset value) and a value that is not
//     WAKE sets BADWRITE and warns; STARTUP keeps X4 (20) and DELAY (13:0), resets to 0xC4; COUNT keeps 8 bits and never counts down (a documented gap);
//   - `reset()` (the power-on reset, gated on PSM.WDSEL's XOSC bit by `RP2040.reset()`) puts every register and flag back; an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write;
//   - an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_XOSC_HPP
#define RP2040PY_CORE_XOSC_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

constexpr uint32_t kXoscWarnRead = kRegWarnRead, kXoscWarnReadAtomicArea = kRegWarnReadAtomicArea, kXoscWarnWrite = kRegWarnWrite;
// Warnings that carry data beyond the three of `RegWarn` (the host formats the message): `value` is the offending field.
constexpr uint32_t kXoscWarnInvalidFreqRange = 3, kXoscWarnInvalidEnable = 4, kXoscWarnInvalidDormant = 5;

struct XoscHost {
    RegWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace xosc_regs {
constexpr uint32_t REG_CTRL = 0x00, REG_STATUS = 0x04, REG_DORMANT = 0x08, REG_STARTUP = 0x0C, REG_COUNT = 0x1C;
constexpr uint32_t CTRL_MASK = 0x00FFFFFF, CTRL_ENABLE_LSB = 12, CTRL_ENABLE_BITS = 0x00FFF000, CTRL_FREQ_RANGE_BITS = 0x00000FFF, CTRL_FREQ_RANGE_1_15MHZ = 0xAA0;
constexpr uint32_t CTRL_ENABLE_DISABLE = 0xD1E, CTRL_ENABLE_ENABLE = 0xFAB;
constexpr uint32_t STATUS_STABLE = 0x80000000u, STATUS_BADWRITE = 0x01000000u, STATUS_ENABLED = 0x00001000u;
constexpr uint32_t DORMANT_VALUE = 0x636F6D61u, WAKE_VALUE = 0x77616B65u;
constexpr uint32_t STARTUP_RESET = 0xC4, STARTUP_X4 = 0x00100000u, STARTUP_DELAY_BITS = 0x00003FFFu;
}  // namespace xosc_regs

class XoscBlock {
public:
    XoscBlock() = default;
    XoscBlock(const XoscBlock&) = delete;  // the window handler holds a pointer to this object
    XoscBlock& operator=(const XoscBlock&) = delete;

    void init(const XoscHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    uint32_t ctrl = xosc_regs::CTRL_FREQ_RANGE_1_15MHZ;
    uint32_t status = 0;  // BADWRITE only; STABLE and ENABLED are derived from the flags
    uint32_t dormant = xosc_regs::WAKE_VALUE;
    uint32_t startup = xosc_regs::STARTUP_RESET;
    uint32_t count = 0;
    bool enabled = false;
    bool stable = false;
    bool is_dormant = false;

    // Back to power-on: disabled, not stable, not dormant.
    bool reset() noexcept {
        using namespace xosc_regs;
        ctrl = CTRL_FREQ_RANGE_1_15MHZ;
        status = 0;
        dormant = WAKE_VALUE;
        startup = STARTUP_RESET;
        count = 0;
        enabled = false;
        stable = false;
        is_dormant = false;
        return true;
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace xosc_regs;
        if (offset == REG_CTRL) return ctrl;
        if (offset == REG_STATUS) {
            uint32_t value = status;
            if (stable) value |= STATUS_STABLE;
            if (enabled) value |= STATUS_ENABLED;
            return value;
        }
        if (offset == REG_DORMANT) return dormant;
        if (offset == REG_STARTUP) return startup;
        if (offset == REG_COUNT) return count;
        if (host_.warn) {
            host_.warn(host_.ctx, kXoscWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kXoscWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace xosc_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        if (offset == REG_CTRL) {
            const uint32_t masked = v & CTRL_MASK;
            if ((masked & CTRL_FREQ_RANGE_BITS) != CTRL_FREQ_RANGE_1_15MHZ) {
                status |= STATUS_BADWRITE;
                if (host_.warn) host_.warn(host_.ctx, kXoscWarnInvalidFreqRange, offset, masked & CTRL_FREQ_RANGE_BITS);
            }
            ctrl = (masked & CTRL_ENABLE_BITS) | CTRL_FREQ_RANGE_1_15MHZ;
            const uint32_t enable_value = (masked & CTRL_ENABLE_BITS) >> CTRL_ENABLE_LSB;
            if (enable_value == CTRL_ENABLE_ENABLE) {
                if (!is_dormant) {
                    enabled = true;
                    stable = true;  // the model is stable at once
                }
            } else if (enable_value == CTRL_ENABLE_DISABLE) {
                enabled = false;
                stable = false;
            } else if (enable_value != 0) {
                status |= STATUS_BADWRITE;
                if (host_.warn) host_.warn(host_.ctx, kXoscWarnInvalidEnable, offset, enable_value);
            }
        } else if (offset == REG_STATUS) {
            if (v & STATUS_BADWRITE) status &= ~STATUS_BADWRITE;  // write 1 to clear
        } else if (offset == REG_DORMANT) {
            if (v == DORMANT_VALUE) {
                is_dormant = true;
                stable = false;
                dormant = v;
            } else {
                if (v != WAKE_VALUE) {
                    status |= STATUS_BADWRITE;
                    if (host_.warn) host_.warn(host_.ctx, kXoscWarnInvalidDormant, offset, v);
                }
                is_dormant = false;
                if (enabled) stable = true;
                dormant = WAKE_VALUE;
            }
        } else if (offset == REG_STARTUP) {
            startup = v & (STARTUP_X4 | STARTUP_DELAY_BITS);
        } else if (offset == REG_COUNT) {
            count = v & 0xFF;  // the countdown is not implemented
        } else if (host_.warn) {
            host_.warn(host_.ctx, kXoscWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<XoscBlock>::handler(this); }

private:
    XoscHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_XOSC_HPP
