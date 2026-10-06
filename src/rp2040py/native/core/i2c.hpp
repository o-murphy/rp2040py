// The RP2040 I2C (DW_apb_i2c, master side) in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of peripherals/_i2c.py, which stays as the pure-Python
// reference and the oracle (tests/test_i2c_diff.py).
//
// What it owns: the register file, the two 16-entry FIFOs (the command/TX FIFO carries a data byte plus the CMD, STOP and RESTART bits; the RX FIFO the received bytes plus a
// FIRST_DATA_BYTE flag), the interrupt pair, and the bus state machine (IDLE, START, CONNECT, CONNECTED, STOP) with its busy / stop / pending-restart / first-byte flags. It has **no
// timing of its own** and does not model a wire: it asks the *device on the bus* - through the host - to start, to connect to an address, to take a byte, to give a byte and to stop,
// and each answer arrives later (or at once, from inside the callback) as `complete_start()`, `complete_connect(ack, nack_byte)`, `complete_write(ack)`, `complete_read(value)`,
// `complete_stop()`. What it does not own, reached through an `I2cHost` of plain function pointers (see core_host.hpp): the interrupt line, the five device callbacks and the logger.
//
// Re-entrancy is the design, not an accident: `next_command()` marks the block busy and calls a callback, which may call the matching `complete_*` before it returns, which updates the
// state and calls `next_command()` again - down the whole queue of commands in one call stack. Nothing is held across a host call but what the reference also holds (the command byte
// just pulled); all other state lives in members, so a nested call sees - and leaves - the state the Python one does.
//
// Failures follow the contract of core_host.hpp: a host call that fails makes the block return `false` at once, leaving the state the reference's exception would have left.
//
// Quirks of the reference that are kept (each pinned by tests/test_i2c_diff.py or the C++ checks):
//   - the default device (no callback set): start and stop complete at once, **every connect is NACKed**, a write is NACKed, a read returns 0xFF - the shell completes these in C++;
//   - reads have side effects: IC_DATA_CMD pulls a byte (an empty FIFO sets RX_UNDER and reads 0), every IC_CLR_* clears its interrupts and reads 1 if it cleared any, IC_TX_ABRT_SOURCE
//     clears itself (keeping bit 9, which nothing ever sets), IC_CLR_INTR and IC_CLR_TX_ABRT clear it as well; an alias write decodes against a *read*;
//   - IC_INTR_MASK is writable (13 bits, reset value 0x8FF; a write re-evaluates the line); IC_DMA_CR/TDLR/RDLR, IC_SDA_SETUP, IC_ACK_GENERAL_CALL, IC_SLV_DATA_NACK_ONLY and IC_SDA_HOLD are
//     stored with their datasheet masks and reset values and *not acted on* (no slave, no I2C DREQs: a feature in the backlog);
//   - `abort()` ORs the reason into IC_TX_ABRT_SOURCE (reasons accumulate until read), replaces the flush count (bits 31:23) with the number of commands dropped, empties both FIFOs and raises TX_ABRT;
//   - IC_CON, IC_TAR, IC_SAR, the four SCL counts, IC_SDA_HOLD, IC_SDA_SETUP, IC_FS_SPKLEN and IC_SLV_DATA_NACK_ONLY are written only while IC_ENABLE[0] = 0 (datasheet 4.3.17), with the
//     datasheet's widths (IC_CON 9:0, IC_TAR 11:0, IC_SAR 9:0, the counts 15:0, IC_SDA_HOLD 23:0) and minima (the counts 6, 8, 6, 8, IC_FS_SPKLEN 1); IC_CON with the speed field 0 is
//     rewritten to 3 (high speed); the thresholds are 8 bits and then the FIFO size; IC_DATA_CMD keeps 11 bits of a write, and FIRST_DATA_BYTE is bit 11 of what it reads;
//   - IC_ENABLE keeps bits 2:0 and a set ABORT bit (software cannot clear it), drops it when the bus is idle, aborts and sets `stop` otherwise, and empties both FIFOs when ENABLE is cleared;
//   - RX_FULL is a level (raw while the RX FIFO holds more than IC_RX_TL entries: re-evaluated on every push, pull, flush and IC_RX_TL write); TX_EMPTY is raised by a command being taken and
//     cleared by a write only when the level goes above IC_TX_TL; ACTIVITY is raised when the master starts, cleared by IC_CLR_ACTIVITY / IC_CLR_INTR only while idle, and by disabling;
//   - `reset()` clears the registers, the FIFOs and the state machine and drops the line, and never touches the host's callbacks (wiring, not state) or `raw_write_value`;
//   - an unimplemented offset warns and reads 0xFFFFFFFF (a read above 0x1000 warns a second time); an unimplemented write warns.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_I2C_HPP
#define RP2040PY_CORE_I2C_HPP

