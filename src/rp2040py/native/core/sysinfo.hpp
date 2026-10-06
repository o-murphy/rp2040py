// The RP2040 SYSINFO block (datasheet 2.20) in C++ (docs/records/0096-cpp-mcu-core.md): three read-only identity registers. The pure-Python reference is `peripherals/_sysinfo.py`; the lockstep
// differential of `tests/test_ident_diff.py` holds the two to the same behaviour, and the reference's comments carry the sources.
//
// The behaviour, and the quirks kept:
//   - CHIP_ID = REVISION << 28 | PART (2) << 12 | MANUFACTURER (0x927); the revision is 2 when the loaded bootrom's version byte (ROM address 0x13) is 3 (B2) and 1 otherwise, so the host is
//     asked for that byte on every CHIP_ID read (the bootrom is loaded after the chip is built);
//   - PLATFORM reads 0x2 and GITREF_RP2040 0xE0C912E8 (both from rp2040js, not independently sourced);
//   - a write to any of the three is ignored; a write to anything else warns, and an unimplemented offset warns on a read and reads 0xFFFFFFFF (a read above 0x1000 warns a second time);
//   - an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_SYSINFO_HPP
#define RP2040PY_CORE_SYSINFO_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

using SysInfoRomVersionFn = bool (*)(void* ctx, uint32_t* version);  // the bootrom's version byte; false: failure parked
constexpr uint32_t kSysInfoWarnRead = kRegWarnRead, kSysInfoWarnReadAtomicArea = kRegWarnReadAtomicArea, kSysInfoWarnWrite = kRegWarnWrite;

struct SysInfoHost {
    SysInfoRomVersionFn rom_version = nullptr;
    RegWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace sysinfo_regs {
constexpr uint32_t CHIP_ID = 0x0, PLATFORM = 0x4, GITREF_RP2040 = 0x40;
constexpr uint32_t MANUFACTURER = 0x927, PART = 0x0002, ROM_VERSION_B2 = 3;
}  // namespace sysinfo_regs

class SysInfoBlock {
public:
    SysInfoBlock() = default;
    SysInfoBlock(const SysInfoBlock&) = delete;  // the window handler holds a pointer to this object
    SysInfoBlock& operator=(const SysInfoBlock&) = delete;

    void init(const SysInfoHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // The block holds no state: nothing to put back.
    bool reset() noexcept { return true; }

    // A failure of the host call leaves the flag raised; the value returned then is not used.
    uint32_t read(uint32_t offset) noexcept {
        using namespace sysinfo_regs;
        switch (offset) {
            case CHIP_ID: {
                uint32_t version = 0;
                if (host_.rom_version != nullptr && !host_.rom_version(host_.ctx, &version)) return 0;
                const uint32_t revision = version == ROM_VERSION_B2 ? 2u : 1u;
                return (revision << 28) | (PART << 12) | MANUFACTURER;
            }
            case PLATFORM: return 0x00000002u;
            case GITREF_RP2040: return 0xE0C912E8u;
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kSysInfoWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kSysInfoWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace sysinfo_regs;
        if (offset == CHIP_ID || offset == PLATFORM || offset == GITREF_RP2040) return true;  // read-only: no effect
        if (host_.warn) host_.warn(host_.ctx, kSysInfoWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<SysInfoBlock>::handler(this); }

private:
    SysInfoHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_SYSINFO_HPP
