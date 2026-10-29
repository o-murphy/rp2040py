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

#include "core_host.hpp"
#include "interpolator.hpp"
#include "window_map.hpp"

namespace rp2040core {

constexpr uint32_t kSioIrqProc0 = 15;  // SIO_IRQ_PROC0; SIO_IRQ_PROC1 is the next

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
    bool (*update_pins)(void* ctx, uint32_t pin_mask, bool qspi) = nullptr;  // these pins' (the QSPI bank's if `qspi`) SIO-driven state changed; false: it failed
    void (*add_cycles)(void* ctx, uint32_t cycles) = nullptr;           // charge the CPU
    // Core 1 is launched (the last word of the boot ROM's handshake arrived over the FIFO, `multicore` mode): reset it to `vtor`, `sp`, `pc` and let it run. False: it failed.
    bool (*launch_core1)(void* ctx, uint32_t vtor, uint32_t sp, uint32_t pc) = nullptr;
    // An event (what a `SEV` does) for core `core`: the boot ROM that this block stands in for executes one after each word it echoes, and core 0 sleeps in a WFE until it comes.
    void (*send_event)(void* ctx, uint32_t core) = nullptr;
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
constexpr uint32_t GPIO_MASK = 0x3FFFFFFF;  // GPIO0..29 (datasheet: bits 31:30 reserved)
constexpr uint32_t QSPI_MASK = 0x3F;       // GPIO_HI_OUT / GPIO_HI_OE: the six QSPI pins, 5:0
constexpr int64_t INTERP_ADD_MASK = 0xFFFFFF;  // INTERPx_ACCUMy_ADD is 23:0 (datasheet, and SIO_INTERP0_ACCUM0_ADD_BITS 0x00ffffff in pico-sdk's sio.h)
}  // namespace sio_regs

// What SIO keeps PER CORE (datasheet 2.3.1: each processor has its own divider, its own two interpolators and its own end of each FIFO); the spinlocks and the GPIO registers are shared.
// Core 1's copy is idle until core 1 runs (record 0104, D3). The FIFOs will join these in P1.
struct SioCoreState {
    int64_t div_dividend = 0, div_divisor = 0, div_remainder = 0;  // reset: the datasheet's 0
    double div_quotient = 0;
    uint32_t div_csr = 1;  // DIV_CSR.READY resets to 1
    Interpolator interp[2] = {Interpolator(0), Interpolator(1)};

    void reset() noexcept {
        div_dividend = 0;
        div_divisor = 0;
        div_quotient = 0;
        div_remainder = 0;
        div_csr = 1;
        interp[0].reset();
        interp[1].reset();
    }
};

class SioBlock {
public:
    SioBlock() noexcept = default;
    SioBlock(const SioBlock&) = delete;
    SioBlock& operator=(const SioBlock&) = delete;

    void init(const ChipPorts* ports, uint32_t block, const SioHost& host) noexcept {
        port_.init(ports, block, kSioIrqProc0);  // the FIFO lines are SIO_IRQ_PROC0 (15) and SIO_IRQ_PROC1 (16)
        host_ = host;
    }

    // The inter-core FIFOs (datasheet 2.3.1.4) and the high-level answer of the boot ROM's launch handshake for core 1 are on (record 0104; the Python reference has them too). `set_multicore(false)`
    // gives back the old stub - the registers warn and read as 0xFFFFFFFF (record 0053) - for a test of that.
    void set_multicore(bool on) noexcept { multicore_ = on; }
    bool multicore() const noexcept { return multicore_; }
    // Core 1 has come out of reset (PSM.FRCE_OFF PROC1 cleared) and the boot ROM it runs has drained its mailbox: it pushes a 0 to core 0 (and a SEV after it), which `multicore_reset_core1()`
    // of the pico-sdk waits for.
    bool core1_boot_done() noexcept {
        if (!multicore_) return true;
        fifo_[1].push(0);
        if (host_.send_event != nullptr) host_.send_event(host_.ctx, 0);
        return update_fifo_irqs();
    }
    // Core 1 goes back to its boot state (PSM, a chip reset): the FIFOs are emptied and the handshake starts over. False: a line could not be updated.
    bool reset_core1() noexcept {
        fifo_[0].clear();
        fifo_[1].clear();
        roe_[0] = roe_[1] = wof_[0] = wof_[1] = false;
        launch_seq_ = 0;
        core1_launched_ = false;
        return update_fifo_irqs();
    }