#include <cstdint>

#include "core_host.hpp"
#include "fifo.hpp"

namespace rp2040core {

using I2cIrqFn = bool (*)(void* ctx, bool level);                          // false: failure parked
using I2cStartFn = bool (*)(void* ctx, bool repeated);                     // the device is asked to produce a (repeated) START
using I2cConnectFn = bool (*)(void* ctx, uint32_t address, uint32_t mode); // ... to address a target; mode 0 = write, 1 = read
using I2cWriteFn = bool (*)(void* ctx, uint32_t value);                    // ... to take a byte
using I2cReadFn = bool (*)(void* ctx, bool ack);                           // ... to give a byte (and be ACKed unless it is the last)
using I2cStopFn = bool (*)(void* ctx);                                     // ... to produce a STOP
constexpr uint32_t kI2cWarnRead = kRegWarnRead, kI2cWarnReadAtomicArea = kRegWarnReadAtomicArea, kI2cWarnWrite = kRegWarnWrite;
using I2cWarnFn = RegWarnFn;

struct I2cHost {
    I2cIrqFn irq = nullptr;
    I2cStartFn start = nullptr;
    I2cConnectFn connect = nullptr;
    I2cWriteFn write_byte = nullptr;
    I2cReadFn read_byte = nullptr;
    I2cStopFn stop = nullptr;
    I2cWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace i2c_regs {
constexpr uint32_t CON = 0x00, TAR = 0x04, SAR = 0x08, DATA_CMD = 0x10, SS_SCL_HCNT = 0x14, SS_SCL_LCNT = 0x18, FS_SCL_HCNT = 0x1C, FS_SCL_LCNT = 0x20;
constexpr uint32_t INTR_STAT = 0x2C, INTR_MASK = 0x30, RAW_INTR_STAT = 0x34, RX_TL = 0x38, TX_TL = 0x3C;
constexpr uint32_t CLR_INTR = 0x40, CLR_RX_UNDER = 0x44, CLR_RX_OVER = 0x48, CLR_TX_OVER = 0x4C, CLR_RD_REQ = 0x50, CLR_TX_ABRT = 0x54, CLR_RX_DONE = 0x58;
constexpr uint32_t CLR_ACTIVITY = 0x5C, CLR_STOP_DET = 0x60, CLR_START_DET = 0x64, CLR_GEN_CALL = 0x68;
constexpr uint32_t ENABLE = 0x6C, STATUS = 0x70, TXFLR = 0x74, RXFLR = 0x78, SDA_HOLD = 0x7C, TX_ABRT_SOURCE = 0x80;
constexpr uint32_t SLV_DATA_NACK_ONLY = 0x84, DMA_CR = 0x88, DMA_TDLR = 0x8C, DMA_RDLR = 0x90, SDA_SETUP = 0x94, ACK_GENERAL_CALL = 0x98, CLR_RESTART_DET = 0xA8;
constexpr uint32_t ENABLE_STATUS = 0x9C, FS_SPKLEN = 0xA0, COMP_PARAM_1 = 0xF4, COMP_VERSION = 0xF8, COMP_TYPE = 0xFC;
// IC_CON
constexpr uint32_t CON_SLAVE_DISABLE = 1u << 6, CON_RESTART_EN = 1u << 5, CON_10BIT_MASTER = 1u << 4, CON_SPEED_SHIFT = 1, CON_SPEED_MASK = 0x3, CON_MASTER = 1u << 0;
// (`SPEED_STANDARD` and the interrupt names nothing in the block raises are read by the shell and the checks)
[[maybe_unused]] constexpr uint32_t SPEED_STANDARD = 1;
constexpr uint32_t SPEED_INVALID = 0, SPEED_FAST = 2, SPEED_HIGH = 3;
// IC_STATUS
constexpr uint32_t ST_MST_ACTIVITY = 1u << 5, ST_RFF = 1u << 4, ST_RFNE = 1u << 3, ST_TFE = 1u << 2, ST_TFNF = 1u << 1, ST_ACTIVITY = 1u << 0;
// IC_ENABLE
constexpr uint32_t EN_TX_CMD_BLOCK = 1u << 2, EN_ABORT = 1u << 1, EN_ENABLE = 1u << 0;
// IC_TX_ABRT_SOURCE
constexpr uint32_t TX_FLUSH_CNT_MASK = 0x1FF, TX_FLUSH_CNT_SHIFT = 23;
constexpr uint32_t ABRT_USER_ABRT = 1u << 16, ARB_LOST = 1u << 12, ABRT_SBYTE_NORSTRT = 1u << 9, ABRT_GCALL_NOACK = 1u << 4, ABRT_TXDATA_NOACK = 1u << 3;
constexpr uint32_t ABRT_10ADDR2_NOACK = 1u << 2, ABRT_10ADDR1_NOACK = 1u << 1, ABRT_7B_ADDR_NOACK = 1u << 0;
// interrupts
constexpr uint32_t R_RESTART_DET = 1u << 12;
constexpr uint32_t R_GEN_CALL = 1u << 11, R_START_DET = 1u << 10, R_STOP_DET = 1u << 9, R_ACTIVITY = 1u << 8, R_RX_DONE = 1u << 7;
constexpr uint32_t R_TX_ABRT = 1u << 6, R_RD_REQ = 1u << 5, R_TX_EMPTY = 1u << 4, R_TX_OVER = 1u << 3, R_RX_FULL = 1u << 2, R_RX_OVER = 1u << 1, R_RX_UNDER = 1u << 0;
// IC_INTR_MASK: reset value 0x8FF (datasheet), 13 bits
constexpr uint32_t INTR_MASK_RESET = 0x8FF, INTR_MASK_BITS = 0x1FFF;
// Writable bits (datasheet 4.3.17: IC_CON 9:0 - bit 10 is read only -, IC_TAR 11:0, IC_DATA_CMD 10:0 of a write, IC_SDA_HOLD 23:0, IC_ENABLE 2:0)
constexpr uint32_t CON_MASK = 0x3FF, TAR_MASK = 0xFFF, DATA_CMD_MASK = 0x7FF, SDA_HOLD_MASK = 0xFFFFFF, ENABLE_MASK = 0x7;
// "The minimum valid value is 6 / 8 ...; hardware prevents values less than this being written, and if attempted results in 6 / 8 being set"; IC_FS_SPKLEN's minimum is 1
constexpr uint32_t SS_HCNT_MIN = 6, SS_LCNT_MIN = 8, FS_HCNT_MIN = 6, FS_LCNT_MIN = 8, SPKLEN_MIN = 1;
// FIFO entry bits (datasheet IC_DATA_CMD: FIRST_DATA_BYTE is bit 11 of what is read, RESTART bit 10 of what is written)
constexpr uint32_t FIRST_DATA_BYTE = 1u << 11, RESTART = 1u << 10, STOP = 1u << 9, CMD = 1u << 8;
// the state machine
constexpr uint32_t STATE_IDLE = 0, STATE_START = 1, STATE_CONNECT = 2, STATE_CONNECTED = 3, STATE_STOP = 4;
constexpr uint32_t MODE_WRITE = 0, MODE_READ = 1;
}  // namespace i2c_regs

class I2cBlock {
public:
    static constexpr uint32_t kFifoDepth = 16;

