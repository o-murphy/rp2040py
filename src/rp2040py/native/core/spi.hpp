// The RP2040 SPI (PL022) in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of peripherals/_spi.py, which stays as the pure-Python reference and the oracle
// (tests/test_spi_diff.py).
//
// What it owns: the register file (SSPCR0/1, the prescale divisor, the DMA control, the raw and enabled interrupts), the two 8-entry FIFOs, the busy flag, and the whole of the
// block's behaviour towards the guest. A byte written to SSPDR leaves through the host's `transmit(value)` and the transfer ends when the device calls
// `complete_transmit(rx_value)` - at once from inside the callback or later from a clock alarm - but not before the word's time on the wire (`data_bits` SSPCLKOUT periods, a frame alarm of the chip's clock): an early reply is held until then. What it does not own, reached through a `SpiHost` of plain function pointers (see
// core_host.hpp): the interrupt line, the two DREQs (the chip's DMA), the transmit callback and the logger.
//
// Re-entrancy is part of the design, not an accident: `do_tx()` marks the block busy and calls `transmit`, which may call `complete_transmit()` before it returns, which pushes the received
// value, updates the FIFOs and calls `do_tx()` again (at most as deep as the TX FIFO). Nothing is held across the host call but the byte being sent; all other state lives in members, exactly
// as in the reference, so the nested call sees - and leaves - the same state the Python one does.
//
// Failures follow the contract of core_host.hpp: a host call that fails (the callback raised) makes the block return `false` at once, leaving the state the reference's exception would have left.
//
// Quirks of the reference that are kept (each one is pinned by tests/test_spi_diff.py or the C++ checks):
//   - a DR write on a full TX FIFO is dropped silently; a completion into a full RX FIFO sets the overrun bit (ROR), raises the line at once and drops the value; a completion with nothing sent is accepted and pushes;
//   - a written value is masked to DSS + 1 bits (so 1..16, the invalid sizes DSS = 0..2 included), a completed one is kept whole; the reserved bits of CR0, CR1, IMSC and DMACR are not stored (datasheet 4.4.4), CPSR is masked to 0xFE (the datasheet: its bit 0 always reads zero);
//   - the TX interrupt is raw while the TX FIFO holds at most 4 (half of 8), the RX interrupt while the RX FIFO holds at least 4; ICR clears only RT and ROR, by the *raw* value the bus passed (a direct write keeps the one the last atomic write left);
//   - SR's BSY is "busy or the TX FIFO is not empty"; SR, RIS and MIS are read-only: a write to them is an unimplemented one;
//   - both DREQs are re-published on every FIFO change, every write of CR1 and DMACR, and by `reset()` (and `power_on()`, the constructor's own publication): TX is asserted while the TX FIFO
//     is not full, RX while the RX FIFO is not empty - and only with SSE and the matching DMACR enable set ("all request signals are deasserted if the PrimeCell SSP is disabled, or the DMA
//     enable signal is cleared", datasheet 4.4.3.16);
//   - a word takes DATA_BITS x SSPCLKOUT (clk_peri / (CPSDVSR x (1 + SCR))) on the wire; the inter-frame gap of SPH = 0 (SSPFSSOUT is pulsed high between words) is not modelled - the datasheet gives it only in a figure and no
//     source for its length was read. A slave (MS) and an unconfigured bit clock have no frame time. Disabling the SSP mid-word does not abort it. A reply before the frame's end is held; one after it completes at once;
//   - nothing leaves a disabled SSP (a primed TX FIFO starts when CR1 enables it, 4.4.3.3); with CR1.LBM the transmitted word comes straight back into the RX FIFO instead of reaching the device;
//   - RIS resets to 0x8 (TX FIFO empty);
//   - a SET/CLR/XOR alias write decodes against a *read* of the register, and a read of SSPDR pulls a byte from the RX FIFO;
//   - `reset()` clears the registers and both FIFOs, republishes the DREQs and drops the line, and never touches the host's callbacks (wiring, not state) or `raw_write_value`;
//   - an unimplemented offset warns and reads 0xFFFFFFFF; a read above 0x1000 warns a second time.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_SPI_HPP
#define RP2040PY_CORE_SPI_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"
#include "fifo.hpp"

