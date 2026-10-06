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
//   - the TX DREQ is asked for when UARTEN, TXE and DMACR.TXDMAE are set (the TX FIFO never fills), the RX one when UARTEN, RXE and RXDMAE are set and the FIFO holds a byte (and DMAONERR is
//     not holding it back); every change of CR, DMACR, the FIFO's emptiness or the error interrupts re-announces both (TX then RX);
//   - a disabled UART (UARTEN 0), transmitter (TXE 0) or receiver (RXE 0) sends / receives nothing; an overrun drops the byte and sets UARTRSR.OE and the OE interrupt;
//   - UARTCR.LBE (loopback): a byte written to DR enters the receiver (as `feed_byte`) instead of reaching the device;
//   - UARTIFLS, UARTILPR, UARTDMACR (and CR, LCR_H) keep only their datasheet bits; IFLS and ILPR are stored and not acted on;
//   - IBRD/FBRD writes announce the baud rate every time (the host decides whether anyone listens); IBRD is masked to 16 bits, FBRD to 6, IMSC to 11;
//   - an alias write decodes against a *read* of the register, with that read's side effects (a SET/CLR/XOR write of DR pulls a byte from the FIFO);
//   - `reset()` clears the registers and the FIFO and drops the interrupt line, but never the host's callbacks (wiring, not state) and never `raw_write_value`;
//   - an unimplemented offset warns and reads 0xFFFFFFFF; a read above 0x1000 warns a second time.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_UART_HPP
#define RP2040PY_CORE_UART_HPP

#include <cstdint>

#include "core_host.hpp"

namespace rp2040core {

using UartIrqFn = bool (*)(void* ctx, bool level);               // false: failure parked
using UartDreqFn = bool (*)(void* ctx, bool rx, bool asserted);  // the TX (rx false) or RX DREQ; false: failure parked
using UartByteFn = bool (*)(void* ctx, uint32_t byte);           // a transmitted byte, already masked to 8 bits; false: failure parked
using UartBaudFn = bool (*)(void* ctx);                          // IBRD/FBRD changed: the host announces the new baud rate; false: failure parked
// The messages the Python block logs through `BasePeripheral.warn` (see core_host.hpp); the host formats them.
constexpr uint32_t kUartWarnRead = kRegWarnRead, kUartWarnReadAtomicArea = kRegWarnReadAtomicArea, kUartWarnWrite = kRegWarnWrite;
using UartWarnFn = RegWarnFn;

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
constexpr uint32_t DR = 0x0, RSR = 0x4, FR = 0x18, ILPR = 0x20, IBRD = 0x24, FBRD = 0x28, LCR_H = 0x2C, CR = 0x30, IFLS = 0x34, IMSC = 0x38, IRIS = 0x3C, IMIS = 0x40, ICR = 0x44, DMACR = 0x48;
constexpr uint32_t PERIPHID0 = 0xFE0, PERIPHID1 = 0xFE4, PERIPHID2 = 0xFE8, PERIPHID3 = 0xFEC;
constexpr uint32_t PCELLID0 = 0xFF0, PCELLID1 = 0xFF4, PCELLID2 = 0xFF8, PCELLID3 = 0xFFC;
constexpr uint32_t FR_TXFE = 1u << 7, FR_RXFF = 1u << 6, FR_RXFE = 1u << 4;
[[maybe_unused]] constexpr uint32_t LCR_FEN = 1u << 4;  // the shell's `fifos_enabled`; the block itself never looks at it (the FIFO is 32 deep either way)
constexpr uint32_t CR_RXE = 1u << 9, CR_TXE = 1u << 8, CR_UARTEN = 1u << 0, CR_LBE = 1u << 7;
constexpr uint32_t INT_OE = 1u << 10, INT_TX = 1u << 5, INT_RX = 1u << 4;
constexpr uint32_t INT_ERRORS = 0x780u;  // OE, BE, PE, FE
constexpr uint32_t IMSC_MASK = 0x7FFu;
constexpr uint32_t RSR_OE = 1u << 3;
constexpr uint32_t DMACR_DMAONERR = 1u << 2, DMACR_TXDMAE = 1u << 1, DMACR_RXDMAE = 1u << 0;
// What each register keeps (RP2040 datasheet, 4.2.8): the rest is reserved.
constexpr uint32_t LCR_H_MASK = 0xFFu, CR_MASK = 0xFF87u, IFLS_MASK = 0x3Fu, IFLS_RESET = 0x12u, DMACR_MASK = 0x7u, ILPR_MASK = 0xFFu;
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
    // Stored, not acted on: the RX interrupt comes with every byte (a superset of any trigger level), the TX FIFO never fills, there is no IrDA.
    uint32_t ifls = uart_regs::IFLS_RESET, ilpr = 0, dmacr = 0, rsr = 0;