    I2cBlock() = default;
    I2cBlock(const I2cBlock&) = delete;  // the window handler holds a pointer to this object
    I2cBlock& operator=(const I2cBlock&) = delete;

    // The registers and the machine, public for the shell (the reference's attributes are read and, by tests, written directly).
    uint32_t state = i2c_regs::STATE_IDLE;
    bool busy = false, stop = false, pending_restart = false, first_byte = false;
    uint32_t enable = 0, rx_threshold = 0, tx_threshold = 0;
    uint32_t control = i2c_regs::CON_SLAVE_DISABLE | i2c_regs::CON_RESTART_EN | (i2c_regs::SPEED_FAST << i2c_regs::CON_SPEED_SHIFT) | i2c_regs::CON_MASTER;
    uint32_t ss_clock_high = 0x0028, ss_clock_low = 0x002F, fs_clock_high = 0x0006, fs_clock_low = 0x000D;
    uint32_t target_address = 0x55, slave_address = 0x55;
    uint32_t abort_source = 0, int_raw = 0, int_enable = i2c_regs::INTR_MASK_RESET, spikelen = 0x07;
    // Stored, not acted on (slave mode and the DMA interface are not modelled): the datasheet's reset values.
    uint32_t sda_hold = 0x1, sda_setup = 0x64, ack_general_call = 0x1, slv_data_nack_only = 0, dma_control = 0, dma_tdlr = 0, dma_rdlr = 0;
    Fifo<kFifoDepth> rx, tx;

