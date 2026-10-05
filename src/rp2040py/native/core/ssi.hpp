// The RP2040 SSI - the flash command path - in C++ (docs/records/0096-cpp-mcu-core.md, the flash-path design note and Phase 3's unfinished item): a faithful
// translation of peripherals/_ssi.py, which stays as the pure-Python reference and the oracle (tests/test_ssi_diff.py).
//
// What it is: the register file the bootrom's `flash_*` helpers and boot2 drive, plus a virtual SPI NOR flash behind it. A command is framed by the **QSPI_SS pin**
// (the Pico SDK bit-bangs chip-select through IO_QSPI, bypassing SSI's own SER/SSIENR): chip-select asserting starts a command (and empties the RX queue),
// every byte written to DR0 is shifted into the flash and its answer queued for DR0 reads, chip-select deasserting applies the command to the flash bytes. The flash
// is the caller's buffer (a pointer and a size, like every region of the C++ core); the pin is the caller's `GPIOPin`, listened to by a *direct listener*
// (core/pin.hpp), so a chip-select edge never enters Python.
//
// Bounded where the reference is not: the reference keeps the whole command in a growing bytearray and the RX bytes in an unbounded deque. What is used of the
// command is its first 4 bytes (opcode and address), at most 256 data bytes of a program, and the *number* of bytes shifted so far (a read or a JEDEC ID is indexed
// by position); the RX queue is a ring of `kRxCapacity` entries (the hardware FIFO is 16). Above the ring's capacity the OLDEST byte is dropped - undefined in the
// reference, defined here, and outside the oracle's domain.
//
// Quirks of the reference that are kept (each is pinned by tests/test_ssi_diff.py or the C++ checks):
//   - a DR0 write with SSIENR set and chip-select DEasserted still queues 0xFF (firmware's TXFLR/RXFLR flow control would spin forever otherwise); with SSIENR
//     clear it is ignored;
//   - an erase is applied only with the write-enable latch, aligned down to its sector/block, and clears the latch either way; a program ANDs the data into the
//     flash (NOR can only clear bits) for at most 256 data bytes; neither applies if the command is shorter than its address;
//   - READ_STATUS_2 always answers with QE set, WRITE_STATUS is accepted and ignored, any other opcode answers 0xFF;
//   - `reset()` clears the registers and the command but RE-SYNCS the chip-select flag from the pin (an always-output-enabled pad nothing drives resolves LOW, i.e.
//     already asserted; hard-coding "deasserted" once hung the bootrom) and never touches the listener.
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_SSI_HPP
#define RP2040PY_CORE_SSI_HPP

#include <cstdint>

#include "core_host.hpp"
#include "pin.hpp"
#include "window_map.hpp"

namespace rp2040core {

// The messages the Python block logs through `BasePeripheral.warn`; the host formats them (it owns the logger).
constexpr uint32_t kSsiWarnRead = kRegWarnRead, kSsiWarnReadAtomicArea = kRegWarnReadAtomicArea, kSsiWarnWrite = kRegWarnWrite;
using SsiWarnFn = RegWarnFn;
// Only when no native pin is bound (a Python chip-select): whether the pin reads LOW now. False: a failure is pending with the caller.
using SsiCsLowFn = bool (*)(void* ctx, bool* low);

struct SsiHost {
    SsiWarnFn warn = nullptr;
    SsiCsLowFn cs_low = nullptr;
    void* ctx = nullptr;
};

namespace ssi_regs {
constexpr uint32_t CTRLR0 = 0x00, CTRLR1 = 0x04, SSIENR = 0x08, BAUDR = 0x14, TXFLR = 0x20, RXFLR = 0x24, SR = 0x28;
constexpr uint32_t IDR = 0x58, VERSION_ID = 0x5C, DR0 = 0x60, RX_SAMPLE_DLY = 0xF0, SPI_CTRL_R0 = 0xF4, TXD_DRIVE_EDGE = 0xF8;
constexpr uint32_t SR_TFNF = 0x02, SR_TFE = 0x04, SR_RFNE = 0x08;
// JEDEC-standard SPI NOR commands the bootrom's flash helpers issue
constexpr uint8_t CMD_WRITE_ENABLE = 0x06, CMD_WRITE_DISABLE = 0x04, CMD_READ_STATUS_1 = 0x05, CMD_READ_STATUS_2 = 0x35, CMD_WRITE_STATUS = 0x01;
constexpr uint8_t CMD_PAGE_PROGRAM = 0x02, CMD_SECTOR_ERASE = 0x20, CMD_BLOCK_ERASE = 0xD8, CMD_READ_DATA = 0x03, CMD_READ_JEDEC_ID = 0x9F;
constexpr uint32_t FLASH_PAGE_SIZE = 256, FLASH_SECTOR_SIZE = 4096, FLASH_BLOCK_SIZE = 65536;
constexpr uint32_t STATUS_WEL = 0x02, STATUS2_QE = 0x02;
constexpr uint8_t JEDEC_ID[3] = {0xEF, 0x40, 0x15};
}  // namespace ssi_regs

class SsiBlock {
public:
    static constexpr uint32_t kRxCapacity = 4096;
    static constexpr uint32_t kTxKept = 4 + ssi_regs::FLASH_PAGE_SIZE;  // opcode + address + the data bytes of a page