    // The registers the rest of the chip (and tests) read and write directly, as plain attributes of the Python block.
    uint32_t gpio_value = 0, gpio_output_enable = 0, qspi_gpio_value = 0, qspi_gpio_output_enable = 0;
    uint32_t spin_lock = 0;

    // Core `index`'s divider and interpolators. `interp()` is core 0's, which is what the shells and the tests look at.
    SioCoreState& core(int index) noexcept { return core_[index & 1]; }
    Interpolator& interp(int index) noexcept { return core_[0].interp[index]; }

    // Which core is accessing: the bus's `initiator` byte, read on every divider, interpolator and CPUID access (null: always core 0, a block made without a bus).
    void bind_initiator(const uint8_t* initiator) noexcept { initiator_ = initiator; }

    void reset() noexcept {
        gpio_value = gpio_output_enable = qspi_gpio_value = qspi_gpio_output_enable = 0;
        spin_lock = 0;
        core_[0].reset();
        core_[1].reset();
        (void)reset_core1();
    }

    // The hardware divider: recomputes quotient/remainder from the current operands and charges 8 CPU cycles.
    // Returns false when the divide failed (see kSioFailDivideByZero).
    bool update_hardware_divider(bool signed_division) noexcept {
        SioCoreState& c = active();
        using interp_detail::s32;
        using interp_detail::u32;
        if (c.div_divisor != 0 && (signed_division ? s32(c.div_divisor) : u32(c.div_divisor)) == 0) {
            port_.warn(kSioFailDivideByZero, 0, c.div_divisor);
            return false;
        }
        if (c.div_divisor == 0) {
            // The sign is the dividend's as a 32-bit word (signed for SDIVIDEND, unsigned for UDIVIDEND), as a bus write delivers it - not the sign of the raw value the host passed in.
            const int64_t dividend = signed_division ? s32(c.div_dividend) : u32(c.div_dividend);
            c.div_quotient = dividend > 0 ? -1.0 : 1.0;
            c.div_remainder = c.div_dividend;
        } else if (signed_division) {
            const int64_t dividend = s32(c.div_dividend), divisor = s32(c.div_divisor);
            c.div_quotient = static_cast<double>(dividend) / static_cast<double>(divisor);
            // JS `%`: truncating remainder, sign of the dividend - computed as the Python does, a - b*int(a/b).
            c.div_remainder = dividend - divisor * static_cast<int64_t>(static_cast<double>(dividend) / static_cast<double>(divisor));
        } else {
            const int64_t dividend = u32(c.div_dividend), divisor = u32(c.div_divisor);
            c.div_quotient = static_cast<double>(dividend) / static_cast<double>(divisor);
            c.div_remainder = dividend % divisor;
        }
        c.div_csr = 0b11;
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
            case CPUID: return initiator();
            case SPINLOCK_ST: return spin_lock;
            case DIV_UDIVIDEND: case DIV_SDIVIDEND: return static_cast<double>(active().div_dividend);
            case DIV_UDIVISOR: case DIV_SDIVISOR: return static_cast<double>(active().div_divisor);
            case DIV_QUOTIENT:
                active().div_csr &= ~0b10u;
                return active().div_quotient;
            case DIV_REMAINDER: return static_cast<double>(active().div_remainder);
            case DIV_CSR: return active().div_csr;
            case FIFO_ST: case FIFO_WR: case FIFO_RD:
                if (multicore_) return fifo_read(offset);
                port_.warn(kSioWarnFifo, offset, 0);
                return 4294967295.0;
            default: break;
        }
        for (int i = 0; i < 2; ++i) {
            const uint32_t base = i == 0 ? INTERP0 : INTERP1;
            if (offset < base || offset >= base + 0x40) continue;
            Interpolator& ip = active().interp[i];
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
        port_.warn(kSioWarnReadInvalid, offset, 0);
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
        if (multicore_ && (offset == FIFO_ST || offset == FIFO_WR)) return fifo_write(offset, static_cast<uint32_t>(value));
        const uint32_t prev_value = gpio_value, prev_oe = gpio_output_enable;
        const uint32_t prev_qspi_value = qspi_gpio_value, prev_qspi_oe = qspi_gpio_output_enable;
        const uint32_t masked = static_cast<uint32_t>(value) & GPIO_MASK;
        const uint32_t qspi_masked = static_cast<uint32_t>(value) & QSPI_MASK;
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
            case GPIO_HI_OUT: qspi_gpio_value = qspi_masked; break;
            case GPIO_HI_OUT_SET: qspi_gpio_value |= qspi_masked; break;
            case GPIO_HI_OUT_CLR: qspi_gpio_value &= inverted; break;
            case GPIO_HI_OUT_XOR: qspi_gpio_value ^= qspi_masked; break;
            case GPIO_HI_OE: qspi_gpio_output_enable = qspi_masked; break;
            case GPIO_HI_OE_SET: qspi_gpio_output_enable |= qspi_masked; break;
            case GPIO_HI_OE_CLR: qspi_gpio_output_enable &= inverted; break;
            case GPIO_HI_OE_XOR: qspi_gpio_output_enable ^= qspi_masked; break;
            case DIV_UDIVIDEND: active().div_dividend = value; if (!update_hardware_divider(false)) return false; break;
            case DIV_SDIVIDEND: active().div_dividend = value; if (!update_hardware_divider(true)) return false; break;
            case DIV_UDIVISOR: active().div_divisor = value; if (!update_hardware_divider(false)) return false; break;
            case DIV_SDIVISOR: active().div_divisor = value; if (!update_hardware_divider(true)) return false; break;
            case DIV_QUOTIENT: active().div_quotient = static_cast<double>(value); active().div_csr = 0b11; break;
            case DIV_REMAINDER: active().div_remainder = value; active().div_csr = 0b11; break;
            default:
                if (!write_interpolator(offset, value)) {
                    port_.warn(kSioWarnWriteInvalid, offset, value);
                }
                break;
        }

