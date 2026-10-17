// The IO_BANK0 / IO_QSPI register blocks (datasheet 2.19.6.1, 2.19.6.2) in C++ (docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md "IO_BANK0"): a faithful
// translation of `peripherals/_io.py`, which stays as the pure-Python reference and the oracle (tests/test_pin_diff.py).
//
// It owns no pin: the per-pin registers (CTRL, STATUS, the INTR bits and proc 0's INTE / INTF masks) are the fields of the `Pin`s of the shared `PinBank` it is given (core/pin.hpp), so a
// register access is a few loads and stores and never enters Python. What it does own: proc 1's and dormant wake's INTE / INTF masks, which are stored and raise no interrupt line (there is
// no second core and no dormant mode, docs/records/0098). The layout follows the bank: IO_BANK0's interrupt groups are four words of eight pins each at 0x0F0.., IO_QSPI's six pins fit
// one word per group at 0x30.. (tables 283 and 326).
//
// Kept from the reference, pinned by the oracle: a write to a pin's STATUS word is dropped; INTR is written by the *raw* value the bus passed (`raw_write_value`, whatever the alias: only the
// edge bits are write-1-to-clear); INTS is read only; an INTE / INTF write that changes proc 0's mask re-evaluates the IO interrupt; a CTRL write announces the pin's change of state. An
// unimplemented or unaligned offset in the groups warns (and reads 0xFFFFFFFF).
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract. A failing host call (a listener, the interrupt line) parks its error with the host and the block
// stops at once, as the reference's exception would have stopped it.
#ifndef RP2040PY_CORE_IO_HPP
#define RP2040PY_CORE_IO_HPP

#include <cstdint>

#include "core_host.hpp"
#include "pin.hpp"

namespace rp2040core {

namespace io_regs {
constexpr uint32_t CTRL_MASK = 0x3003331Fu;  // IRQOVER, INOVER, OEOVER, OUTOVER, FUNCSEL (datasheet table 285); the rest is reserved
constexpr uint32_t PINS_PER_WORD = 8;
enum Kind : uint32_t { kIntr = 0, kInte = 1, kIntf = 2, kInts = 3 };
struct Group {
    uint32_t kind;
    uint32_t dest;  // 0 proc 0, 1 proc 1, 2 dormant wake (kIntr has none: 0)
    uint32_t base;
};
constexpr uint32_t kGroups = 10;
constexpr Group kBank0Groups[kGroups] = {
    {kIntr, 0, 0x0F0}, {kInte, 0, 0x100}, {kIntf, 0, 0x110}, {kInts, 0, 0x120}, {kInte, 1, 0x130},
    {kIntf, 1, 0x140}, {kInts, 1, 0x150}, {kInte, 2, 0x160}, {kIntf, 2, 0x170}, {kInts, 2, 0x180},
};
constexpr Group kQspiGroups[kGroups] = {
    {kIntr, 0, 0x30}, {kInte, 0, 0x34}, {kIntf, 0, 0x38}, {kInts, 0, 0x3C}, {kInte, 1, 0x40},
    {kIntf, 1, 0x44}, {kInts, 1, 0x48}, {kInte, 2, 0x4C}, {kIntf, 2, 0x50}, {kInts, 2, 0x54},
};
}  // namespace io_regs

class IoBlock : public RegisterBlock<IoBlock> {
public:
    IoBlock() = default;
    IoBlock(const IoBlock&) = delete;  // the window handler holds a pointer to this object
    IoBlock& operator=(const IoBlock&) = delete;

    // `qspi_layout`: IO_QSPI's interrupt groups instead of IO_BANK0's.
    void init(PinBank* bank, bool qspi_layout, const ChipPorts* ports, uint32_t block) noexcept {
        port_.init(ports, block);
        bank_ = bank;
        groups_ = qspi_layout ? io_regs::kQspiGroups : io_regs::kBank0Groups;
        words_ = (bank->count() + io_regs::PINS_PER_WORD - 1) / io_regs::PINS_PER_WORD;
        pin_registers_end_ = bank->count() * 8;
        reset();
    }


    // The stored proc 1 / dormant wake masks back to zero (proc 0's are on the pins, reset with them).
    void reset() noexcept {
        for (uint32_t d = 0; d < 2; ++d)
            for (uint32_t m = 0; m < 2; ++m)
                for (uint32_t i = 0; i < PinBank::kMaxPins; ++i) extra_[d][m][i] = 0;
    }