    void init(const I2cHost& host) noexcept { host_ = host; }

    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }
    uint32_t int_status() const noexcept { return int_raw & int_enable; }
    uint32_t master_bits() const noexcept { return (control & i2c_regs::CON_10BIT_MASTER) ? 10 : 7; }

    // False: a failure is pending (the state is already what the reference's exception would have left).
    bool reset() noexcept {
        using namespace i2c_regs;
        state = STATE_IDLE;
        busy = stop = pending_restart = first_byte = false;
        rx.reset();
        tx.reset();
        enable = rx_threshold = tx_threshold = 0;
        control = CON_SLAVE_DISABLE | CON_RESTART_EN | (SPEED_FAST << CON_SPEED_SHIFT) | CON_MASTER;
        ss_clock_high = 0x0028;
        ss_clock_low = 0x002F;
        fs_clock_high = 0x0006;
        fs_clock_low = 0x000D;
        target_address = slave_address = 0x55;
        abort_source = int_raw = 0;
        int_enable = INTR_MASK_RESET;
        spikelen = 0x07;
        sda_hold = 0x1;
        sda_setup = 0x64;
        ack_general_call = 0x1;
        slv_data_nack_only = dma_control = dma_tdlr = dma_rdlr = 0;
        return host_.irq(host_.ctx, false);
    }

    bool check_interrupts() noexcept { return host_.irq(host_.ctx, int_status() != 0); }

    // ---- the answers of the device on the bus -------------------------------------------------------------------

    bool complete_start() noexcept {
        using namespace i2c_regs;
        if (tx.empty() || state != STATE_START || stop) return host_.stop(host_.ctx);
        const uint32_t mode = (tx.peek() & CMD) ? MODE_READ : MODE_WRITE;
        state = STATE_CONNECT;
        if (!set_interrupts(R_START_DET)) return false;
        const uint32_t address_mask = master_bits() == 10 ? 0x3FFu : 0xFFu;
        return host_.connect(host_.ctx, target_address & address_mask, mode);
    }

    bool complete_connect(bool ack, uint32_t nack_byte = 0) noexcept {
        using namespace i2c_regs;
        if (!ack || stop) {
            if (!ack) {
                bool ok;
                if (target_address == 0) {
                    ok = abort(ABRT_GCALL_NOACK);
                } else if (control & CON_10BIT_MASTER) {
                    ok = abort(nack_byte == 0 ? ABRT_10ADDR1_NOACK : ABRT_10ADDR2_NOACK);
                } else {
                    ok = abort(ABRT_7B_ADDR_NOACK);
                }
                if (!ok) return false;
            }
            state = STATE_STOP;
            return host_.stop(host_.ctx);
        }
        state = STATE_CONNECTED;
        busy = false;
        first_byte = true;
        return next_command();
    }

    bool complete_write(bool ack) noexcept {
        using namespace i2c_regs;
        if (!ack || stop) {
            if (!ack) {
                if (!abort(ABRT_TXDATA_NOACK)) return false;
            }
            state = STATE_STOP;
            return host_.stop(host_.ctx);
        }
        busy = false;
        return next_command();
    }

    bool complete_read(uint32_t value) noexcept {
        using namespace i2c_regs;
        if (!push_rx(value | (first_byte ? FIRST_DATA_BYTE : 0u))) return false;
        if (stop) {
            state = STATE_STOP;
            return host_.stop(host_.ctx);
        }
        first_byte = false;
        busy = false;
        return next_command();
    }