        const uint32_t pins_to_update = (gpio_value ^ prev_value) | (gpio_output_enable ^ prev_oe);
        if (pins_to_update != 0 && host_.update_pins && !host_.update_pins(host_.ctx, pins_to_update, false)) return false;
        // the QSPI pins too: GPIO_HI_OUT / GPIO_HI_OE drive them (datasheet table 282), and whatever listens to them must hear it
        const uint32_t qspi_to_update = (qspi_gpio_value ^ prev_qspi_value) | (qspi_gpio_output_enable ^ prev_qspi_oe);
        if (qspi_to_update != 0 && host_.update_pins) return host_.update_pins(host_.ctx, qspi_to_update, true);
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
            Interpolator& ip = active().interp[i];
            switch (offset - base) {
                case I_ACCUM0: ip.accum0 = value; break;
                case I_ACCUM1: ip.accum1 = value; break;
                case I_BASE0: ip.base0 = value; break;
                case I_BASE1: ip.base1 = value; break;
                case I_BASE2: ip.base2 = value; break;
                case I_CTRL_LANE0: ip.ctrl0 = value; break;
                case I_CTRL_LANE1: ip.ctrl1 = value; break;
                case I_ACCUM0_ADD: ip.accum0 += value & INTERP_ADD_MASK; break;
                case I_ACCUM1_ADD: ip.accum1 += value & INTERP_ADD_MASK; break;
                case I_BASE_1AND0: ip.set_base01(value); return true;  // updates itself
                default: return false;  // POP/PEEK are read-only: a write is "invalid", as in the Python block
            }
            ip.update();
            return true;
        }
        return false;
    }

    // ---- the FIFOs: `fifo_[0]` carries core 0 -> core 1, `fifo_[1]` core 1 -> core 0; a core writes its own and reads the other's -------------------------------------------------------------
    struct Fifo {
        uint32_t data[8];
        uint32_t head = 0, count = 0;
        bool push(uint32_t word) noexcept {
            if (count == 8) return false;
            data[(head + count++) & 7] = word;
            return true;
        }
        bool pop(uint32_t* word) noexcept {
            if (count == 0) return false;
            *word = data[head];
            head = (head + 1) & 7;
            --count;
            return true;
        }
        void clear() noexcept { head = count = 0; }
    };
    uint32_t fifo_status(uint32_t core) const noexcept {
        return (roe_[core] ? 8u : 0u) | (wof_[core] ? 4u : 0u) | (fifo_[core].count < 8 ? 2u : 0u) | (fifo_[1 - core].count != 0 ? 1u : 0u);
    }
    // The IRQ of each core is the OR of VLD, ROE and WOF of its FIFO_ST; a line is told only when it changes.
    bool update_fifo_irqs() noexcept {
        for (uint32_t core = 0; core < 2; ++core) {
            const bool level = (fifo_status(core) & 0b1101u) != 0;
            if (level != fifo_irq_[core]) {
                fifo_irq_[core] = level;
                if (!port_.irq(level, core)) return false;
            }
        }
        return true;
    }
    double fifo_read(uint32_t offset) noexcept {
        using namespace sio_regs;
        const uint32_t core = initiator();
        if (offset == FIFO_ST) return fifo_status(core);
        if (offset == FIFO_WR) {  // write-only
            port_.warn(kSioWarnReadInvalid, offset, 0);
            return 4294967295.0;
        }
        uint32_t word = 0;
        if (!fifo_[1 - core].pop(&word)) roe_[core] = true;  // a read of an empty FIFO is ignored, and remembered
        (void)update_fifo_irqs();
        return word;
    }
    bool fifo_write(uint32_t offset, uint32_t value) noexcept {
        using namespace sio_regs;
        const uint32_t core = initiator();
        if (offset == FIFO_ST) {  // writing anything clears the two sticky flags
            roe_[core] = wof_[core] = false;
            return update_fifo_irqs();
        }
        if (core == 0 && !core1_launched_ && host_.launch_core1 != nullptr) return boot_rom_answer(value);
        if (!fifo_[core].push(value)) wof_[core] = true;  // a write to a full FIFO is ignored, and remembered
        return update_fifo_irqs();
    }
    // Core 1 is still in the boot ROM (asleep until the mailbox wakes it): the ROM echoes every word of core 0's handshake and starts over on a wrong one; the last word starts core 1 at the
    // vector table, stack and entry it was told (datasheet 2.8.2: 0, 0, 1, vector table, sp, entry). The bits of the real ROM are replaced by this answer, never visible but in the FIFO.
    bool boot_rom_answer(uint32_t word) noexcept {
        static const uint32_t expected[3] = {0, 0, 1};
        if (launch_seq_ < 3) {
            if (word == expected[launch_seq_]) {
                ++launch_seq_;
            } else {
                launch_seq_ = word == 0 ? 1 : 0;  // a wrong word starts over - and a 0 is the first word of the sequence core 0 begins again with
            }
        } else if (word == 0) {
            launch_seq_ = 1;  // core 0 restarts (it drains before a 0)
        } else {
            launch_words_[launch_seq_ - 3] = word;  // the vector table, the stack pointer, the entry: any value
            ++launch_seq_;
        }
        fifo_[1].push(word);  // the echo (core 0 drains before a 0 and waits for each echo: there is room)
        if (host_.send_event != nullptr) host_.send_event(host_.ctx, 0);  // (the ROM's SEV after it)
        if (!update_fifo_irqs()) return false;
        if (launch_seq_ < 6) return true;
        launch_seq_ = 0;
        core1_launched_ = true;
        return host_.launch_core1(host_.ctx, launch_words_[0], launch_words_[1], launch_words_[2]);
    }

    uint32_t initiator() const noexcept { return initiator_ != nullptr ? (*initiator_ & 1u) : 0u; }
    SioCoreState& active() noexcept { return core_[initiator()]; }

    BlockPort port_;
    SioHost host_;
    SioCoreState core_[2];
    const uint8_t* initiator_ = nullptr;
    bool multicore_ = true;
    Fifo fifo_[2];
    bool roe_[2] = {false, false}, wof_[2] = {false, false}, fifo_irq_[2] = {false, false};
    uint32_t launch_seq_ = 0, launch_words_[3] = {0, 0, 0};
    bool core1_launched_ = false;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_SIO_HPP
