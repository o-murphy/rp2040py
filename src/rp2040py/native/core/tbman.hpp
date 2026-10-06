// The RP2040 TBMAN block (datasheet 2.22) in C++ (docs/records/0096-cpp-mcu-core.md): one read-only register, PLATFORM, which reads 1 (ASIC). The pure-Python reference is `peripherals/_tbman.py`; the
// lockstep differential of `tests/test_ident_diff.py` holds the two to the same behaviour.
//
//   - a write to PLATFORM is ignored; a write to anything else warns; an unimplemented offset warns on a read and reads 0xFFFFFFFF (a read above 0x1000 warns a second time);
//   - an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_TBMAN_HPP
#define RP2040PY_CORE_TBMAN_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

constexpr uint32_t kTbmanWarnRead = kRegWarnRead, kTbmanWarnReadAtomicArea = kRegWarnReadAtomicArea, kTbmanWarnWrite = kRegWarnWrite;

struct TbmanHost {
    RegWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace tbman_regs {
constexpr uint32_t PLATFORM = 0x0;
constexpr uint32_t ASIC = 1;
}  // namespace tbman_regs

class TbmanBlock {
public:
    TbmanBlock() = default;
    TbmanBlock(const TbmanBlock&) = delete;  // the window handler holds a pointer to this object
    TbmanBlock& operator=(const TbmanBlock&) = delete;

    void init(const TbmanHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // The block holds no state: nothing to put back.
    bool reset() noexcept { return true; }

    uint32_t read(uint32_t offset) noexcept {
        if (offset == tbman_regs::PLATFORM) return tbman_regs::ASIC;
        if (host_.warn) {
            host_.warn(host_.ctx, kTbmanWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kTbmanWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        if (offset == tbman_regs::PLATFORM) return true;  // read-only: no effect
        if (host_.warn) host_.warn(host_.ctx, kTbmanWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<TbmanBlock>::handler(this); }

private:
    TbmanHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_TBMAN_HPP