    bool complete_stop() noexcept {
        using namespace i2c_regs;
        state = STATE_IDLE;
        if (!set_interrupts(R_STOP_DET)) return false;
        busy = false;
        pending_restart = false;
        if (enable & EN_ABORT) {
            enable &= ~EN_ABORT;
            return true;
        }
        return next_command();
    }

    bool arbitration_lost() noexcept {
        state = i2c_regs::STATE_IDLE;
        busy = false;
        return abort(i2c_regs::ARB_LOST);
    }

    // ---- registers ----------------------------------------------------------------------------------------------

    // Reads a register. A failure of a host call leaves the flag raised and the value already computed (the reference had raised after the same side effects).
    uint32_t read(uint32_t offset) noexcept {
        using namespace i2c_regs;
        uint32_t cleared = 0;
        switch (offset) {
            case CON: return control;
            case TAR: return target_address;
            case SAR: return slave_address;
            case DATA_CMD:
                if (rx.empty()) {
                    (void)set_interrupts(R_RX_UNDER);
                    return 0;
                }
                {
                    const uint32_t value = rx.pull();
                    (void)update_rx_full();
                    return value;
                }
            case SS_SCL_HCNT: return ss_clock_high;
            case SS_SCL_LCNT: return ss_clock_low;
            case FS_SCL_HCNT: return fs_clock_high;
            case FS_SCL_LCNT: return fs_clock_low;
            case INTR_STAT: return int_status();
            case INTR_MASK: return int_enable;
            case RAW_INTR_STAT: return int_raw;
            case RX_TL: return rx_threshold;
            case TX_TL: return tx_threshold;
            case CLR_INTR:
                abort_source &= ABRT_SBYTE_NORSTRT;  // Clear IC_TX_ABRT_SOURCE, except for bit 9
                (void)clear_interrupts(R_RX_UNDER | R_RX_OVER | R_TX_OVER | R_RD_REQ | R_TX_ABRT | R_RX_DONE | clearable_activity() | R_STOP_DET | R_START_DET | R_GEN_CALL, &cleared);
                return cleared;
            case CLR_RX_UNDER: (void)clear_interrupts(R_RX_UNDER, &cleared); return cleared;
            case CLR_RX_OVER: (void)clear_interrupts(R_RX_OVER, &cleared); return cleared;
            case CLR_TX_OVER: (void)clear_interrupts(R_TX_OVER, &cleared); return cleared;
            case CLR_RD_REQ: (void)clear_interrupts(R_RD_REQ, &cleared); return cleared;
            case CLR_TX_ABRT:
                abort_source &= ABRT_SBYTE_NORSTRT;
                (void)clear_interrupts(R_TX_ABRT, &cleared);
                return cleared;
            case CLR_RX_DONE: (void)clear_interrupts(R_RX_DONE, &cleared); return cleared;
            case CLR_ACTIVITY: (void)clear_interrupts(clearable_activity(), &cleared); return cleared;
            case CLR_RESTART_DET: (void)clear_interrupts(R_RESTART_DET, &cleared); return cleared;
            case CLR_STOP_DET: (void)clear_interrupts(R_STOP_DET, &cleared); return cleared;
            case CLR_START_DET: (void)clear_interrupts(R_START_DET, &cleared); return cleared;
            case CLR_GEN_CALL: (void)clear_interrupts(R_GEN_CALL, &cleared); return cleared;
            case ENABLE: return enable;
            case STATUS:
                return (state != STATE_IDLE ? (ST_MST_ACTIVITY | ST_ACTIVITY) : 0u) | (rx.full() ? ST_RFF : 0u) | (!rx.empty() ? ST_RFNE : 0u) | (tx.empty() ? ST_TFE : 0u) |
                       (!tx.full() ? ST_TFNF : 0u);
            case TXFLR: return tx.count();
            case RXFLR: return rx.count();
            case SDA_HOLD: return sda_hold;
            case SLV_DATA_NACK_ONLY: return slv_data_nack_only;
            case DMA_CR: return dma_control;
            case DMA_TDLR: return dma_tdlr;
            case DMA_RDLR: return dma_rdlr;
            case SDA_SETUP: return sda_setup;
            case ACK_GENERAL_CALL: return ack_general_call;
            case TX_ABRT_SOURCE: {
                const uint32_t value = abort_source;
                abort_source &= ABRT_SBYTE_NORSTRT;
                return value;
            }
            case ENABLE_STATUS: return enable & 0x1;  // read only: bit 0 reflects IC_ENABLE, bits 1 and 2 relate to slave mode
            case FS_SPKLEN: return spikelen & 0xFFu;
            case COMP_PARAM_1: return 0;  // "not implemented and therefore reads as 0" (datasheet)
            case COMP_VERSION: return 0x3230312Au;
            case COMP_TYPE: return 0x44570140u;
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kI2cWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kI2cWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace i2c_regs;
        uint32_t word = static_cast<uint32_t>(value);
        uint32_t cleared = 0;
        switch (offset) {
            case CON:
                if (((word >> CON_SPEED_SHIFT) & CON_SPEED_MASK) == SPEED_INVALID) word = (word & ~(CON_SPEED_MASK << CON_SPEED_SHIFT)) | (SPEED_HIGH << CON_SPEED_SHIFT);
                if (config_open()) control = word & CON_MASK;
                return true;
            case TAR:
                if (config_open()) target_address = word & TAR_MASK;
                return true;
            case SAR:
                if (config_open()) slave_address = word & 0x3FFu;
                return true;
            case DATA_CMD:
                if (tx.full()) return set_interrupts(R_TX_OVER);
                tx.push(word & DATA_CMD_MASK);
                if (tx.count() > tx_threshold) {  // "automatically cleared by hardware when the buffer level goes above the threshold"
                    if (!clear_interrupts(R_TX_EMPTY, &cleared)) return false;
                }
                return next_command();
            case INTR_MASK:
                int_enable = word & INTR_MASK_BITS;
                return check_interrupts();
            case SS_SCL_HCNT:
                if (config_open()) ss_clock_high = max_u32(word & 0xFFFFu, SS_HCNT_MIN);
                return true;
            case SS_SCL_LCNT:
                if (config_open()) ss_clock_low = max_u32(word & 0xFFFFu, SS_LCNT_MIN);
                return true;
            case FS_SCL_HCNT:
                if (config_open()) fs_clock_high = max_u32(word & 0xFFFFu, FS_HCNT_MIN);
                return true;
            case FS_SCL_LCNT:
                if (config_open()) fs_clock_low = max_u32(word & 0xFFFFu, FS_LCNT_MIN);
                return true;
            case SDA_HOLD:
                if (config_open()) sda_hold = word & SDA_HOLD_MASK;
                return true;
            case SDA_SETUP:
                if (config_open()) sda_setup = word & 0xFFu;
                return true;
            case SLV_DATA_NACK_ONLY:
                if (config_open()) slv_data_nack_only = word & 0x1u;  // the slave part is never active here
                return true;
            case ACK_GENERAL_CALL: ack_general_call = word & 0x1u; return true;
            case DMA_CR: dma_control = word & 0x3u; return true;
            case DMA_TDLR: dma_tdlr = word & 0xFu; return true;
            case DMA_RDLR: dma_rdlr = word & 0xFu; return true;
            case RX_TL:
                rx_threshold = word & 0xFFu;
                if (rx_threshold > kFifoDepth) rx_threshold = kFifoDepth;
                return update_rx_full();
            case TX_TL:
                tx_threshold = word & 0xFFu;
                if (tx_threshold > kFifoDepth) tx_threshold = kFifoDepth;
                return true;
            case ENABLE:
                word &= ENABLE_MASK;
                word |= enable & EN_ABORT;  // ABORT can only be set by software, not cleared
                if (word & EN_ABORT) {
                    if (state == STATE_IDLE) {
                        word &= ~EN_ABORT;
                    } else {
                        if (!abort(ABRT_USER_ABRT)) return false;
                        stop = true;
                    }
                }
                if (!(word & EN_ENABLE)) {
                    tx.reset();
                    rx.reset();
                    if (!update_rx_full()) return false;  // the RX FIFO is flushed and held in reset
                    uint32_t ignored = 0;
                    if (!clear_interrupts(R_ACTIVITY, &ignored)) return false;  // "Disabling the DW_apb_i2c" clears the ACTIVITY bit
                }
                enable = word;
                return next_command();  // TX_CMD_BLOCK may have changed
            case FS_SPKLEN:
                if (!(enable & EN_ENABLE)) spikelen = max_u32(word & 0xFFu, SPKLEN_MIN);  // only while disabled; 8 bits, minimum 1
                return true;
            default: break;
        }
        if (host_.warn) host_.warn(host_.ctx, kI2cWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<I2cBlock>::handler(this); }

private:
    static uint32_t max_u32(uint32_t a, uint32_t b) noexcept { return a > b ? a : b; }

    // The configuration registers "can be written only when the I2C interface is disabled" (IC_ENABLE[0] = 0); a write at any other time has no effect.
    bool config_open() const noexcept { return (enable & i2c_regs::EN_ENABLE) == 0; }

    // A read of IC_CLR_ACTIVITY / IC_CLR_INTR "clears the ACTIVITY interrupt if the I2C is not active anymore"; while it is, the bit stays set.
    uint32_t clearable_activity() const noexcept { return state == i2c_regs::STATE_IDLE ? i2c_regs::R_ACTIVITY : 0u; }

    // RX_FULL is a level: set while the RX FIFO holds more than RX_TL entries, cleared as soon as it does not.
    bool update_rx_full() noexcept {
        using namespace i2c_regs;
        if (rx.count() > rx_threshold) return set_interrupts(R_RX_FULL);
        uint32_t ignored = 0;
        return clear_interrupts(R_RX_FULL, &ignored);
    }

    bool set_interrupts(uint32_t mask) noexcept {
        if (!(int_raw & mask)) {
            int_raw |= mask;
            return check_interrupts();
        }
        return true;
    }

    // `*result` is what the register read returns: 1 if any of `mask` was raw. False: a failure is pending.
    bool clear_interrupts(uint32_t mask, uint32_t* result) noexcept {
        if (int_raw & mask) {
            int_raw &= ~mask;
            *result = 1;
            return check_interrupts();
        }
        *result = 0;
        return true;
    }

    bool abort(uint32_t reason) noexcept {
        using namespace i2c_regs;
        abort_source &= ~(TX_FLUSH_CNT_MASK << TX_FLUSH_CNT_SHIFT);  // the reasons accumulate until read; the flush count is replaced
        abort_source |= reason | (tx.count() << TX_FLUSH_CNT_SHIFT);
        tx.reset();
        rx.reset();  // "flushes/resets/empties the TX_FIFO and RX_FIFO whenever there is a transmit abort"
        if (!update_rx_full()) return false;
        return set_interrupts(R_TX_ABRT);
    }

    bool push_rx(uint32_t value) noexcept {
        using namespace i2c_regs;
        if (rx.full()) return set_interrupts(R_RX_OVER);
        rx.push(value);
        return update_rx_full();
    }

    // Starts the next step of the transfer if the block is enabled, not blocked and not already waiting for the device. May be re-entered from the callbacks it makes.
    bool next_command() noexcept {
        using namespace i2c_regs;
        const bool enabled = (enable & EN_ENABLE) != 0;
        const bool blocked = (enable & EN_TX_CMD_BLOCK) != 0;
        if (tx.empty() || busy || blocked || !enabled) return true;
        busy = true;
        const bool restart = (tx.peek() & RESTART) != 0 && !pending_restart && !stop;
        if (state == STATE_IDLE || restart) {
            if (!set_interrupts(R_ACTIVITY)) return false;  // "captures activity and stays set until it is cleared"
            pending_restart = restart;
            stop = false;
            state = STATE_START;
            return host_.start(host_.ctx, restart);
        }
        pending_restart = false;
        const uint32_t cmd = tx.pull();
        const bool read_mode = (cmd & CMD) != 0;
        stop = (cmd & STOP) != 0;
        if (read_mode) {
            if (!host_.read_byte(host_.ctx, !stop)) return false;
        } else {
            if (!host_.write_byte(host_.ctx, cmd & 0xFFu)) return false;
        }
        if (tx.count() <= tx_threshold) return set_interrupts(R_TX_EMPTY);
        return true;
    }

    I2cHost host_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_I2C_HPP
