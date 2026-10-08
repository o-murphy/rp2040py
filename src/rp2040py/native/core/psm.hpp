// The RP2040 PSM block (datasheet 2.13.5) in C++ (docs/records/0096-cpp-mcu-core.md): the power-on state machine's three 17-bit registers. The pure-Python reference is `peripherals/_psm.py`; the
// lockstep differential of `tests/test_psm_xosc_resets_diff.py` holds the two to the same behaviour.
//
//   - FRCE_ON, FRCE_OFF and WDSEL keep bits 16:0 (reset 0); `wdsel` is what a watchdog reset reads to decide which domains it resets (`RP2040.reset()`);
//   - REG_DONE = (MASK & ~FRCE_OFF) | (FRCE_ON & FRCE_OFF): a domain is ready unless it is forced off, and FRCE_ON overrides FRCE_OFF; a write to it is ignored;
//   - no `reset()`: the reference has none (the block is not in the RESETS list); an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write;
//   - an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_PSM_HPP
#define RP2040PY_CORE_PSM_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

constexpr uint32_t kPsmWarnRead = kRegWarnRead, kPsmWarnReadAtomicArea = kRegWarnReadAtomicArea, kPsmWarnWrite = kRegWarnWrite;

struct PsmHost {
    RegWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace psm_regs {
constexpr uint32_t FRCE_ON = 0x00, FRCE_OFF = 0x04, REG_WDSEL = 0x08, REG_DONE = 0x0C, BITS_MASK = 0x0001FFFF;
}  // namespace psm_regs

class PsmBlock {
public:
    PsmBlock() = default;
    PsmBlock(const PsmBlock&) = delete;  // the window handler holds a pointer to this object
    PsmBlock& operator=(const PsmBlock&) = delete;

    void init(const PsmHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    uint32_t frce_on = 0;
    uint32_t frce_off = 0;
    uint32_t wdsel = 0;

    uint32_t read(uint32_t offset) noexcept {
        using namespace psm_regs;
        if (offset == FRCE_ON) return frce_on;
        if (offset == FRCE_OFF) return frce_off;
        if (offset == REG_WDSEL) return wdsel;
        if (offset == REG_DONE) return (BITS_MASK & ~frce_off) | (frce_on & frce_off);
        if (host_.warn) {
            host_.warn(host_.ctx, kPsmWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kPsmWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace psm_regs;
        const uint32_t v = static_cast<uint32_t>(value) & BITS_MASK;
        if (offset == FRCE_ON) {
            frce_on = v;
        } else if (offset == FRCE_OFF) {
            frce_off = v;
        } else if (offset == REG_WDSEL) {
            wdsel = v;
        } else if (offset == REG_DONE) {
            // read-only: no effect
        } else if (host_.warn) {
            host_.warn(host_.ctx, kPsmWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<PsmBlock>::handler(this); }

private:
    PsmHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PSM_HPP