namespace rp2040core {

using SpiBitFn = bool (*)(void* ctx, double* nanos);                    // one SSPCLKOUT period (0 while no bit clock is configured); false: failure parked
using SpiTransmitFn = bool (*)(void* ctx, uint32_t value);              // a byte (already masked to DSS + 1 bits) is on the wire; false: failure parked

// The edges of the block that are the host's own (the device on the wire, the bit time); the line, requests, warnings and failure flag are the chip's `ChipPorts`.
struct SpiHost {
    SpiTransmitFn transmit = nullptr;
    SpiBitFn bit_ns = nullptr;
    void* ctx = nullptr;
};

namespace spi_regs {
constexpr uint32_t CR0 = 0x000, CR1 = 0x004, DR = 0x008, SR = 0x00C, CPSR = 0x010, IMSC = 0x014, RIS = 0x018, MIS = 0x01C, ICR = 0x020, DMACR = 0x024;
constexpr uint32_t PERIPHID0 = 0xFE0, PERIPHID1 = 0xFE4, PERIPHID2 = 0xFE8, PERIPHID3 = 0xFEC;
constexpr uint32_t PCELLID0 = 0xFF0, PCELLID1 = 0xFF4, PCELLID2 = 0xFF8, PCELLID3 = 0xFFC;
constexpr uint32_t SR_BSY = 1u << 4, SR_RFF = 1u << 3, SR_RNE = 1u << 2, SR_TNF = 1u << 1, SR_TFE = 1u << 0;
constexpr uint32_t CR0_DSS_MASK = 0xF;
constexpr uint32_t CR1_SSE = 1u << 1, CR1_LBM = 1u << 0;
constexpr uint32_t CR0_MASK = 0xFFFF, CR1_MASK = 0xF, IMSC_MASK = 0xF, DMACR_MASK = 0x3;  // the reserved bits are not stored (datasheet 4.4.4)
constexpr uint32_t DMACR_TXDMAE = 1u << 1, DMACR_RXDMAE = 1u << 0;
// Read only by the shell's derived properties (`spi_mode`, `master_mode`, `clock_frequency`); `CR1_MS` is SSPCR1 bit 2, 0 = master.
[[maybe_unused]] constexpr uint32_t CR0_SPH = 1u << 7, CR0_SPO = 1u << 6, CR1_MS = 1u << 2, CR0_SCR_SHIFT = 8, CR0_SCR_MASK = 0xFF;
constexpr uint32_t CPSR_MASK = 0xFE;
constexpr uint32_t INT_TX = 1u << 3, INT_RX = 1u << 2, INT_RT = 1u << 1, INT_ROR = 1u << 0;
}  // namespace spi_regs

class SpiBlock : public RegisterBlock<SpiBlock> {
public:
    static constexpr uint32_t kFifoDepth = 8;

    SpiBlock() = default;
    SpiBlock(const SpiBlock&) = delete;  // the window handler holds a pointer to this object
    SpiBlock& operator=(const SpiBlock&) = delete;

    // The registers, public for the shell (the reference's private attributes `_control0`, ... are read and, by tests, written directly).
    uint32_t control0 = 0, control1 = 0, dma_control = 0, clock_divisor = 0;
    uint32_t int_raw = spi_regs::INT_TX, int_enable = 0;  // SSPRIS resets to 0x8: the TX FIFO is empty
    bool busy = false;
    Fifo<kFifoDepth> rx, tx;

