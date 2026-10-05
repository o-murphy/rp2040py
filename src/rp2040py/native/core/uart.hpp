// The RP2040 UART (PL011) in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of peripherals/_uart.py, which stays as the pure-Python
// reference and the oracle (tests/test_uart_diff.py).
//
// What it owns: the register file (control, line control, the two baud divisors, the interrupt mask and status), the 32-entry RX FIFO, and the whole of the block's
// behaviour towards the guest. What it does not own, reached through a `UartHost` of plain function pointers: the interrupt line, the TX DREQ (the chip's DMA), the
// transmitted-byte callback, the baud-rate announcement and the logger. The baud rate itself is *not* computed here: it depends on the chip's `clk_peri`, a zero divider
// is a `ZeroDivisionError` that has to reach the writer, and the callback is Python's - so the host does all three.
//
// Failures. A host function may fail (a Python callback may raise); the failure is parked by the callee and `*host.failed` is raised. The block checks it after every
// host call and returns `false` at once, leaving exactly the state an exception would have left in the reference.
//
// Quirks of the reference that are kept (each one is pinned by tests/test_uart_diff.py or the C++ checks):
//   - transmit is instant: a DR write calls the host's `on_byte` at once and raises TXINTR (an edge - a DR write sets it, ICR clears it for good, a mere empty FIFO
//     does not), and FR always reports TXFE and never TXFF or BUSY;
//   - the RX FIFO is 32 deep whatever LCR_H.FEN says, a push on a full FIFO drops the byte, a pull from an empty one reads 0;
//   - `UARTICR` clears the bits of the *raw* value the bus passed, not of the alias-decoded value, and a direct (non-atomic) write keeps the raw value the last atomic
//     write left;
//   - the UARTCR write drives the TX DREQ from UARTEN alone (never the RX one, never TXE/RXE);
//   - IBRD/FBRD writes announce the baud rate every time (the host decides whether anyone listens); IBRD is masked to 16 bits, FBRD to 6, IMSC to 11;
//   - an alias write decodes against a *read* of the register, with that read's side effects (a SET/CLR/XOR write of DR pulls a byte from the FIFO);
//   - `reset()` clears the registers and the FIFO and drops the interrupt line, but never the host's callbacks (wiring, not state) and never `raw_write_value`;
//   - an unimplemented offset warns and reads 0xFFFFFFFF; a read above 0x1000 warns a second time.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_UART_HPP
#define RP2040PY_CORE_UART_HPP

#include <cstdint>

#include "window_map.hpp"

namespace rp2040core {

using UartIrqFn = bool (*)(void* ctx, bool level);               // false: failure parked
using UartDreqFn = bool (*)(void* ctx, bool asserted);           // the TX DREQ; false: failure parked
using UartByteFn = bool (*)(void* ctx, uint32_t byte);           // a transmitted byte, already masked to 8 bits; false: failure parked
using UartBaudFn = bool (*)(void* ctx);                          // IBRD/FBRD changed: the host announces the new baud rate; false: failure parked
// The messages the Python block logs through `BasePeripheral.warn`; the host formats them (it owns the logger).
enum UartWarn : uint32_t {
    kUartWarnRead = 0,            // "Unimplemented peripheral read from 0x{offset:x}"
    kUartWarnReadAtomicArea = 1,  // "Unimplemented read from peripheral in the atomic operation region" (offset > 0x1000)
    kUartWarnWrite = 2,           // "Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}"
};
using UartWarnFn = void (*)(void* ctx, uint32_t kind, uint32_t offset, int64_t value);

struct UartHost {
    UartIrqFn irq = nullptr;
    UartDreqFn dreq = nullptr;
    UartByteFn on_byte = nullptr;
    UartBaudFn baud_changed = nullptr;
    UartWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace uart_regs {
constexpr uint32_t DR = 0x0, FR = 0x18, IBRD = 0x24, FBRD = 0x28, LCR_H = 0x2C, CR = 0x30, IMSC = 0x38, IRIS = 0x3C, IMIS = 0x40, ICR = 0x44;
constexpr uint32_t PERIPHID0 = 0xFE0, PERIPHID1 = 0xFE4, PERIPHID2 = 0xFE8, PERIPHID3 = 0xFEC;
constexpr uint32_t PCELLID0 = 0xFF0, PCELLID1 = 0xFF4, PCELLID2 = 0xFF8, PCELLID3 = 0xFFC;
constexpr uint32_t FR_TXFE = 1u << 7, FR_RXFF = 1u << 6, FR_RXFE = 1u << 4;
[[maybe_unused]] constexpr uint32_t LCR_FEN = 1u << 4;  // the shell's `fifos_enabled`; the block itself never looks at it (the FIFO is 32 deep either way)
constexpr uint32_t CR_RXE = 1u << 9, CR_TXE = 1u << 8, CR_UARTEN = 1u << 0;
constexpr uint32_t INT_TX = 1u << 5, INT_RX = 1u << 4;
constexpr uint32_t IMSC_MASK = 0x7FFu;
}  // namespace uart_regs

class UartBlock {
public:
    static constexpr uint32_t kRxCapacity = 32;