    SsiBlock() = default;
    SsiBlock(const SsiBlock&) = delete;  // the pin holds a pointer to this object
    SsiBlock& operator=(const SsiBlock&) = delete;

    // The flash is the caller's buffer and must outlive the block.
    void init(uint8_t* flash, uint32_t flash_size, const SsiHost& host) noexcept {
        flash_ = flash;
        flash_size_ = flash_size;
        host_ = host;
    }

    // Binds the block to the chip-select pin (position 0 of its bank) and syncs the chip-select flag from the pin's level now. False if the listener table is
    // full or the level could not be read.
    bool bind_cs(PinBank* bank) noexcept {
        bank_ = bank;
        if (!bank_->add_direct_listener(0, &SsiBlock::cs_listener, this)) {
            bank_ = nullptr;
            return false;
        }
        return resync_cs();
    }

    // Unregisters from the pin; the owner calls it before the block (or the pin) goes away.
    void detach() noexcept {
        if (bank_ != nullptr) bank_->remove_direct_listener(0, &SsiBlock::cs_listener, this);
        bank_ = nullptr;
    }

    // The chip-select pin changed (what the direct listener calls; a Python chip-select calls it through the shell). LOW = the flash is selected.
    void on_cs_change(bool low) noexcept {
        if (low && !cs_asserted_) {
            tx_len_ = 0;  // a fresh command ...
            rx_head_ = rx_count_ = 0;  // ... and an empty RX queue
        } else if (cs_asserted_ && !low) {
            apply_command();
        }
        cs_asserted_ = low;
    }

    // Registers and the command back to power-on; the chip-select flag is read from the pin again. False: the level could not be read (the failure is pending).
    bool reset() noexcept {
        ctrlr0_ = ctrlr1_ = ssienr_ = baudr_ = txflr_ = spictrl0_ = rxsampdly_ = txddriveedge_ = 0;
        write_enabled_ = false;
        tx_len_ = 0;
        rx_head_ = rx_count_ = 0;
        return resync_cs();
    }

    // ---- state, for the shell's views --------------------------------------------------------------------------------
    uint32_t ssienr() const noexcept { return ssienr_; }
    uint32_t txflr() const noexcept { return txflr_; }
    bool write_enabled() const noexcept { return write_enabled_; }
    bool cs_asserted() const noexcept { return cs_asserted_; }
    uint32_t rx_count() const noexcept { return rx_count_; }
    uint8_t rx_at(uint32_t i) const noexcept { return rx_[(rx_head_ + i) % kRxCapacity]; }
    uint32_t tx_length() const noexcept { return tx_len_; }
    uint8_t tx_at(uint32_t i) const noexcept { return i < kTxKept ? tx_[i] : 0; }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }
    void set_write_enabled(bool value) noexcept { write_enabled_ = value; }
    void set_ssienr(uint32_t value) noexcept { ssienr_ = value; }
    void tx_append(uint8_t byte) noexcept { push_tx(byte); }
    void rx_push(uint8_t byte) noexcept { push_rx(byte); }

