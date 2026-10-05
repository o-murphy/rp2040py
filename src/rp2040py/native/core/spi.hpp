// The RP2040 SPI (PL022) in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of peripherals/_spi.py, which stays as the pure-Python reference and the oracle
// (tests/test_spi_diff.py).
//
// What it owns: the register file (SSPCR0/1, the prescale divisor, the DMA control, the raw and enabled interrupts), the two 8-entry FIFOs, the busy flag, and the whole of the
// block's behaviour towards the guest. It has **no timing of its own**: a byte written to SSPDR leaves through the host's `transmit(value)` and the transfer ends when the device calls
// `complete_transmit(rx_value)` - at once from inside the callback or later from a clock alarm. What it does not own, reached through a `SpiHost` of plain function pointers (see
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
//   - a written value is masked to DSS + 1 bits (so 1..16, the invalid sizes DSS = 0..2 included), a completed one is kept whole; CR0, CR1, IMSC and DMACR are stored whole, CPSR is masked to 0xFE;
//   - the TX interrupt is raw while the TX FIFO holds at most 4 (half of 8), the RX interrupt while the RX FIFO holds at least 4; ICR clears only RT and ROR, by the *decoded* value;
//   - SR's BSY is "busy or the TX FIFO is not empty"; SR, RIS and MIS are read-only: a write to them is an unimplemented one;
//   - both DREQs are re-published on every FIFO change and by `reset()` (and `power_on()`, the constructor's own publication): TX is asserted unless the TX FIFO is full, RX only while the RX
//     FIFO is not empty;
//   - a SET/CLR/XOR alias write decodes against a *read* of the register, and a read of SSPDR pulls a byte from the RX FIFO;
//   - `reset()` clears the registers and both FIFOs, republishes the DREQs and drops the line, and never touches the host's callbacks (wiring, not state) or `raw_write_value`;
//   - an unimplemented offset warns and reads 0xFFFFFFFF; a read above 0x1000 warns a second time.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_SPI_HPP
#define RP2040PY_CORE_SPI_HPP

#include <cstdint>

#include "core_host.hpp"
#include "fifo.hpp"

namespace rp2040core {

using SpiIrqFn = bool (*)(void* ctx, bool level);                       // false: failure parked
using SpiDreqFn = bool (*)(void* ctx, bool tx, bool asserted);          // `tx`: the TX DREQ, else the RX one; false: failure parked
using SpiTransmitFn = bool (*)(void* ctx, uint32_t value);              // a byte (already masked to DSS + 1 bits) is on the wire; false: failure parked
constexpr uint32_t kSpiWarnRead = kRegWarnRead, kSpiWarnReadAtomicArea = kRegWarnReadAtomicArea, kSpiWarnWrite = kRegWarnWrite;
using SpiWarnFn = RegWarnFn;

struct SpiHost {
    SpiIrqFn irq = nullptr;
    SpiDreqFn dreq = nullptr;
    SpiTransmitFn transmit = nullptr;
    SpiWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace spi_regs {
constexpr uint32_t CR0 = 0x000, CR1 = 0x004, DR = 0x008, SR = 0x00C, CPSR = 0x010, IMSC = 0x014, RIS = 0x018, MIS = 0x01C, ICR = 0x020, DMACR = 0x024;
constexpr uint32_t PERIPHID0 = 0xFE0, PERIPHID1 = 0xFE4, PERIPHID2 = 0xFE8, PERIPHID3 = 0xFEC;
constexpr uint32_t PCELLID0 = 0xFF0, PCELLID1 = 0xFF4, PCELLID2 = 0xFF8, PCELLID3 = 0xFFC;
constexpr uint32_t SR_BSY = 1u << 4, SR_RFF = 1u << 3, SR_RNE = 1u << 2, SR_TNF = 1u << 1, SR_TFE = 1u << 0;
constexpr uint32_t CR0_DSS_MASK = 0xF;
constexpr uint32_t CR1_SSE = 1u << 1;
// Read only by the shell's derived properties (`spi_mode`, `master_mode`, `clock_frequency`): the reference tests `MS` (bit 2) against SSPCR0 - the bit is SSPCR1's - and the port keeps that.
[[maybe_unused]] constexpr uint32_t CR0_SPH = 1u << 7, CR0_SPO = 1u << 6, CR0_MS = 1u << 2, CR0_SCR_SHIFT = 8, CR0_SCR_MASK = 0xFF;
constexpr uint32_t CPSR_MASK = 0xFE;
constexpr uint32_t INT_TX = 1u << 3, INT_RX = 1u << 2, INT_RT = 1u << 1, INT_ROR = 1u << 0;
}  // namespace spi_regs

class SpiBlock {
public:
    static constexpr uint32_t kFifoDepth = 8;

    SpiBlock() = default;
    SpiBlock(const SpiBlock&) = delete;  // the window handler holds a pointer to this object
    SpiBlock& operator=(const SpiBlock&) = delete;

    // The registers, public for the shell (the reference's private attributes `_control0`, ... are read and, by tests, written directly).
    uint32_t control0 = 0, control1 = 0, dma_control = 0, clock_divisor = 0;
    uint32_t int_raw = 0, int_enable = 0;
    bool busy = false;
    Fifo<kFifoDepth> rx, tx;

    void init(const SpiHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

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
        control0 = control1 = dma_control = clock_divisor = 0;
        int_raw = int_enable = 0;
        if (!update_dma_tx()) return false;
        if (!update_dma_rx()) return false;
        return host_.irq(host_.ctx, false);
    }

    bool check_interrupts() noexcept { return host_.irq(host_.ctx, int_status() != 0); }

    // The device finishes the transfer in flight (or, with none in flight, hands over a value anyway: the reference accepts it).
    bool complete_transmit(uint32_t rx_value) noexcept {
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
        if (host_.warn) {
            host_.warn(host_.ctx, kSpiWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kSpiWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace spi_regs;
        const uint32_t word = static_cast<uint32_t>(value);
        switch (offset) {
            case CR0: control0 = word; return true;
            case CR1: control1 = word; return true;
            case DR:
                if (!tx.full()) {
                    tx.push(word & ((1u << data_bits()) - 1u));  // decoded with respect to CR0.DSS
                    if (!do_tx()) return false;
                    return fifos_updated();
                }
                return true;
            case CPSR: clock_divisor = word & CPSR_MASK; return true;
            case IMSC:
                int_enable = word;
                return check_interrupts();
            case DMACR: dma_control = word; return true;
            case ICR:
                int_raw &= ~(word & (INT_RT | INT_ROR));
                return check_interrupts();
            default: break;
        }
        if (host_.warn) host_.warn(host_.ctx, kSpiWarnWrite, offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step - remember the raw value, decode the alias against a *read* of the register (with that read's side effects), then write.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) {
            value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        }
        return write(offset, value);
    }

    WindowHandler window_handler() noexcept { return BlockWindow<SpiBlock>::handler(this); }

private:
    bool update_dma_tx() noexcept { return host_.dreq(host_.ctx, true, !tx.full()); }
    bool update_dma_rx() noexcept { return host_.dreq(host_.ctx, false, !rx.empty()); }

    // Sends the next byte if the wire is free. The block is busy from before the host call until the device completes it - which may be before the host call returns.
    bool do_tx() noexcept {
        if (!busy && !tx.empty()) {
            const uint32_t value = tx.pull();
            busy = true;
            if (!host_.transmit(host_.ctx, value)) return false;
            return fifos_updated();
        }
        return true;
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

    SpiHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_SPI_HPP
