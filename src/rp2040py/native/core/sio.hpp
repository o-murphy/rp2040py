// The RP2040 SIO block in C++ (docs/records/0096-cpp-mcu-core.md, Phase 2): a translation of sio.py, which stays as the
// pure-Python reference and the oracle. SIO is the second-hottest block of a MicroPython boot (46% of the accesses,
// after TIMER's 52%): spinlocks, the GPIO output registers, the hardware divider and the two interpolators.
//
// What it owns: the registers and the arithmetic. What it reaches through a `SioHost` of function pointers (a Python
// trampoline today, the C++ GPIO block and CPU in Phase 3 / step 4): the input levels of the pins, telling the pins their
// SIO-driven output changed, charging the divider's cycles to the CPU, and the logger.
//
// Quirks of the Python block that are kept on purpose, because replay of recorded firmware is the acceptance test:
//  - the divider's quotient is genuinely fractional (a `double`), as upstream rp2040js leaves it; only the bus truncates
//    it to a 32-bit integer on the way out;
//  - the "divide by zero" result tests the dividend as written (unmasked) with `> 0`;
//  - after any write, pins whose SIO-driven value or output enable changed are re-evaluated - GPIO only, never the QSPI
//    pins - and a SPINLOCK write returns before that;
//  - a write the block does not know warns with the *unmasked* value.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_SIO_HPP
#define RP2040PY_CORE_SIO_HPP

#include <cstdint>

#include "interpolator.hpp"
#include "window_map.hpp"

namespace rp2040core {

enum SioWarn : uint32_t {
    kSioWarnFifo = 0,          // "Inter-core FIFO (0x50-0x58) is not implemented. core1/_thread is unsupported (see docs/records/0053-...)"
    kSioWarnReadInvalid = 1,   // "Read from invalid SIO address: {offset:x}"
    kSioWarnWriteInvalid = 2,  // "Write to invalid SIO address: {offset:x}, value={value:x}"
    // Not a warning: the one way the Python block can raise. A divisor that is non-zero as written but zero once taken as a
    // 32-bit value (2**32, say) reaches `dividend / divisor` with 0 - ZeroDivisionError. The host turns this into that
    // exception, and the write stops there with the quotient, remainder and CSR untouched, as in Python.
    kSioFailDivideByZero = 3,
};

struct SioHost {
    uint32_t (*gpio_in)(void* ctx) = nullptr;                           // GPIO_IN: the 30 pins' effective input levels
    uint32_t (*qspi_in)(void* ctx) = nullptr;                           // GPIO_HI_IN: the 6 QSPI pins'
    bool (*update_pins)(void* ctx, uint32_t pin_mask) = nullptr;        // these pins' SIO-driven state changed; false: it failed
    void (*add_cycles)(void* ctx, uint32_t cycles) = nullptr;           // charge the CPU
    void (*warn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value) = nullptr;
    void* ctx = nullptr;
};

namespace sio_regs {
constexpr uint32_t CPUID = 0x000, GPIO_IN = 0x004, GPIO_HI_IN = 0x008;
constexpr uint32_t GPIO_OUT = 0x010, GPIO_OUT_SET = 0x014, GPIO_OUT_CLR = 0x018, GPIO_OUT_XOR = 0x01C;
constexpr uint32_t GPIO_OE = 0x020, GPIO_OE_SET = 0x024, GPIO_OE_CLR = 0x028, GPIO_OE_XOR = 0x02C;
constexpr uint32_t GPIO_HI_OUT = 0x030, GPIO_HI_OUT_SET = 0x034, GPIO_HI_OUT_CLR = 0x038, GPIO_HI_OUT_XOR = 0x03C;
constexpr uint32_t GPIO_HI_OE = 0x040, GPIO_HI_OE_SET = 0x044, GPIO_HI_OE_CLR = 0x048, GPIO_HI_OE_XOR = 0x04C;
constexpr uint32_t FIFO_ST = 0x050, FIFO_WR = 0x054, FIFO_RD = 0x058, SPINLOCK_ST = 0x05C;
constexpr uint32_t DIV_UDIVIDEND = 0x060, DIV_UDIVISOR = 0x064, DIV_SDIVIDEND = 0x068, DIV_SDIVISOR = 0x06C;
constexpr uint32_t DIV_QUOTIENT = 0x070, DIV_REMAINDER = 0x074, DIV_CSR = 0x078;
constexpr uint32_t INTERP0 = 0x080, INTERP1 = 0x0C0;  // each: 16 registers, see the lane offsets below
constexpr uint32_t SPINLOCK0 = 0x100, SPINLOCK31 = 0x17C;
// Offsets inside an interpolator's 0x40-byte register file.
constexpr uint32_t I_ACCUM0 = 0x00, I_ACCUM1 = 0x04, I_BASE0 = 0x08, I_BASE1 = 0x0C, I_BASE2 = 0x10;
constexpr uint32_t I_POP_LANE0 = 0x14, I_POP_LANE1 = 0x18, I_POP_FULL = 0x1C;
constexpr uint32_t I_PEEK_LANE0 = 0x20, I_PEEK_LANE1 = 0x24, I_PEEK_FULL = 0x28;
constexpr uint32_t I_CTRL_LANE0 = 0x2C, I_CTRL_LANE1 = 0x30, I_ACCUM0_ADD = 0x34, I_ACCUM1_ADD = 0x38, I_BASE_1AND0 = 0x3C;
constexpr uint32_t GPIO_MASK = 0x3FFFFFFF;
}  // namespace sio_regs

class SioBlock {
public:
    SioBlock() noexcept : interp_{Interpolator(0), Interpolator(1)} {}
    SioBlock(const SioBlock&) = delete;
    SioBlock& operator=(const SioBlock&) = delete;