    void init(const UartHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
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
    bool tx_enabled() const noexcept { return (ctrl & uart_regs::CR_TXE) != 0; }
    bool rx_enabled() const noexcept { return (ctrl & uart_regs::CR_RXE) != 0; }

    // False: a failure is pending.
    bool check_interrupts() noexcept {
        if (!host_.irq(host_.ctx, (interrupt_status & interrupt_mask) != 0)) return false;
        return true;
    }

    // The two DMA requests. TX: the transmit FIFO never fills, so it asks whenever the transmitter is enabled and TXDMAE is set. RX: whenever the receiver is enabled, RXDMAE is set and the
    // FIFO holds a byte - unless DMAONERR is set and an error interrupt is up ("the DMA receive request outputs ... are disabled when the UART error interrupt is asserted").
    bool update_dreq() noexcept {
        using namespace uart_regs;
        const bool ready = enabled();
        const bool tx = ready && tx_enabled() && (dmacr & DMACR_TXDMAE) != 0;
        const bool rx = ready && rx_enabled() && (dmacr & DMACR_RXDMAE) != 0 && !rx_empty() && !((dmacr & DMACR_DMAONERR) != 0 && (interrupt_status & INT_ERRORS) != 0);
        if (!host_.dreq(host_.ctx, false, tx)) return false;
        return host_.dreq(host_.ctx, true, rx);
    }

    // A byte arrives from the wire (the CLI's console, a test). A disabled receiver (or UART) takes nothing; on a full FIFO the byte is dropped and OE and its interrupt are set.
    bool feed_byte(uint32_t value) noexcept {
        using namespace uart_regs;
        if (!(enabled() && rx_enabled())) return true;
        if (rx_full()) {
            rsr |= RSR_OE;
            interrupt_status |= INT_OE;
        } else {
            rx_push(value);
            interrupt_status |= INT_RX;  // not held back until a trigger level (UARTIFLS): see the note on `ifls`
        }
        if (!check_interrupts()) return false;
        return update_dreq();
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
        ifls = uart_regs::IFLS_RESET;
        ilpr = dmacr = rsr = 0;
        if (!host_.irq(host_.ctx, false)) return false;
        return update_dreq();
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
                (void)update_dreq();
                return value;
            }
            case RSR: return rsr;
            case ILPR: return ilpr;
            case FR: return flags();
            case IBRD: return int_divisor;
            case FBRD: return frac_divisor;
            case LCR_H: return line_ctrl;
            case CR: return ctrl;
            case IFLS: return ifls;
            case DMACR: return dmacr;
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
                // "TXE: Transmit enable": a disabled transmitter (or UART) sends nothing - the byte goes nowhere
                if (!(enabled() && tx_enabled())) return true;
                if (ctrl & CR_LBE) {
                    // "LBE: Loop back enable": the byte reaches the receiver, not the device
                    if (!feed_byte(static_cast<uint32_t>(value) & 0xFFu)) return false;
                } else if (!host_.on_byte(host_.ctx, static_cast<uint32_t>(value) & 0xFFu)) {
                    return false;
                }
                interrupt_status |= INT_TX;
                return check_interrupts();
            case RSR: rsr = 0; return true;  // the write is UARTECR: it clears the error flags
            case ILPR: ilpr = static_cast<uint32_t>(value) & ILPR_MASK; return true;
            case IBRD:
                int_divisor = static_cast<uint32_t>(value) & 0xFFFFu;
                return host_.baud_changed(host_.ctx);
            case FBRD:
                frac_divisor = static_cast<uint32_t>(value) & 0x3Fu;
                return host_.baud_changed(host_.ctx);
            case LCR_H: line_ctrl = static_cast<uint32_t>(value) & LCR_H_MASK; return true;
            case CR:
                ctrl = static_cast<uint32_t>(value) & CR_MASK;
                return update_dreq();
            case IFLS: ifls = static_cast<uint32_t>(value) & IFLS_MASK; return true;
            case DMACR:
                dmacr = static_cast<uint32_t>(value) & DMACR_MASK;
                return update_dreq();
            case IMSC:
                interrupt_mask = static_cast<uint32_t>(value) & IMSC_MASK;
                return check_interrupts();
            case ICR:
                interrupt_status &= ~static_cast<uint32_t>(raw_write_value_);
                if (!check_interrupts()) return false;
                return update_dreq();  // DMAONERR: clearing the error interrupt lets the receive request through again
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
            value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        }
        return write(offset, value);
    }

    // ---- window handler entry points: what the bus's C++ window registry calls ---------------------------------

    WindowHandler window_handler() noexcept { return BlockWindow<UartBlock>::handler(this); }

private:
    UartHost host_;
    uint32_t rx_[kRxCapacity] = {};
    uint32_t rx_start_ = 0, rx_used_ = 0;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_UART_HPP
