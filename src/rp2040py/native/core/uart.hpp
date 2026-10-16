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
//   - the transmitter has a time: a DR write enters the TX FIFO (32 deep with LCR_H.FEN, a one-entry holding register without; a write to a full FIFO is lost), the shift
//     register takes one character at a time and holds it for `frame bits x bit time` on the chip's clock (an alarm of this block), and only when it has gone out is it
//     delivered (`on_byte`, or the receiver under loopback). FR reports TXFE (FIFO empty), TXFF (full) and BUSY (shifting or FIFO not empty). Each character moving into the shift
//     register is the FIFO falling by one: when that takes it from above the UARTIFLS.TXIFLSEL level to at or below it, TXINTR is raised (a transition, not a level: the characters that
//     follow do not raise it again; cleared by ICR, or by writes taking the FIFO back above the level). With no
//     baud rate configured (a zero divider) the bit time is 0 and a character leaves at once, as it always did;
//   - the RX FIFO is 32 deep whatever LCR_H.FEN says, a push on a full FIFO drops the byte, a pull from an empty one reads 0;
//   - `UARTICR` clears the bits of the *raw* value the bus passed, not of the alias-decoded value, and a direct (non-atomic) write keeps the raw value the last atomic
//     write left;
//   - the TX DREQ is asked for when UARTEN, TXE and DMACR.TXDMAE are set and the TX FIFO has an empty place (UARTTXDMASREQ), the RX one when UARTEN, RXE and RXDMAE are set and the FIFO holds a byte (and DMAONERR is
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

#include "clock.hpp"
#include "core_host.hpp"

namespace rp2040core {

using UartByteFn = bool (*)(void* ctx, uint32_t byte);           // a transmitted byte, already masked to 8 bits; false: failure parked
using UartBaudFn = bool (*)(void* ctx);                          // IBRD/FBRD changed: the host announces the new baud rate; false: failure parked
using UartBitFn = bool (*)(void* ctx, double* nanos);            // the time of one bit on the wire (0 while no baud rate is configured); false: failure parked
// The messages the Python block logs through `BasePeripheral.warn` (see core_host.hpp); the host formats them.

// The edges of the block that are the host's own (the device on the wire, the baud rate, the bit time); its line, requests, warnings and failure flag are the chip's `ChipPorts`.
struct UartHost {
    UartByteFn on_byte = nullptr;
    UartBaudFn baud_changed = nullptr;
    UartBitFn bit_ns = nullptr;
    void* ctx = nullptr;
};

namespace uart_regs {
constexpr uint32_t DR = 0x0, RSR = 0x4, FR = 0x18, ILPR = 0x20, IBRD = 0x24, FBRD = 0x28, LCR_H = 0x2C, CR = 0x30, IFLS = 0x34, IMSC = 0x38, IRIS = 0x3C, IMIS = 0x40, ICR = 0x44, DMACR = 0x48;
constexpr uint32_t PERIPHID0 = 0xFE0, PERIPHID1 = 0xFE4, PERIPHID2 = 0xFE8, PERIPHID3 = 0xFEC;
constexpr uint32_t PCELLID0 = 0xFF0, PCELLID1 = 0xFF4, PCELLID2 = 0xFF8, PCELLID3 = 0xFFC;
constexpr uint32_t FR_TXFE = 1u << 7, FR_RXFF = 1u << 6, FR_TXFF = 1u << 5, FR_RXFE = 1u << 4, FR_BUSY = 1u << 3;
constexpr uint32_t LCR_FEN = 1u << 4, LCR_PEN = 1u << 1, LCR_STP2 = 1u << 3;  // FEN: the TX FIFO's depth (the RX FIFO is 32 deep either way)
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
    static constexpr uint32_t kTxDepth = 32;

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

    // `irq_line` is the instance's NVIC line, `dreq_base` its TX request (RX follows).
    void init(Clock* clock, const ChipPorts* ports, uint32_t block, uint32_t irq_line, uint32_t dreq_base, const UartHost& host) noexcept {
        clock_ = clock;
        port_.init(ports, block, irq_line, dreq_base);
        host_ = host;
        tx_alarm.fire = &UartBlock::on_tx_done;
        tx_alarm.ctx = this;
    }

    // Unlinks the TX alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept { cancel_alarm(&tx_alarm); }

    Alarm tx_alarm;

    bool failed() const noexcept { return port_.failed(); }
    bool raise_dreq(bool rx, bool asserted) noexcept { return port_.dreq(rx ? 1u : 0u, asserted); }
    bool raise_irq(bool level) noexcept { return port_.irq(level); }
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