    void init(const SioHost& host) noexcept { host_ = host; }

    // The registers the rest of the chip (and tests) read and write directly, as plain attributes of the Python block.
    uint32_t gpio_value = 0, gpio_output_enable = 0, qspi_gpio_value = 0, qspi_gpio_output_enable = 0;
    int64_t div_dividend = 0, div_divisor = 1, div_remainder = 0;
    double div_quotient = 0;
    uint32_t div_csr = 0, spin_lock = 0;

    Interpolator& interp(int index) noexcept { return interp_[index]; }

    void reset() noexcept {
        gpio_value = gpio_output_enable = qspi_gpio_value = qspi_gpio_output_enable = 0;
        div_dividend = 0;
        div_divisor = 1;
        div_quotient = 0;
        div_remainder = 0;
        div_csr = 0;
        spin_lock = 0;
        interp_[0].reset();
        interp_[1].reset();
    }

    // The hardware divider: recomputes quotient/remainder from the current operands and charges 8 CPU cycles.
    // Returns false when the divide failed (see kSioFailDivideByZero).
    bool update_hardware_divider(bool signed_division) noexcept {
        using interp_detail::s32;
        using interp_detail::u32;
        if (div_divisor != 0 && (signed_division ? s32(div_divisor) : u32(div_divisor)) == 0) {
            if (host_.warn) host_.warn(host_.ctx, kSioFailDivideByZero, 0, div_divisor);
            return false;
        }
        if (div_divisor == 0) {
            div_quotient = div_dividend > 0 ? -1.0 : 1.0;
            div_remainder = div_dividend;
        } else if (signed_division) {
            const int64_t dividend = s32(div_dividend), divisor = s32(div_divisor);
            div_quotient = static_cast<double>(dividend) / static_cast<double>(divisor);
            // JS `%`: truncating remainder, sign of the dividend - computed as the Python does, a - b*int(a/b).
            div_remainder = dividend - divisor * static_cast<int64_t>(static_cast<double>(dividend) / static_cast<double>(divisor));
        } else {
            const int64_t dividend = u32(div_dividend), divisor = u32(div_divisor);
            div_quotient = static_cast<double>(dividend) / static_cast<double>(divisor);
            div_remainder = dividend % divisor;
        }
        div_csr = 0b11;
        if (host_.add_cycles) host_.add_cycles(host_.ctx, 8);
        return true;
    }