    // `clock` carries the frame alarm: a word occupies the wire for `data_bits` SSPCLKOUT periods from the moment it leaves the TX FIFO, and the device's reply is held until then.
    // `irq_line` is the instance's NVIC line, `dreq_base` its TX request (RX follows).
    void init(Clock* clock, const ChipPorts* ports, uint32_t block, uint32_t irq_line, uint32_t dreq_base, const SpiHost& host) noexcept {
        port_.init(ports, block, irq_line, dreq_base);
        clock_ = clock;
        host_ = host;
        frame_active = has_reply = false;
        frame_alarm.fire = &SpiBlock::on_frame_done;
        frame_alarm.ctx = this;
    }

    // Unlinks the frame alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept { cancel_alarm(&frame_alarm); }

    Alarm frame_alarm;
    bool frame_active = false, has_reply = false;  // a word is being shifted out / the device has answered before its last bit
    uint32_t reply = 0;


    uint32_t int_status() const noexcept { return int_raw & int_enable; }
    bool enabled() const noexcept { return (control1 & spi_regs::CR1_SSE) != 0; }
    uint32_t data_bits() const noexcept { return (control0 & spi_regs::CR0_DSS_MASK) + 1; }

    // The constructor's own DREQ publication (the reference calls both updates from `__init__`). False: a failure is pending.
    bool power_on() noexcept {
        if (!update_dma_tx()) return false;
        return update_dma_rx();
    }

    // False: a failure is pending (the register state is already what the reference's exception would have left).
    bool reset() noexcept {
        rx.reset();
        tx.reset();
        busy = false;
        frame_active = false;
        has_reply = false;
        clock_->cancel(&frame_alarm);
        control0 = control1 = dma_control = clock_divisor = 0;
        int_raw = spi_regs::INT_TX;
        int_enable = 0;
        if (!update_dma_tx()) return false;
        if (!update_dma_rx()) return false;
        return raise_irq(false);
    }

    bool check_interrupts() noexcept { return raise_irq(int_status() != 0); }

    // The device finishes the transfer in flight (or, with none in flight, hands over a value anyway: the reference accepts it).
    bool complete_transmit(uint32_t rx_value) noexcept {
        if (frame_active) {  // the word is still being shifted out: it completes when its last bit has gone
            reply = rx_value;
            has_reply = true;
            return true;
        }
        return deliver(rx_value);
    }