    UartBlock() = default;
    UartBlock(const UartBlock&) = delete;  // the window handler holds a pointer to this object
    UartBlock& operator=(const UartBlock&) = delete;

    // The registers, public for the shell (the reference's private attributes `_ctrl_register`, ... are read and, by tests, written directly).
    uint32_t ctrl = uart_regs::CR_RXE | uart_regs::CR_TXE;
    uint32_t line_ctrl = 0;
    uint32_t int_divisor = 0, frac_divisor = 0;
    uint32_t interrupt_mask = 0, interrupt_status = 0;

    void init(const UartHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_.failed != nullptr && *host_.failed != 0; }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // ---- the RX FIFO (the reference's `utils.fifo.FIFO(32)`) ---------------------------------------------------

    uint32_t rx_count() const noexcept { return rx_used_; }
    bool rx_empty() const noexcept { return rx_used_ == 0; }
    bool rx_full() const noexcept { return rx_used_ == kRxCapacity; }
    uint32_t rx_at(uint32_t index) const noexcept { return rx_[(rx_start_ + index) % kRxCapacity]; }
    void rx_push(uint32_t value) noexcept {
        if (rx_used_ < kRxCapacity) {
            rx_[(rx_start_ + rx_used_) % kRxCapacity] = value;
            ++rx_used_;
        }
    }
    uint32_t rx_pull() noexcept {
        if (rx_used_ == 0) return 0;
        const uint32_t value = rx_[rx_start_];
        rx_start_ = (rx_start_ + 1) % kRxCapacity;
        --rx_used_;
        return value;
    }
    uint32_t rx_peek() const noexcept { return rx_used_ ? rx_[rx_start_] : 0; }
    void rx_reset() noexcept { rx_used_ = 0; }

    // ---- the block --------------------------------------------------------------------------------------------

    uint32_t flags() const noexcept {
        using namespace uart_regs;
        return (rx_full() ? FR_RXFF : 0u) | (rx_empty() ? FR_RXFE : 0u) | FR_TXFE;
    }
    bool enabled() const noexcept { return (ctrl & uart_regs::CR_UARTEN) != 0; }

    // False: a failure is pending.
    bool check_interrupts() noexcept {
        if (!host_.irq(host_.ctx, (interrupt_status & interrupt_mask) != 0)) return false;
        return true;
    }

    // A byte arrives from the wire (the CLI's console, a test); a push on a full FIFO drops it.
    bool feed_byte(uint32_t value) noexcept {
        rx_push(value);
        interrupt_status |= uart_regs::INT_RX;
        return check_interrupts();
    }