    // The register file as a number: the divider's quotient is the one that is not an integer. (A read has side effects:
    // a spinlock acquire, the divider's quotient-read flag, an interpolator POP.)
    double read_wide(uint32_t offset) noexcept {
        using namespace sio_regs;
        if (offset >= SPINLOCK0 && offset <= SPINLOCK31) {
            const uint32_t bit = 1u << ((offset - SPINLOCK0) / 4);
            if (spin_lock & bit) return 0;
            spin_lock |= bit;
            return static_cast<double>(bit);
        }
        switch (offset) {
            case GPIO_IN: return host_.gpio_in ? static_cast<double>(host_.gpio_in(host_.ctx)) : 0.0;
            case GPIO_HI_IN: return host_.qspi_in ? static_cast<double>(host_.qspi_in(host_.ctx)) : 0.0;
            case GPIO_OUT: return gpio_value;
            case GPIO_OE: return gpio_output_enable;
            case GPIO_HI_OUT: return qspi_gpio_value;
            case GPIO_HI_OE: return qspi_gpio_output_enable;
            case GPIO_OUT_SET: case GPIO_OUT_CLR: case GPIO_OUT_XOR:
            case GPIO_OE_SET: case GPIO_OE_CLR: case GPIO_OE_XOR:
            case GPIO_HI_OUT_SET: case GPIO_HI_OUT_CLR: case GPIO_HI_OUT_XOR:
            case GPIO_HI_OE_SET: case GPIO_HI_OE_CLR: case GPIO_HI_OE_XOR:
                return 0;  // the Python block has a TODO "verify with silicone" here: the write-only aliases read as 0
            case CPUID: return 0;  // always core 0
            case SPINLOCK_ST: return spin_lock;
            case DIV_UDIVIDEND: case DIV_SDIVIDEND: return static_cast<double>(div_dividend);
            case DIV_UDIVISOR: case DIV_SDIVISOR: return static_cast<double>(div_divisor);
            case DIV_QUOTIENT:
                div_csr &= ~0b10u;
                return div_quotient;
            case DIV_REMAINDER: return static_cast<double>(div_remainder);
            case DIV_CSR: return div_csr;
            case FIFO_ST: case FIFO_WR: case FIFO_RD:
                if (host_.warn) host_.warn(host_.ctx, kSioWarnFifo, offset, 0);
                return 4294967295.0;
            default: break;
        }
        for (int i = 0; i < 2; ++i) {
            const uint32_t base = i == 0 ? INTERP0 : INTERP1;
            if (offset < base || offset >= base + 0x40) continue;
            Interpolator& ip = interp_[i];
            switch (offset - base) {
                case I_ACCUM0: return static_cast<double>(ip.accum0);
                case I_ACCUM1: return static_cast<double>(ip.accum1);
                case I_BASE0: return static_cast<double>(ip.base0);
                case I_BASE1: return static_cast<double>(ip.base1);
                case I_BASE2: return static_cast<double>(ip.base2);
                case I_CTRL_LANE0: return static_cast<double>(ip.ctrl0);
                case I_CTRL_LANE1: return static_cast<double>(ip.ctrl1);
                case I_PEEK_LANE0: return ip.result0;
                case I_PEEK_LANE1: return ip.result1;
                case I_PEEK_FULL: return ip.result2;
                case I_POP_LANE0: { const uint32_t v = ip.result0; ip.writeback(); return v; }
                case I_POP_LANE1: { const uint32_t v = ip.result1; ip.writeback(); return v; }
                case I_POP_FULL: { const uint32_t v = ip.result2; ip.writeback(); return v; }
                case I_ACCUM0_ADD: return ip.smresult0;
                case I_ACCUM1_ADD: return ip.smresult1;
                default: break;  // I_BASE_1AND0 is write-only: it falls through to "invalid", as in the Python block
            }
        }
        if (host_.warn) host_.warn(host_.ctx, kSioWarnReadInvalid, offset, 0);
        return 4294967295.0;
    }

    // What the bus sees: the 32-bit integer `int(value) & 0xFFFFFFFF` of the register (the quotient truncates toward zero).
    uint32_t read32(uint32_t offset) noexcept {
        const double v = read_wide(offset);
        return static_cast<uint32_t>(static_cast<int64_t>(v));
    }

