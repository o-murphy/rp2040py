// The RP2040 RESETS block (datasheet 2.14.3) in C++ (docs/records/0096-cpp-mcu-core.md): the reset controller's three 25-bit registers. The pure-Python reference is `peripherals/_reset.py`; the
// lockstep differential of `tests/test_psm_xosc_resets_diff.py` holds the two to the same behaviour.
//
//   - RESET and WDSEL keep bits 24:0 and reset to 0 (the datasheet resets RESET to 0x1FFFFFF; the model does not gate a peripheral on its bit and starts with all of them out of reset - documented in
//     the reference); `wdsel` is what a watchdog reset reads (`RP2040.reset()`);
//   - RESET_DONE = ~RESET & 0x1FFFFFF (a peripheral out of reset is done); a write to it is ignored;
//   - no `reset()`: the reference has none; an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write; an alias write decodes against a *read* of the register.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_RESETS_HPP
#define RP2040PY_CORE_RESETS_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {


namespace resets_regs {
constexpr uint32_t REG_RESET = 0x0, REG_WDSEL = 0x4, REG_RESET_DONE = 0x8, BITS_MASK = 0x01FFFFFF;
}  // namespace resets_regs

class ResetsBlock : public RegisterBlock<ResetsBlock> {
public:
    ResetsBlock() = default;
    ResetsBlock(const ResetsBlock&) = delete;  // the window handler holds a pointer to this object
    ResetsBlock& operator=(const ResetsBlock&) = delete;

    void init(const ChipPorts* ports, uint32_t block) noexcept { port_.init(ports, block); }


    uint32_t reset_bits = 0;  // the RESET register
    uint32_t wdsel = 0;

    // The registers as one flat array: `reset_bits`, `wdsel`.
    static constexpr uint32_t kSnapshotWords = 2;
    void snapshot(uint32_t* out) const noexcept {
        out[0] = reset_bits;
        out[1] = wdsel;
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace resets_regs;
        if (offset == REG_RESET) return reset_bits;
        if (offset == REG_WDSEL) return wdsel;
        if (offset == REG_RESET_DONE) return ~reset_bits & BITS_MASK;
        port_.unimplemented_read(offset);
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace resets_regs;
        const uint32_t v = static_cast<uint32_t>(value) & BITS_MASK;
        if (offset == REG_RESET) {
            reset_bits = v;
        } else if (offset == REG_WDSEL) {
            wdsel = v;
        } else if (offset == REG_RESET_DONE) {
            // read-only: no effect
        } else {
            port_.unimplemented_write(offset, value);
        }
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step.


private:
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_RESETS_HPP
