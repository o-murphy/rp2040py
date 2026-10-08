// The RP2040 SYSCFG block (datasheet 2.21) in C++ (docs/records/0096-cpp-mcu-core.md): processor configuration registers. The pure-Python reference is `peripherals/_syscfg.py`; the lockstep
// differential of `tests/test_small_blocks_diff.py` holds the two to the same behaviour.
//
//   - PROC0_NMI_MASK is the C++ `Cpu`'s own `interrupt_nmi_mask` (the block owns no copy of it); the other six registers are stored with the datasheet's widths and reset values:
//     PROC1_NMI_MASK 32 bits, PROC_CONFIG (DAP instance ids 31:24 writable, reset 0x10000000; HALTED 1:0 read 0), the two input-synchroniser bypasses (30 and 6 bits), DBGFORCE (0xEE
//     writable, reset 0x66) and MEMPOWERDOWN (8 bits);
//   - `reset()` (RESETS bit 18) puts every register back, the NMI mask included; an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write;
//   - an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_SYSCFG_HPP
#define RP2040PY_CORE_SYSCFG_HPP

#include <cstdint>

#include "core_host.hpp"
#include "cpu.hpp"

namespace rp2040core {

constexpr uint32_t kSyscfgWarnRead = kRegWarnRead, kSyscfgWarnReadAtomicArea = kRegWarnReadAtomicArea, kSyscfgWarnWrite = kRegWarnWrite;

struct SyscfgHost {
    RegWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace syscfg_regs {
constexpr uint32_t PROC0_NMI_MASK = 0x00, PROC1_NMI_MASK = 0x04, PROC_CONFIG = 0x08, PROC_IN_SYNC_BYPASS = 0x0C, PROC_IN_SYNC_BYPASS_HI = 0x10, DBGFORCE = 0x14, MEMPOWERDOWN = 0x18;
constexpr uint32_t PROC_CONFIG_WRITABLE = 0xFF000000u, PROC_CONFIG_RESET = 0x10000000u;
constexpr uint32_t PROC_IN_SYNC_BYPASS_MASK = 0x3FFFFFFFu, PROC_IN_SYNC_BYPASS_HI_MASK = 0x3Fu;
constexpr uint32_t DBGFORCE_WRITABLE = 0xEEu, DBGFORCE_RESET = 0x66u, MEMPOWERDOWN_MASK = 0xFFu;
}  // namespace syscfg_regs

class SyscfgBlock {
public:
    SyscfgBlock() = default;
    SyscfgBlock(const SyscfgBlock&) = delete;  // the window handler holds a pointer to this object
    SyscfgBlock& operator=(const SyscfgBlock&) = delete;

    // `cpu` is the core whose NMI mask PROC0_NMI_MASK is (not owned).
    void init(Cpu* cpu, const SyscfgHost& host) noexcept {
        cpu_ = cpu;
        host_ = host;
    }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    uint32_t proc1_nmi_mask = 0;
    uint32_t proc_config = syscfg_regs::PROC_CONFIG_RESET;
    uint32_t proc_in_sync_bypass = 0;
    uint32_t proc_in_sync_bypass_hi = 0;
    uint32_t dbgforce = syscfg_regs::DBGFORCE_RESET;
    uint32_t mempowerdown = 0;

    bool reset() noexcept {
        using namespace syscfg_regs;
        cpu_->interrupt_nmi_mask = 0;
        proc1_nmi_mask = 0;
        proc_config = PROC_CONFIG_RESET;
        proc_in_sync_bypass = 0;
        proc_in_sync_bypass_hi = 0;
        dbgforce = DBGFORCE_RESET;
        mempowerdown = 0;
        return true;
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace syscfg_regs;
        switch (offset) {
            case PROC0_NMI_MASK: return cpu_->interrupt_nmi_mask;
            case PROC1_NMI_MASK: return proc1_nmi_mask;
            case PROC_CONFIG: return proc_config;
            case PROC_IN_SYNC_BYPASS: return proc_in_sync_bypass;
            case PROC_IN_SYNC_BYPASS_HI: return proc_in_sync_bypass_hi;
            case DBGFORCE: return dbgforce;
            case MEMPOWERDOWN: return mempowerdown;
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kSyscfgWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kSyscfgWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace syscfg_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        switch (offset) {
            case PROC0_NMI_MASK: cpu_->interrupt_nmi_mask = v; break;
            case PROC1_NMI_MASK: proc1_nmi_mask = v; break;
            case PROC_CONFIG: proc_config = (proc_config & ~PROC_CONFIG_WRITABLE) | (v & PROC_CONFIG_WRITABLE); break;
            case PROC_IN_SYNC_BYPASS: proc_in_sync_bypass = v & PROC_IN_SYNC_BYPASS_MASK; break;
            case PROC_IN_SYNC_BYPASS_HI: proc_in_sync_bypass_hi = v & PROC_IN_SYNC_BYPASS_HI_MASK; break;
            case DBGFORCE: dbgforce = (dbgforce & ~DBGFORCE_WRITABLE) | (v & DBGFORCE_WRITABLE); break;
            case MEMPOWERDOWN: mempowerdown = v & MEMPOWERDOWN_MASK; break;
            default:
                if (host_.warn) host_.warn(host_.ctx, kSyscfgWarnWrite, offset, value);
                break;
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

    WindowHandler window_handler() noexcept { return BlockWindow<SyscfgBlock>::handler(this); }

private:
    Cpu* cpu_ = nullptr;
    SyscfgHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_SYSCFG_HPP