    // proc 1 (dest 1) and dormant wake (dest 2) masks: `force` false is INTE, true is INTF.
    uint32_t extra(uint32_t dest, bool force, uint32_t pin) const noexcept { return extra_[dest - 1][force ? 1 : 0][pin]; }

    // False: a failure is pending.
    // The alias decode asks the fallible read.
    bool decode_read(uint32_t offset, uint32_t* out) noexcept { return read(offset, out); }

    bool read(uint32_t offset, uint32_t* out) noexcept {
        using namespace io_regs;
        if (offset < pin_registers_end_) {
            const uint32_t index = offset >> 3;
            if (offset & 4u) {
                *out = bank_->pin(index).ctrl;
                return true;
            }
            return bank_->status(index, out);
        }
        const Group* g = (offset & 3u) ? nullptr : locate(offset);
        if (g == nullptr) {
            port_.unimplemented_read(offset);
            *out = 0xFFFFFFFFu;
            return true;
        }
        const uint32_t first = ((offset - g->base) >> 2) * PINS_PER_WORD;
        uint32_t result = 0;
        for (uint32_t slot = 0; slot < PINS_PER_WORD; ++slot) {
            const uint32_t index = first + slot;
            if (index >= bank_->count()) break;
            const Pin& pin = bank_->pin(index);
            uint32_t nibble = 0;
            if (g->kind == kIntr) {
                nibble = pin.irq_status;
            } else {
                const uint32_t enable = g->dest == 0 ? pin.irq_enable_mask : extra_[g->dest - 1][0][index];
                const uint32_t force = g->dest == 0 ? pin.irq_force_mask : extra_[g->dest - 1][1][index];
                nibble = g->kind == kInte ? enable : g->kind == kIntf ? force : ((pin.irq_status & enable) | force);
            }
            result |= nibble << (slot * 4);
        }
        *out = result;
        return true;
    }

    uint32_t read(uint32_t offset) noexcept {
        uint32_t value = 0;
        (void)read(offset, &value);
        return value;
    }

    // `value` is the value after any atomic-alias decode. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace io_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        if (offset < pin_registers_end_) {
            if (offset & 4u) {
                const uint32_t index = offset >> 3;
                bank_->pin(index).ctrl = v & CTRL_MASK;
                return bank_->check_for_updates(index);
            }
            return true;  // STATUS is read only
        }
        const Group* g = (offset & 3u) ? nullptr : locate(offset);
        if (g == nullptr) {
            port_.unimplemented_write(offset, value);
            return true;
        }
        const uint32_t first = ((offset - g->base) >> 2) * PINS_PER_WORD;
        for (uint32_t slot = 0; slot < PINS_PER_WORD; ++slot) {
            const uint32_t index = first + slot;
            if (index >= bank_->count()) break;
            Pin& pin = bank_->pin(index);
            const uint32_t nibble = (v >> (slot * 4)) & 0xFu;
            if (g->kind == kIntr) {
                // by the bits the bus passed, whatever the alias: only the edge bits are write-1-to-clear
                if (!bank_->update_irq_value(index, static_cast<uint32_t>((raw_write_value_ >> (slot * 4)) & 0xF))) return false;
            } else if (g->kind == kInts) {
                continue;  // read only
            } else if (g->dest == 0) {
                if (g->kind == kInte && pin.irq_enable_mask != nibble) {
                    pin.irq_enable_mask = nibble;
                    if (!bank_->io_interrupt()) return false;
                } else if (g->kind == kIntf && pin.irq_force_mask != nibble) {
                    pin.irq_force_mask = nibble;
                    if (!bank_->io_interrupt()) return false;
                }
            } else {
                extra_[g->dest - 1][g->kind == kInte ? 0 : 1][index] = nibble;
            }
        }
        return true;
    }



private:
    const io_regs::Group* locate(uint32_t offset) const noexcept {
        for (uint32_t n = 0; n < io_regs::kGroups; ++n)
            if (groups_[n].base <= offset && offset < groups_[n].base + 4 * words_) return &groups_[n];
        return nullptr;
    }

    PinBank* bank_ = nullptr;
    const io_regs::Group* groups_ = io_regs::kBank0Groups;
    uint32_t words_ = 0;
    uint32_t pin_registers_end_ = 0;
    uint32_t extra_[2][2][PinBank::kMaxPins] = {};  // [proc 1 / dormant wake][INTE / INTF][pin]
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_IO_HPP