    // ---- the TX FIFO (the reference's `FIFO(32)`) and the shift register ---------------------------------------

    uint32_t tx_count() const noexcept { return tx_used_; }
    bool tx_empty() const noexcept { return tx_used_ == 0; }
    uint32_t tx_capacity() const noexcept { return (line_ctrl & uart_regs::LCR_FEN) ? kTxDepth : 1u; }  // "disabled (have a depth of one location)"
    bool tx_full() const noexcept { return tx_used_ >= tx_capacity(); }
    uint32_t tx_at(uint32_t index) const noexcept { return tx_[(tx_start_ + index) % kTxDepth]; }
    bool tx_shifting() const noexcept { return tx_shifting_; }
    // The level at or below which TXINTR asserts: the TXIFLSEL fraction of 32, or empty with the FIFOs disabled; the reserved b101-b111 act as the reset value (1/2).
    uint32_t tx_trigger() const noexcept {
        if (!(line_ctrl & uart_regs::LCR_FEN)) return 0;
        switch (ifls & 0x7u) {
            case 0: return 4;
            case 1: return 8;
            case 3: return 24;
            case 4: return 28;
            default: return 16;
        }
    }
    void tx_push(uint32_t value) noexcept {
        if (tx_used_ < kTxDepth) {
            tx_[(tx_start_ + tx_used_) % kTxDepth] = value;
            ++tx_used_;
        }
    }
    uint32_t tx_pull() noexcept {
        if (tx_used_ == 0) return 0;
        const uint32_t value = tx_[tx_start_];
        tx_start_ = (tx_start_ + 1) % kTxDepth;
        --tx_used_;
        return value;
    }
    void tx_reset() noexcept { tx_used_ = 0; }

    // ---- the block --------------------------------------------------------------------------------------------

    uint32_t flags() const noexcept {
        using namespace uart_regs;
        // BUSY: set as soon as the TX FIFO is non-empty, until the last character has gone out of the shift register
        return (rx_full() ? FR_RXFF : 0u) | (rx_empty() ? FR_RXFE : 0u) | (tx_empty() ? FR_TXFE : 0u) | (tx_full() ? FR_TXFF : 0u) | ((tx_shifting_ || !tx_empty()) ? FR_BUSY : 0u);
    }
    bool enabled() const noexcept { return (ctrl & uart_regs::CR_UARTEN) != 0; }
    bool tx_enabled() const noexcept { return (ctrl & uart_regs::CR_TXE) != 0; }
    bool rx_enabled() const noexcept { return (ctrl & uart_regs::CR_RXE) != 0; }

    // False: a failure is pending.
    bool check_interrupts() noexcept {
        if (!raise_irq((interrupt_status & interrupt_mask) != 0)) return false;
        return true;
    }

