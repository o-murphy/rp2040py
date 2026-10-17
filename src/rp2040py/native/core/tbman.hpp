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


namespace tbman_regs {
// (`REG_PLATFORM`, not `PLATFORM`: on Windows Python's pyconfig.h defines the macro PLATFORM as "win32", and MSVC then reads the constant as a string literal.)
constexpr uint32_t REG_PLATFORM = 0x0;
constexpr uint32_t ASIC = 1;
}  // namespace tbman_regs

class TbmanBlock : public RegisterBlock<TbmanBlock> {
public:
    TbmanBlock() = default;
    TbmanBlock(const TbmanBlock&) = delete;  // the window handler holds a pointer to this object
    TbmanBlock& operator=(const TbmanBlock&) = delete;

    void init(const ChipPorts* ports, uint32_t block) noexcept { port_.init(ports, block); }


    // The block holds no state: nothing to put back.
    bool reset() noexcept { return true; }

    uint32_t read(uint32_t offset) noexcept {
        if (offset == tbman_regs::REG_PLATFORM) return tbman_regs::ASIC;
        port_.unimplemented_read(offset);
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        if (offset == tbman_regs::REG_PLATFORM) return true;  // read-only: no effect
        port_.unimplemented_write(offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step.


private:
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_TBMAN_HPP
