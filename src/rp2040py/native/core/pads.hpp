// The PADS_BANK0 / PADS_QSPI register blocks (datasheet 2.19.6.3, 2.19.6.4) in C++ (docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md "IO_BANK0"): a faithful
// translation of `peripherals/_pads.py`, which stays as the pure-Python reference and the oracle (tests/test_pin_diff.py).
//
// The per-pad registers are the `pad_value` of the `Pin`s of the shared `PinBank` (core/pin.hpp). A write masks to the eight defined bits (31:8 are reserved), announces the pin's change of
// state and then tells it the pad moved (`pad_changed`: IE or a pull on a floating pin moves the level the interrupt logic sees). What the block owns: VOLTAGE_SELECT and, for PADS_BANK0,
// the SWCLK / SWD pads (stored; there is no electrical model, docs/records/0098). PADS_QSPI lists its pads in a different order from the bank (SCLK, SD0..SD3, SS, table 344), so the
// block maps pad to pin through a table. As in the reference, any offset between the first and the last pad register reaches a pad, aligned or not.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_PADS_HPP
#define RP2040PY_CORE_PADS_HPP

#include <cstdint>

#include "core_host.hpp"
#include "io.hpp"
#include "pin.hpp"

namespace rp2040core {

namespace pads_regs {
constexpr uint32_t VOLTAGE_SELECT = 0x00, PAD_MASK = 0xFF;
constexpr uint32_t SWCLK = 0x7C, SWD = 0x80;  // PADS_BANK0 only: the debug port's two pads, no pin behind them (tables 342, 343)
constexpr uint32_t SWCLK_RESET = 0xDA, SWD_RESET = 0x5A;
constexpr uint32_t GPIO_FIRST = 0x4, GPIO_LAST = 0x78, QSPI_FIRST = 0x4, QSPI_LAST = 0x18;
constexpr uint32_t kQspiPadToPin[6] = {0, 2, 3, 4, 5, 1};  // SCLK, SD0, SD1, SD2, SD3, SS -> the bank's SCLK, SS, SD0..SD3
}  // namespace pads_regs

class PadsBlock : public RegisterBlock<PadsBlock> {
public:
    PadsBlock() = default;
    PadsBlock(const PadsBlock&) = delete;  // the window handler holds a pointer to this object
    PadsBlock& operator=(const PadsBlock&) = delete;

    void init(PinBank* bank, bool qspi, const ChipPorts* ports, uint32_t block) noexcept {
        port_.init(ports, block);
        bank_ = bank;
        qspi_ = qspi;
        first_ = qspi ? pads_regs::QSPI_FIRST : pads_regs::GPIO_FIRST;
        last_ = qspi ? pads_regs::QSPI_LAST : pads_regs::GPIO_LAST;
        reset();
    }


    // `VOLTAGE_SELECT` and the debug pads, the only state this block owns itself - the per-pad registers live on the pins and are reset with them.
    void reset() noexcept {
        voltage_select = 0;
        swclk = pads_regs::SWCLK_RESET;
        swd = pads_regs::SWD_RESET;
    }

    uint32_t voltage_select = 0, swclk = pads_regs::SWCLK_RESET, swd = pads_regs::SWD_RESET;

    // The registers the block owns as one flat array: `voltage_select`, `swclk`, `swd`.
    static constexpr uint32_t kSnapshotWords = 3;
    void snapshot(uint32_t* out) const noexcept {
        out[0] = voltage_select;
        out[1] = swclk;
        out[2] = swd;
    }

    // The alias decode asks the fallible read.
    bool decode_read(uint32_t offset, uint32_t* out) noexcept { return read(offset, out); }

    bool read(uint32_t offset, uint32_t* out) noexcept {
        using namespace pads_regs;
        if (first_ <= offset && offset <= last_) {
            *out = bank_->pin(pin_of(offset)).pad_value;
            return true;
        }
        if (offset == VOLTAGE_SELECT) {
            *out = voltage_select;
            return true;
        }
        if (!qspi_ && offset == SWCLK) {
            *out = swclk;
            return true;
        }
        if (!qspi_ && offset == SWD) {
            *out = swd;
            return true;
        }
        port_.unimplemented_read(offset);
        *out = 0xFFFFFFFFu;
        return true;
    }

    uint32_t read(uint32_t offset) noexcept {
        uint32_t value = 0;
        (void)read(offset, &value);
        return value;
    }

    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace pads_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        if (first_ <= offset && offset <= last_) {
            const uint32_t index = pin_of(offset);
            bank_->pin(index).pad_value = v & PAD_MASK;  // 31:8 are reserved
            if (!bank_->check_for_updates(index)) return false;
            return bank_->pad_changed(index);
        }
        if (offset == VOLTAGE_SELECT) {
            voltage_select = v & 1u;
        } else if (!qspi_ && offset == SWCLK) {
            swclk = v & PAD_MASK;
        } else if (!qspi_ && offset == SWD) {
            swd = v & PAD_MASK;
        } else {
            port_.unimplemented_write(offset, value);
        }
        return true;
    }



private:
    uint32_t pin_of(uint32_t offset) const noexcept {
        const uint32_t pad = (offset - first_) >> 2;
        return qspi_ ? pads_regs::kQspiPadToPin[pad] : pad;
    }

    PinBank* bank_ = nullptr;
    bool qspi_ = false;
    uint32_t first_ = 0, last_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PADS_HPP
