// The RP2040 BUSCTRL block (datasheet 2.1.5) in C++ (docs/records/0096-cpp-mcu-core.md): the bus-priority register and four performance counters with their event selectors. The pure-Python
// reference is `peripherals/_busctrl.py`; the lockstep differential of `tests/test_small_blocks_diff.py` holds the two to the same behaviour.
//
//   - BUS_PRIORITY keeps bits 0, 4, 8 and 12 (PROC0, PROC1, DMA_R, DMA_W); BUS_PRIORITY_ACK reads 1 and a write to it is ignored;
//   - PERFCTRn reads its counter (nothing counts: there is no bus fabric to observe) and any write clears it; PERFSELn keeps 5 bits and resets to 0x1F;
//   - `reset()` (RESETS bit 1) puts every register back; an unimplemented offset warns on a read (and reads 0xFFFFFFFF) and on a write;
//   - an alias write decodes against a *read* of the register, as the reference's `BasePeripheral.write_uint32_atomic` does.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_BUSCTRL_HPP
#define RP2040PY_CORE_BUSCTRL_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

constexpr uint32_t kBusctrlWarnRead = kRegWarnRead, kBusctrlWarnReadAtomicArea = kRegWarnReadAtomicArea, kBusctrlWarnWrite = kRegWarnWrite;

struct BusctrlHost {
    RegWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace busctrl_regs {
constexpr uint32_t BUS_PRIORITY = 0x00, BUS_PRIORITY_ACK = 0x04, PERFCTR0 = 0x08, PERFSEL3 = 0x24;
[[maybe_unused]] constexpr uint32_t PERFSEL0 = 0x0C;  // only the C++ checks (and the shell) name it
constexpr uint32_t BUS_PRIORITY_MASK = 0x1111, PERFSEL_MASK = 0x1F;
}  // namespace busctrl_regs

class BusctrlBlock {
public:
    BusctrlBlock() { reset(); }
    BusctrlBlock(const BusctrlBlock&) = delete;  // the window handler holds a pointer to this object
    BusctrlBlock& operator=(const BusctrlBlock&) = delete;

    void init(const BusctrlHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // The registers the block owns, for the shell's attributes.
    uint32_t bus_priority;  // all set by the constructor, through reset()
    uint32_t perf_ctr[4];
    uint32_t perf_sel[4];

    bool reset() noexcept {
        bus_priority = 0;
        for (int i = 0; i < 4; ++i) {
            perf_ctr[i] = 0;
            perf_sel[i] = busctrl_regs::PERFSEL_MASK;
        }
        return true;
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace busctrl_regs;
        if (offset == BUS_PRIORITY) return bus_priority;
        if (offset == BUS_PRIORITY_ACK) return 1;
        if (offset >= PERFCTR0 && offset <= PERFSEL3 && (offset & 3) == 0) {
            const uint32_t index = (offset - PERFCTR0) >> 3;
            return (offset & 4) ? perf_sel[index] : perf_ctr[index];
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kBusctrlWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kBusctrlWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace busctrl_regs;
        if (offset == BUS_PRIORITY) {
            bus_priority = static_cast<uint32_t>(value) & BUS_PRIORITY_MASK;
        } else if (offset == BUS_PRIORITY_ACK) {
            // read-only: no effect
        } else if (offset >= PERFCTR0 && offset <= PERFSEL3 && (offset & 3) == 0) {
            const uint32_t index = (offset - PERFCTR0) >> 3;
            if (offset & 4)
                perf_sel[index] = static_cast<uint32_t>(value) & PERFSEL_MASK;
            else
                perf_ctr[index] = 0;
        } else if (host_.warn) {
            host_.warn(host_.ctx, kBusctrlWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<BusctrlBlock>::handler(this); }

private:
    BusctrlHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_BUSCTRL_HPP