    bool deliver(uint32_t rx_value) noexcept {
        busy = false;
        if (!rx.full()) {
            rx.push(rx_value);
        } else {
            int_raw |= spi_regs::INT_ROR;
            if (!check_interrupts()) return false;  // the line is the OR of the enabled raw interrupts: the overrun reaches it at once
        }
        if (!fifos_updated()) return false;
        return do_tx();
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace spi_regs;
        switch (offset) {
            case CR0: return control0;
            case CR1: return control1;
            case DR: {
                if (!rx.empty()) {
                    const uint32_t value = rx.pull();
                    (void)fifos_updated();
                    return value;
                }
                return 0;
            }
            case SR:
                return ((busy || !tx.empty()) ? SR_BSY : 0u) | (rx.full() ? SR_RFF : 0u) | (!rx.empty() ? SR_RNE : 0u) | (!tx.full() ? SR_TNF : 0u) | (tx.empty() ? SR_TFE : 0u);
            case CPSR: return clock_divisor;
            case IMSC: return int_enable;
            case RIS: return int_raw;
            case MIS: return int_status();
            case DMACR: return dma_control;
            case PERIPHID0: return 0x22;
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

    // `value` is the value after any atomic-alias decode. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace spi_regs;
        const uint32_t word = static_cast<uint32_t>(value);
        switch (offset) {
            case CR0: control0 = word & CR0_MASK; return true;
            case CR1:
                control1 = word & CR1_MASK;
                if (!do_tx()) return false;  // enabling starts a primed TX FIFO, and SSE gates the DMA requests
                return fifos_updated();
            case DR:
                if (!tx.full()) {
                    tx.push(word & ((1u << data_bits()) - 1u));  // decoded with respect to CR0.DSS
                    if (!do_tx()) return false;
                    return fifos_updated();
                }
                return true;
            case CPSR: clock_divisor = word & CPSR_MASK; return true;
            case IMSC:
                int_enable = word & IMSC_MASK;
                return check_interrupts();
            case DMACR:
                dma_control = word & DMACR_MASK;
                if (!update_dma_tx()) return false;
                return update_dma_rx();
            case ICR:
                int_raw &= ~(static_cast<uint32_t>(raw_write_value_) & (INT_RT | INT_ROR));  // the bits the bus passed, whatever the alias (as the UART's ICR)
                return check_interrupts();
            default: break;
        }
        port_.unimplemented_write(offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step - remember the raw value, decode the alias against a *read* of the register (with that read's side effects), then write.


private:
    // "All request signals are deasserted if the PrimeCell SSP is disabled, or the DMA enable signal is cleared" (datasheet 4.4.3.16).
    bool update_dma_tx() noexcept { return raise_dreq(true, !tx.full() && enabled() && (dma_control & spi_regs::DMACR_TXDMAE)); }
    bool update_dma_rx() noexcept { return raise_dreq(false, !rx.empty() && enabled() && (dma_control & spi_regs::DMACR_RXDMAE)); }

    // Sends the next byte if the wire is free. The block is busy from before the host call until the device completes it - which may be before the host call returns.
    bool do_tx() noexcept {
        if (!busy && !tx.empty() && enabled()) {  // nothing leaves a disabled SSP (datasheet 4.4.3.3)
            const uint32_t value = tx.pull();
            busy = true;
            double ns = 0.0;
            if (!frame_ns(&ns)) return false;
            if (ns > 0) {
                frame_active = true;
                clock_->schedule(&frame_alarm, ns);
            }
            if (control1 & spi_regs::CR1_LBM) {  // loop back: the transmit shifter feeds the receive shifter
                if (!complete_transmit(value)) return false;
            } else if (!host_.transmit(host_.ctx, value)) {
                return false;
            }
            return fifos_updated();
        }
        return true;
    }

    // How long the word that just left the TX FIFO is on the wire. A slave (CR1.MS) is clocked from outside, and an unconfigured bit clock has no time: both are instant.
    bool frame_ns(double* ns) noexcept {
        *ns = 0.0;
        if (control1 & spi_regs::CR1_MS) return true;
        double bit = 0.0;
        if (!host_.bit_ns(host_.ctx, &bit)) return false;
        *ns = bit * static_cast<double>(data_bits());
        return true;
    }

    // The word's last bit has gone: the device's reply, if it already came, completes the transfer; otherwise BSY stays up until it does.
    static bool on_frame_done(void* ctx) noexcept {
        SpiBlock* block = static_cast<SpiBlock*>(ctx);
        block->frame_active = false;
        if (!block->has_reply) return true;
        block->has_reply = false;
        return block->deliver(block->reply);
    }

    bool fifos_updated() noexcept {
        using namespace spi_regs;
        const uint32_t prev_status = int_status();
        if (tx.count() <= kFifoDepth / 2) {
            int_raw |= INT_TX;
        } else {
            int_raw &= ~INT_TX;
        }
        if (rx.count() >= kFifoDepth / 2) {
            int_raw |= INT_RX;
        } else {
            int_raw &= ~INT_RX;
        }
        if (int_status() != prev_status) {
            if (!check_interrupts()) return false;
        }
        if (!update_dma_tx()) return false;
        return update_dma_rx();
    }

    bool raise_dreq(bool tx, bool asserted) noexcept { return port_.dreq(tx ? 0u : 1u, asserted); }
    bool raise_irq(bool level) noexcept { return port_.irq(level); }

    SpiHost host_;
    Clock* clock_ = nullptr;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_SPI_HPP