    // ---- registers --------------------------------------------------------------------------------------------------

    // The offset is the window offset including the alias bits, as the reference sees it. An unimplemented offset warns and reads as all ones.
    uint32_t read(uint32_t offset) noexcept {
        using namespace ssi_regs;
        switch (offset) {
            case TXFLR: return txflr_;
            case RXFLR: return rx_count_;
            case CTRLR0: return ctrlr0_;
            case CTRLR1: return ctrlr1_;
            case SSIENR: return ssienr_;
            case BAUDR: return baudr_;
            case SR: return SR_TFE | SR_TFNF | (rx_count_ != 0 ? SR_RFNE : 0u);
            case IDR: return 0x51535049u;
            case VERSION_ID: return 0x3430312Au;
            case RX_SAMPLE_DLY: return rxsampdly_;
            case TXD_DRIVE_EDGE: return txddriveedge_;
            case SPI_CTRL_R0: return spictrl0_;
            case DR0: return pop_rx();
            default: break;
        }
        if (host_.warn != nullptr) {
            host_.warn(host_.ctx, kSsiWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kSsiWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode.
    void write(uint32_t offset, int64_t value) noexcept {
        using namespace ssi_regs;
        const uint32_t word = static_cast<uint32_t>(value);
        switch (offset) {
            case TXFLR: txflr_ = word; return;
            case RXFLR: return;  // read-only FIFO level: a write is a no-op
            case CTRLR0: ctrlr0_ = word; return;
            case CTRLR1: ctrlr1_ = word; return;
            case SSIENR: ssienr_ = word; return;
            case BAUDR: baudr_ = word; return;
            case RX_SAMPLE_DLY: rxsampdly_ = word & 0xFFu; return;
            case TXD_DRIVE_EDGE: txddriveedge_ = word & 0xFFu; return;
            case SPI_CTRL_R0: spictrl0_ = word; return;
            case DR0:
                if (ssienr_ != 0) push_rx(cs_asserted_ ? shift_byte(static_cast<uint8_t>(word & 0xFFu)) : static_cast<uint8_t>(0xFF));
                return;
            default: break;
        }
        if (host_.warn != nullptr) host_.warn(host_.ctx, kSsiWarnWrite, offset, value);
    }

    // `BasePeripheral.write_uint32_atomic`, step for step - remember the raw value, decode the alias against a *read* of the register (with that read's side
    // effects: a DR0 alias pops the RX queue), then write.
    void write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) {
            value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        }
        write(offset, value);
    }

    // ---- window handler entry points: what the bus's C++ window registry calls ---------------------------

    WindowHandler window_handler() noexcept { return BlockWindow<SsiBlock>::handler(this); }

private:
    static bool cs_listener(void* ctx, uint32_t, int new_state, int) noexcept {
        static_cast<SsiBlock*>(ctx)->on_cs_change(new_state == kPinLow);  // active low
        return true;
    }

    bool resync_cs() noexcept {
        if (bank_ != nullptr) {
            int code = 0;
            if (!bank_->state_code(0, &code)) return false;
            cs_asserted_ = code == kPinLow;
        } else if (host_.cs_low != nullptr) {
            bool low = false;
            if (!host_.cs_low(host_.ctx, &low)) return false;
            cs_asserted_ = low;
        }
        return true;
    }

    void push_tx(uint8_t byte) noexcept {
        if (tx_len_ < kTxKept) tx_[tx_len_] = byte;
        ++tx_len_;
    }

    void push_rx(uint8_t byte) noexcept {
        if (rx_count_ == kRxCapacity) {  // undefined in the reference: the oldest goes
            rx_head_ = (rx_head_ + 1) % kRxCapacity;
            --rx_count_;
        }
        rx_[(rx_head_ + rx_count_) % kRxCapacity] = byte;
        ++rx_count_;
    }

    uint32_t pop_rx() noexcept {
        if (rx_count_ == 0) return 0;
        const uint8_t byte = rx_[rx_head_];
        rx_head_ = (rx_head_ + 1) % kRxCapacity;
        --rx_count_;
        return byte;
    }

    uint32_t address() const noexcept { return (static_cast<uint32_t>(tx_[1]) << 16) | (static_cast<uint32_t>(tx_[2]) << 8) | tx_[3]; }

    // One SPI clock's worth of full-duplex exchange: `byte_out` goes into the (virtual) flash chip, and what it shifts back is returned. Only write enable/disable
    // take effect at once; erase and program wait for chip-select to deassert, as a real flash chip applies them once the whole command is clocked in.
    uint8_t shift_byte(uint8_t byte_out) noexcept {
        using namespace ssi_regs;
        const uint32_t pos = tx_len_;
        push_tx(byte_out);
        const uint8_t opcode = tx_[0];
        if (pos == 0) {
            if (opcode == CMD_WRITE_ENABLE) {
                write_enabled_ = true;
            } else if (opcode == CMD_WRITE_DISABLE) {
                write_enabled_ = false;
            }
            return 0xFF;
        }
        if (opcode == CMD_READ_STATUS_1) return write_enabled_ ? STATUS_WEL : 0;
        if (opcode == CMD_READ_STATUS_2) return STATUS2_QE;
        if (opcode == CMD_WRITE_STATUS) return 0;
        if (opcode == CMD_READ_JEDEC_ID) {
            const uint32_t index = pos - 1;
            return index < 3 ? JEDEC_ID[index] : 0;
        }
        if (opcode == CMD_READ_DATA && pos >= 4) {
            const int64_t target = static_cast<int64_t>(address()) + (pos - 4);
            return (target >= 0 && target < static_cast<int64_t>(flash_size_)) ? flash_[target] : static_cast<uint8_t>(0xFF);
        }
        return 0xFF;  // an unrecognized opcode: the idle bus, the least likely value to look like a real but wrong answer
    }

    void apply_command() noexcept {
        using namespace ssi_regs;
        if (tx_len_ == 0) return;
        const uint8_t opcode = tx_[0];
        if ((opcode == CMD_SECTOR_ERASE || opcode == CMD_BLOCK_ERASE) && tx_len_ >= 4) {
            if (write_enabled_) {
                const uint32_t size = opcode == CMD_SECTOR_ERASE ? FLASH_SECTOR_SIZE : FLASH_BLOCK_SIZE;
                const uint32_t start = address() & ~(size - 1);
                if (static_cast<uint64_t>(start) + size <= flash_size_) {
                    for (uint32_t i = 0; i < size; ++i) flash_[start + i] = 0xFF;
                }
            }
            write_enabled_ = false;
        } else if (opcode == CMD_PAGE_PROGRAM && tx_len_ > 4) {
            if (write_enabled_) {
                const uint32_t start = address();
                const uint32_t length = tx_len_ - 4 < FLASH_PAGE_SIZE ? tx_len_ - 4 : FLASH_PAGE_SIZE;
                for (uint32_t i = 0; i < length; ++i) {
                    const uint64_t target = static_cast<uint64_t>(start) + i;
                    if (target < flash_size_) flash_[target] &= tx_[4 + i];  // NOR flash can only clear bits
                }
            }
            write_enabled_ = false;
        } else if (opcode == CMD_WRITE_STATUS) {
            write_enabled_ = false;
        }
        tx_len_ = 0;
    }

    uint8_t* flash_ = nullptr;
    uint32_t flash_size_ = 0;
    SsiHost host_;
    PinBank* bank_ = nullptr;
    uint32_t ctrlr0_ = 0, ctrlr1_ = 0, ssienr_ = 0, baudr_ = 0, txflr_ = 0, spictrl0_ = 0, rxsampdly_ = 0, txddriveedge_ = 0;
    bool write_enabled_ = false;
    bool cs_asserted_ = false;
    uint8_t tx_[kTxKept] = {};
    uint32_t tx_len_ = 0;
    uint8_t rx_[kRxCapacity] = {};
    uint32_t rx_head_ = 0, rx_count_ = 0;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_SSI_HPP