    // The two DMA requests. TX: whenever the transmitter is enabled, TXDMAE is set and the transmit FIFO has an empty place. RX: whenever the receiver is enabled, RXDMAE is set and the
    // FIFO holds a byte - unless DMAONERR is set and an error interrupt is up ("the DMA receive request outputs ... are disabled when the UART error interrupt is asserted").
    bool update_dreq() noexcept {
        using namespace uart_regs;
        const bool ready = enabled();
        const bool tx = ready && tx_enabled() && (dmacr & DMACR_TXDMAE) != 0 && !tx_full();  // UARTTXDMASREQ: at least one empty place in the TX FIFO
        const bool rx = ready && rx_enabled() && (dmacr & DMACR_RXDMAE) != 0 && !rx_empty() && !((dmacr & DMACR_DMAONERR) != 0 && (interrupt_status & INT_ERRORS) != 0);
        if (!raise_dreq(false, tx)) return false;
        return raise_dreq(true, rx);
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

    // A character that has gone out: onto the line, or into the receiver under loopback ("the UARTTXD path is fed back to the UARTRXD path").
    bool tx_emit(uint32_t byte) noexcept {
        if (ctrl & uart_regs::CR_LBE) return feed_byte(byte);
        return host_.on_byte(host_.ctx, byte);
    }

    // Moves characters from the TX FIFO into the shift register while it is idle (and the UART and the transmitter are enabled). Each move is the FIFO falling by one: from above the trigger
    // level to at or below it raises TXINTR. A character with no time on the wire (no baud rate configured) is delivered at once. `*moved` says whether anything moved. False: a failure is pending.
    bool tx_pump(bool* moved) noexcept {
        using namespace uart_regs;
        *moved = false;
        while (!tx_shifting_ && !tx_empty() && enabled() && tx_enabled()) {
            const uint32_t before = tx_count();
            const uint32_t byte = tx_pull();
            *moved = true;
            double bit = 0.0;
            if (!host_.bit_ns(host_.ctx, &bit)) return false;
            const double ns = bit * static_cast<double>(frame_bits());
            if (ns <= 0) {
                if (!tx_emit(byte)) return false;
            } else {
                tx_shifting_ = true;
                tx_byte_ = byte;
                clock_->schedule(&tx_alarm, ns);
            }
            if (before > tx_trigger() && tx_trigger() >= tx_count()) interrupt_status |= INT_TX;
        }
        return true;
    }

    // Bits on the wire per character: start + data + parity + 1 or 2 stop (LCR_H: PEN bit 1, STP2 bit 3, WLEN bits 6:5).
    uint32_t frame_bits() const noexcept {
        using namespace uart_regs;
        return 1u + (5u + ((line_ctrl >> 5) & 0x3u)) + ((line_ctrl & LCR_PEN) ? 1u : 0u) + ((line_ctrl & LCR_STP2) ? 2u : 1u);
    }

    // False: a failure is pending (the register state is already what the reference's exception would have left).
    bool reset() noexcept {
        ctrl = uart_regs::CR_RXE | uart_regs::CR_TXE;
        line_ctrl = 0;
        rx_reset();
        tx_reset();
        tx_shifting_ = false;
        clock_->cancel(&tx_alarm);
        interrupt_mask = 0;
        interrupt_status = 0;
        int_divisor = 0;
        frac_divisor = 0;
        ifls = uart_regs::IFLS_RESET;
        ilpr = dmacr = rsr = 0;
        if (!raise_irq(false)) return false;
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
        port_.unimplemented_read(offset);
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode; the raw value is `raw_write_value()`. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace uart_regs;
        switch (offset) {
            case DR: {
                // "TXE: Transmit enable": a disabled transmitter (or UART) sends nothing - the byte goes nowhere
                if (!(enabled() && tx_enabled())) return true;
                // A write to a full FIFO is lost, as on the chip (firmware polls UARTFR.TXFF first)
                if (!tx_full()) {
                    tx_push(static_cast<uint32_t>(value) & 0xFFu);
                    if (tx_count() > tx_trigger()) interrupt_status &= ~INT_TX;  // cleared by writes taking the FIFO above the trigger level
                }
                bool moved = false;
                if (!tx_pump(&moved)) return false;
                if (!check_interrupts()) return false;
                return update_dreq();
            }
            case RSR: rsr = 0; return true;  // the write is UARTECR: it clears the error flags
            case ILPR: ilpr = static_cast<uint32_t>(value) & ILPR_MASK; return true;
            case IBRD:
                int_divisor = static_cast<uint32_t>(value) & 0xFFFFu;
                return host_.baud_changed(host_.ctx);
            case FBRD:
                frac_divisor = static_cast<uint32_t>(value) & 0x3Fu;
                return host_.baud_changed(host_.ctx);
            case LCR_H:
                line_ctrl = static_cast<uint32_t>(value) & LCR_H_MASK;
                return update_dreq();  // FEN changes the TX FIFO's depth, and so whether it is full
            case CR: {
                ctrl = static_cast<uint32_t>(value) & CR_MASK;
                bool moved = false;
                if (!tx_pump(&moved)) return false;  // re-enabled with characters waiting
                if (moved && !check_interrupts()) return false;
                return update_dreq();
            }
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
        port_.unimplemented_write(offset, value);
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
    // The character's stop bit has gone out: it reaches the line (or the loopback), and the next one starts.
    static bool on_tx_done(void* ctx) noexcept {
        UartBlock* block = static_cast<UartBlock*>(ctx);
        block->tx_shifting_ = false;
        if (!block->tx_emit(block->tx_byte_)) return false;
        bool moved = false;
        if (!block->tx_pump(&moved)) return false;
        if (!block->check_interrupts()) return false;
        return block->update_dreq();
    }

    BlockPort port_;
    UartHost host_;
    Clock* clock_ = nullptr;
    uint32_t rx_[kRxCapacity] = {};
    uint32_t rx_start_ = 0, rx_used_ = 0;
    uint32_t tx_[kTxDepth] = {};
    uint32_t tx_start_ = 0, tx_used_ = 0, tx_byte_ = 0;
    bool tx_shifting_ = false;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_UART_HPP