    // Returns false if the write failed (a divide by zero, or telling the pins failed): the failure is pending with the
    // host's caller, and the pin re-evaluation is skipped, as an exception would skip it.
    bool write32(uint32_t offset, int64_t value) noexcept {
        using namespace sio_regs;
        if (offset >= SPINLOCK0 && offset <= SPINLOCK31) {
            spin_lock &= ~(1u << ((offset - SPINLOCK0) / 4));
            return true;
        }
        const uint32_t prev_value = gpio_value, prev_oe = gpio_output_enable;
        const uint32_t masked = static_cast<uint32_t>(value) & GPIO_MASK;
        const uint32_t inverted = static_cast<uint32_t>(~value);

        switch (offset) {
            case GPIO_OUT: gpio_value = masked; break;
            case GPIO_OUT_SET: gpio_value |= masked; break;
            case GPIO_OUT_CLR: gpio_value &= inverted; break;
            case GPIO_OUT_XOR: gpio_value ^= masked; break;
            case GPIO_OE: gpio_output_enable = masked; break;
            case GPIO_OE_SET: gpio_output_enable |= masked; break;
            case GPIO_OE_CLR: gpio_output_enable &= inverted; break;
            case GPIO_OE_XOR: gpio_output_enable ^= masked; break;
            case GPIO_HI_OUT: qspi_gpio_value = masked; break;
            case GPIO_HI_OUT_SET: qspi_gpio_value |= masked; break;
            case GPIO_HI_OUT_CLR: qspi_gpio_value &= inverted; break;
            case GPIO_HI_OUT_XOR: qspi_gpio_value ^= masked; break;
            case GPIO_HI_OE: qspi_gpio_output_enable = masked; break;
            case GPIO_HI_OE_SET: qspi_gpio_output_enable |= masked; break;
            case GPIO_HI_OE_CLR: qspi_gpio_output_enable &= inverted; break;
            case GPIO_HI_OE_XOR: qspi_gpio_output_enable ^= masked; break;
            case DIV_UDIVIDEND: div_dividend = value; if (!update_hardware_divider(false)) return false; break;
            case DIV_SDIVIDEND: div_dividend = value; if (!update_hardware_divider(true)) return false; break;
            case DIV_UDIVISOR: div_divisor = value; if (!update_hardware_divider(false)) return false; break;
            case DIV_SDIVISOR: div_divisor = value; if (!update_hardware_divider(true)) return false; break;
            case DIV_QUOTIENT: div_quotient = static_cast<double>(value); div_csr = 0b11; break;
            case DIV_REMAINDER: div_remainder = value; div_csr = 0b11; break;
            default:
                if (!write_interpolator(offset, value)) {
                    if (host_.warn) host_.warn(host_.ctx, kSioWarnWriteInvalid, offset, value);
                }
                break;
        }

        const uint32_t pins_to_update = (gpio_value ^ prev_value) | (gpio_output_enable ^ prev_oe);
        if (pins_to_update != 0 && host_.update_pins) return host_.update_pins(host_.ctx, pins_to_update);
        return true;
    }

    // ---- window handler entry points: what the bus calls for the SIO address range -----------------------

    static uint32_t window_read32(void* ctx, uint32_t offset) { return static_cast<SioBlock*>(ctx)->read32(offset); }
    static void window_write32(void* ctx, uint32_t offset, int64_t raw_value, uint32_t /*atomic_type*/) {
        static_cast<SioBlock*>(ctx)->write32(offset, raw_value);
    }
    WindowHandler window_handler() noexcept { return WindowHandler{&SioBlock::window_read32, &SioBlock::window_write32, this}; }

private:
    bool write_interpolator(uint32_t offset, int64_t value) noexcept {
        using namespace sio_regs;
        for (int i = 0; i < 2; ++i) {
            const uint32_t base = i == 0 ? INTERP0 : INTERP1;
            if (offset < base || offset >= base + 0x40) continue;
            Interpolator& ip = interp_[i];
            switch (offset - base) {
                case I_ACCUM0: ip.accum0 = value; break;
                case I_ACCUM1: ip.accum1 = value; break;
                case I_BASE0: ip.base0 = value; break;
                case I_BASE1: ip.base1 = value; break;
                case I_BASE2: ip.base2 = value; break;
                case I_CTRL_LANE0: ip.ctrl0 = value; break;
                case I_CTRL_LANE1: ip.ctrl1 = value; break;
                case I_ACCUM0_ADD: ip.accum0 += value; break;
                case I_ACCUM1_ADD: ip.accum1 += value; break;
                case I_BASE_1AND0: ip.set_base01(value); return true;  // updates itself
                default: return false;  // POP/PEEK are read-only: a write is "invalid", as in the Python block
            }
            ip.update();
            return true;
        }
        return false;
    }

    SioHost host_;
    Interpolator interp_[2];
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_SIO_HPP
