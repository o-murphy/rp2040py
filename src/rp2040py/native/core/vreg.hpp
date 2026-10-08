// The RP2040 VREG_AND_CHIP_RESET block (datasheet 2.10.6) in C++ (docs/records/0096-cpp-mcu-core.md): the regulator and brown-out registers (stored, nothing is regulated) and CHIP_RESET's
// "how did we get here" flags. The pure-Python reference is `peripherals/_vreg_and_chip_reset.py`; the lockstep differential of `tests/test_small_blocks_diff.py` holds the two together.
//
//   - VREG keeps VSEL 7:4, HIZ 1 and EN 0 (reset 0xB1); BOD keeps VSEL 7:4 and EN 0 (reset 0x91); ROK (VREG bit 12) reads 0, not independently sourced;
//   - CHIP_RESET (reset HAD_POR) is read-only except PSM_RESTART_FLAG (bit 24), which a write of 1 clears; `record_reset_cause()` sets one of HAD_POR / HAD_RUN / HAD_PSM_RESTART and
//     clears the other two, leaving PSM_RESTART_FLAG alone; the reference has no `reset()`, and this block has none either;
//   - an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write; an alias write decodes against a *read* of the register.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_VREG_HPP
#define RP2040PY_CORE_VREG_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

constexpr uint32_t kVregWarnRead = kRegWarnRead, kVregWarnReadAtomicArea = kRegWarnReadAtomicArea, kVregWarnWrite = kRegWarnWrite;

struct VregHost {
    RegWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace vreg_regs {
constexpr uint32_t REG_VREG = 0x0, REG_BOD = 0x4, REG_CHIP_RESET = 0x8;
constexpr uint32_t VREG_RESET = 0xB1, BOD_RESET = 0x91, VREG_WRITABLE = 0xF3, BOD_WRITABLE = 0xF1;
constexpr uint32_t HAD_POR = 1u << 8, HAD_RUN = 1u << 16, HAD_PSM_RESTART = 1u << 20, PSM_RESTART_FLAG = 1u << 24;
}  // namespace vreg_regs

class VregBlock {
public:
    VregBlock() = default;
    VregBlock(const VregBlock&) = delete;  // the window handler holds a pointer to this object
    VregBlock& operator=(const VregBlock&) = delete;

    void init(const VregHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    uint32_t vreg = vreg_regs::VREG_RESET;
    uint32_t bod = vreg_regs::BOD_RESET;
    uint32_t chip_reset = vreg_regs::HAD_POR;

    // `flag` is one of the three cause flags (the shell checks); the register keeps only PSM_RESTART_FLAG besides it.
    void record_reset_cause(uint32_t flag) noexcept { chip_reset = (chip_reset & vreg_regs::PSM_RESTART_FLAG) | flag; }

    uint32_t read(uint32_t offset) noexcept {
        using namespace vreg_regs;
        if (offset == REG_VREG) return vreg;
        if (offset == REG_BOD) return bod;
        if (offset == REG_CHIP_RESET) return chip_reset;
        if (host_.warn) {
            host_.warn(host_.ctx, kVregWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kVregWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace vreg_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        if (offset == REG_VREG) {
            vreg = (vreg & ~VREG_WRITABLE) | (v & VREG_WRITABLE);
        } else if (offset == REG_BOD) {
            bod = (bod & ~BOD_WRITABLE) | (v & BOD_WRITABLE);
        } else if (offset == REG_CHIP_RESET) {
            if (v & PSM_RESTART_FLAG) chip_reset &= ~PSM_RESTART_FLAG;  // write 1 to clear; the three status flags are read-only
        } else if (host_.warn) {
            host_.warn(host_.ctx, kVregWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<VregBlock>::handler(this); }

private:
    VregHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_VREG_HPP