    // False: a failure is pending (the register state is already what the reference's exception would have left).
    bool reset() noexcept {
        ctrl = uart_regs::CR_RXE | uart_regs::CR_TXE;
        line_ctrl = 0;
        rx_reset();
        interrupt_mask = 0;
        interrupt_status = 0;
        int_divisor = 0;
        frac_divisor = 0;
        return host_.irq(host_.ctx, false);
    }

    // Reads a register. A failure of a host call leaves the flag raised and the value already computed (the reference had raised after the same side effects).
    uint32_t read(uint32_t offset) noexcept {
        using namespace uart_regs;
        switch (offset) {
            case DR: {
                const uint32_t value = rx_pull();
                if (!rx_empty()) {
                    interrupt_status |= INT_RX;
                } else {
                    interrupt_status &= ~INT_RX;
                }
                (void)check_interrupts();
                return value;
            }
            case FR: return flags();
            case IBRD: return int_divisor;
            case FBRD: return frac_divisor;
            case LCR_H: return line_ctrl;
            case CR: return ctrl;
            case IMSC: return interrupt_mask;
            case IRIS: return interrupt_status;
            case IMIS: return interrupt_status & interrupt_mask;
            case PERIPHID0: return 0x11;
            case PERIPHID1: return 0x10;
            case PERIPHID2: return 0x34;
            case PERIPHID3: return 0x00;
            case PCELLID0: return 0x0D;
            case PCELLID1: return 0xF0;
            case PCELLID2: return 0x05;
            case PCELLID3: return 0xB1;
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kUartWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kUartWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode; the raw value is `raw_write_value()`. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace uart_regs;
        switch (offset) {
            case DR:
                if (!host_.on_byte(host_.ctx, static_cast<uint32_t>(value) & 0xFFu)) return false;
                interrupt_status |= INT_TX;
                return check_interrupts();
            case IBRD:
                int_divisor = static_cast<uint32_t>(value) & 0xFFFFu;
                return host_.baud_changed(host_.ctx);
            case FBRD:
                frac_divisor = static_cast<uint32_t>(value) & 0x3Fu;
                return host_.baud_changed(host_.ctx);
            case LCR_H: line_ctrl = static_cast<uint32_t>(value); return true;
            case CR:
                ctrl = static_cast<uint32_t>(value);
                return host_.dreq(host_.ctx, enabled());
            case IMSC:
                interrupt_mask = static_cast<uint32_t>(value) & IMSC_MASK;
                return check_interrupts();
            case ICR:
                interrupt_status &= ~static_cast<uint32_t>(raw_write_value_);
                return check_interrupts();
            default: break;
        }
        if (host_.warn) host_.warn(host_.ctx, kUartWarnWrite, offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step - remember the raw value, decode the alias against a *read* of the register (with that read's side
    // effects), then write.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) {
            const int64_t current = static_cast<int64_t>(read(offset));
            switch (atomic_type) {
                case kAtomicXor: value = current ^ raw; break;
                case kAtomicSet: value = current | raw; break;
                case kAtomicClear: value = current & ~raw; break;
                default: break;
            }
        }
        return write(offset, value);
    }

    // ---- window handler entry points: what the bus's C++ window registry calls ---------------------------------

    static uint32_t window_read32(void* ctx, uint32_t offset) { return static_cast<UartBlock*>(ctx)->read(offset); }
    static void window_write32(void* ctx, uint32_t offset, int64_t raw_value, uint32_t atomic_type) {
        (void)static_cast<UartBlock*>(ctx)->write_atomic(offset, raw_value, atomic_type);
    }
    WindowHandler window_handler() noexcept { return WindowHandler{&UartBlock::window_read32, &UartBlock::window_write32, this}; }

private:
    UartHost host_;
    uint32_t rx_[kRxCapacity] = {};
    uint32_t rx_start_ = 0, rx_used_ = 0;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_UART_HPP
