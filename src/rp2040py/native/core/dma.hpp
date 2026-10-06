// The RP2040 DMA controller in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of
// peripherals/_dma.py, which stays as the pure-Python reference and the oracle (tests/test_dma_diff.py).
//
// What it owns: the twelve channels (addresses, count, control word, the transfer in flight and its alarm on the
// C++ `Clock`), the raw/enable/force interrupt registers, the four pacing timers and the set of asserted DREQs. A
// transfer is one bus read and one bus write, so the block holds the C++ `Bus` directly - nothing about a transfer
// between RAM, a PIO FIFO or a UART needs Python. What it does not own: the two interrupt lines, `clk_sys` and the
// logger, reached through a `DmaHost` of plain function pointers.
//
// Failures. A host function (and any Python peripheral window a transfer touches) may fail; the failure is parked
// by the callee and `*host.failed` is raised. The DMA checks it after every bus access and host call and returns
// `false` at once, leaving exactly the state an exception would have left in the reference (a transfer that failed
// on its read has not written, nor advanced an address, nor counted).
//
// Re-entrancy. A transfer writes a peripheral, which may answer by changing a DREQ - or by writing this block's
// own registers - while `transfer()` is still on the stack. Nothing is cached across a bus access except what the
// reference also read before it (the control word, the data size and the ring mask at the start of a transfer).
//
// Quirks of the reference that are kept (each one is pinned by tests/test_dma_diff.py or the C++ checks):
//   - a CTRL rewrite while a channel runs re-arms its alarm, so if that was its last transfer the alarm runs one
//     more on the finished channel and takes the count to -1 (signed here, as there; the register reads back 0xFFFFFFFF);
//     a trigger with a count of 0 starts nothing (the reference used to leave such a channel BUSY for good);
//   - a reset leaves BUSY (and the sticky error bits) alone - only the low 24 bits of CTRL are rewritten;
//   - `set_dreq` wakes the channels waiting on a DREQ only on a rising edge, and a reset leaves the DREQs alone;
//   - TIMER3's dividend is `timer3 >> 4` (upstream rp2040js shifts by 36, which JavaScript masks to 4);
//   - the pacing timers' period is computed in doubles in the reference's order of operations.
// Deliberately not kept: a DREQ number of 64 or more (no peripheral has one - the largest is 39) is ignored,
// where the reference would store it in a dict; addresses wrap at 32 bits (the reference's ints grow).
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_DMA_HPP
#define RP2040PY_CORE_DMA_HPP

#include <cstdint>

#include "bus.hpp"
#include "clock.hpp"
#include "core_host.hpp"
#include "window_map.hpp"

namespace rp2040core {

using DmaIrqFn = bool (*)(void* ctx, uint32_t line, bool level);  // false: failure parked
using DmaClockFn = double (*)(void* ctx);                          // clk_sys in Hz; a failure is parked and flagged
// The messages the Python block logs through `BasePeripheral.warn`; the host formats them (it owns the logger).
constexpr uint32_t kDmaWarnRead = kRegWarnRead, kDmaWarnReadAtomicArea = kRegWarnReadAtomicArea, kDmaWarnWrite = kRegWarnWrite;
using DmaWarnFn = RegWarnFn;

struct DmaHost {
    DmaIrqFn irq = nullptr;
    DmaClockFn clk_sys = nullptr;
    DmaWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;       // the shared parked-failure flag (see _pending.pyx)
    uint32_t lines[2] = {11, 12};      // IRQ numbers of DMA_IRQ_0 / DMA_IRQ_1
};

namespace dma_regs {
// per-channel (offset & 0x83F)
constexpr uint32_t READ_ADDR = 0x000, WRITE_ADDR = 0x004, TRANS_COUNT = 0x008, CTRL_TRIG = 0x00C;
constexpr uint32_t AL1_CTRL = 0x010, AL1_READ_ADDR = 0x014, AL1_WRITE_ADDR = 0x018, AL1_TRANS_COUNT_TRIG = 0x01C;
constexpr uint32_t AL2_CTRL = 0x020, AL2_TRANS_COUNT = 0x024, AL2_READ_ADDR = 0x028, AL2_WRITE_ADDR_TRIG = 0x02C;
constexpr uint32_t AL3_CTRL = 0x030, AL3_WRITE_ADDR = 0x034, AL3_TRANS_COUNT = 0x038, AL3_READ_ADDR_TRIG = 0x03C;
constexpr uint32_t DBG_CTDREQ = 0x800, DBG_TCR = 0x804;
// controller
constexpr uint32_t INTR = 0x400, INTE0 = 0x404, INTF0 = 0x408, INTS0 = 0x40C, INTE1 = 0x414, INTF1 = 0x418, INTS1 = 0x41C;
constexpr uint32_t TIMER0 = 0x420, TIMER1 = 0x424, TIMER2 = 0x428, TIMER3 = 0x42C;
constexpr uint32_t MULTI_CHAN_TRIGGER = 0x430, FIFO_LEVELS = 0x440, CHAN_ABORT = 0x444, N_CHANNELS = 0x448;
// CTRL bits
constexpr uint32_t EN = 1u << 0, INCR_READ = 1u << 4, INCR_WRITE = 1u << 5, RING_SEL = 1u << 10;
constexpr uint32_t IRQ_QUIET = 1u << 21, BSWAP = 1u << 22, BUSY = 1u << 24;
constexpr uint32_t READ_ERROR = 1u << 30, WRITE_ERROR = 1u << 29;
constexpr uint32_t CTRL_WRITE_MASK = 0xFFFFFFu, CTRL_WC_MASK = READ_ERROR | WRITE_ERROR;
[[maybe_unused]] constexpr uint32_t TREQ_TIMER0 = 0x3B, TREQ_TIMER1 = 0x3C, TREQ_TIMER2 = 0x3D, TREQ_TIMER3 = 0x3E, TREQ_PERMANENT = 0x3F;
}  // namespace dma_regs

class DmaBlock;

class DmaChannel {
public:
    DmaChannel() = default;
    DmaChannel(const DmaChannel&) = delete;  // the alarm node points back at this object
    DmaChannel& operator=(const DmaChannel&) = delete;

    int index = 0;
    uint32_t ctrl = 0, read_addr = 0, write_addr = 0;
    int64_t trans_count = 0;
    uint32_t trans_count_reload = 0, dreq_counter = 0;
    uint32_t treq = 0, data_size = 1, chain_to = 0, ring_mask = 0;
    bool swap = false;  // BSWAP, which only 16/32-bit transfers honour
    Alarm alarm;

    bool active() const noexcept { return (ctrl & dma_regs::EN) != 0 && (ctrl & dma_regs::BUSY) != 0; }

    inline bool start() noexcept;
    inline bool schedule_transfer() noexcept;
    inline void abort() noexcept;
    inline uint32_t read(uint32_t offset) const noexcept;
    inline bool write(uint32_t offset, int64_t value) noexcept;
    inline bool reset() noexcept { return write(dma_regs::CTRL_TRIG, static_cast<int64_t>(index) << 11); }

private:
    friend class DmaBlock;
    inline bool transfer() noexcept;
    inline bool move_one() noexcept;
    static bool on_alarm(void* ctx) noexcept { return static_cast<DmaChannel*>(ctx)->transfer(); }
    DmaBlock* dma_ = nullptr;
};

class DmaBlock {
public:
    static constexpr int kChannels = 12;

    DmaBlock() = default;
    DmaBlock(const DmaBlock&) = delete;
    DmaBlock& operator=(const DmaBlock&) = delete;

    DmaChannel channels[kChannels];
    uint32_t int_raw = 0;

    // Binds the block to its bus, clock and host. Must be called once, before anything else.
    void init(Bus* bus, Clock* clock, const DmaHost& host) noexcept {
        bus_ = bus;
        clock_ = clock;
        host_ = host;
        for (int i = 0; i < kChannels; ++i) {
            DmaChannel& channel = channels[i];
            channel.dma_ = this;
            channel.index = i;
            channel.alarm.fire = &DmaChannel::on_alarm;
            channel.alarm.ctx = &channel;
            (void)channel.reset();  // the reference's channel constructor does this; it can reach no host function
        }
    }

    // Unlinks every alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept {
        for (int i = 0; i < kChannels; ++i) cancel_alarm(&channels[i].alarm);
    }

    Bus* bus() const noexcept { return bus_; }
    Clock* clock() const noexcept { return clock_; }
    bool failed() const noexcept { return host_.failed != nullptr && *host_.failed != 0; }

    uint32_t int_status0() const noexcept { return (int_raw & int_enable_[0]) | int_force_[0]; }
    uint32_t int_status1() const noexcept { return (int_raw & int_enable_[1]) | int_force_[1]; }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }
    uint32_t timer(int index) const noexcept { return timers_[index]; }
    uint64_t dreq_mask() const noexcept { return dreq_; }
    bool dreq(uint32_t number) const noexcept { return number < 64 && ((dreq_ >> number) & 1u) != 0; }

    // The period of a pacing timer in microseconds, or 0 when it is disabled; sets `*ok` false if reading clk_sys failed.
    double get_timer(uint32_t treq, bool* ok) noexcept {
        using namespace dma_regs;
        // "The pacing timer produces TREQ assertions at a rate set by ((X/Y) * sys_clk) ... can only generate TREQs at a rate of 1 per sys_clk (i.e. permanent TREQ) or less": the period is
        // Y/X sys_clk cycles, at least one. X is bits 31:16 of every one of the four timers; a timer with X or Y at 0 (reset: both) asserts nothing.
        double cycles;
        *ok = true;
        if (treq == TREQ_PERMANENT) {
            cycles = 1.0;
        } else if (treq >= TREQ_TIMER0 && treq <= TREQ_TIMER3) {
            const uint32_t value = timers_[treq - TREQ_TIMER0];
            const uint32_t dividend = value >> 16, divisor = value & 0xFFFFu;
            if (divisor == 0 || dividend == 0) return 0.0;
            cycles = static_cast<double>(divisor) / static_cast<double>(dividend);
            if (cycles < 1.0) cycles = 1.0;
        } else {
            // every non-timer TREQ is a period of 0 whatever clk_sys is, so the host is not asked: a channel stalled on a DREQ is rescheduled after every transfer, and that must not cross into Python.
            return 0.0;
        }
        const double clk = host_.clk_sys(host_.ctx);
        if (failed()) {
            *ok = false;
            return 0.0;
        }
        return (cycles * 1e6) / clk;
    }

    // A peripheral's DREQ line going active. Channels waiting on it are woken only on the rising edge.
    bool set_dreq(uint32_t number) noexcept {
        if (number >= 64) return true;
        if (dreq(number)) return true;
        dreq_ |= uint64_t{1} << number;
        for (int i = 0; i < kChannels; ++i) {
            DmaChannel& channel = channels[i];
            if (channel.treq == number && channel.active()) {
                if (!channel.schedule_transfer()) return false;
            }
        }
        return true;
    }
    void clear_dreq(uint32_t number) noexcept {
        if (number < 64) dreq_ &= ~(uint64_t{1} << number);
    }

    // Re-announces both lines, in order, whatever changed - the reference's call pattern.
    bool check_interrupts() noexcept {
        if (!host_.irq(host_.ctx, host_.lines[0], int_status0() != 0)) return false;
        return host_.irq(host_.ctx, host_.lines[1], int_status1() != 0);
    }

    // Channels back to power-on and the registers cleared; the asserted DREQs are deliberately untouched (see the
    // reference's reset()). Returns false if an interrupt-line call failed.
    bool reset() noexcept {
        for (int i = 0; i < kChannels; ++i) {
            clock_->cancel(&channels[i].alarm);
            if (!channels[i].reset()) return false;
        }
        int_raw = 0;
        int_enable_[0] = int_force_[0] = int_enable_[1] = int_force_[1] = 0;
        for (int i = 0; i < 4; ++i) timers_[i] = 0;
        return check_interrupts();
    }

    // ---- registers --------------------------------------------------------------------------------------

    // The offset is the window offset including the alias bits, as the reference sees it.
    uint32_t read(uint32_t offset) noexcept {
        using namespace dma_regs;
        if ((offset & 0x7FFu) < 0x300u) return channels[(offset & 0x7FFu) >> 6].read(offset & 0x83Fu);
        switch (offset) {
            case TIMER0: case TIMER1: case TIMER2: case TIMER3: return timers_[(offset - TIMER0) >> 2];
            case INTR: return int_raw;
            case INTE0: return int_enable_[0];
            case INTF0: return int_force_[0];
            case INTS0: return int_status0();
            case INTE1: return int_enable_[1];
            case INTF1: return int_force_[1];
            case INTS1: return int_status1();
            case N_CHANNELS: return kChannels;
            case MULTI_CHAN_TRIGGER: case CHAN_ABORT: case FIFO_LEVELS: return 0;  // self-clearing (an abort is flushed at once) / debug FIFO levels: see _dma.py
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kDmaWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kDmaWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode; the raw value is `raw_write_value()`. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace dma_regs;
        if ((offset & 0x7FFu) < 0x300u) return channels[(offset & 0x7FFu) >> 6].write(offset & 0x83Fu, value);
        switch (offset) {
            case TIMER0: case TIMER1: case TIMER2: case TIMER3:
                timers_[(offset - TIMER0) >> 2] = static_cast<uint32_t>(value);
                return true;
            case INTR: case INTS0: case INTS1:
                int_raw &= ~static_cast<uint32_t>(raw_write_value_);
                return check_interrupts();
            case INTE0: int_enable_[0] = static_cast<uint32_t>(value) & 0xFFFFu; return check_interrupts();
            case INTF0: int_force_[0] = static_cast<uint32_t>(value) & 0xFFFFu; return check_interrupts();
            case INTE1: int_enable_[1] = static_cast<uint32_t>(value) & 0xFFFFu; return check_interrupts();
            case INTF1: int_force_[1] = static_cast<uint32_t>(value) & 0xFFFFu; return check_interrupts();
            case MULTI_CHAN_TRIGGER:
                for (int i = 0; i < kChannels; ++i) {
                    if ((value >> i) & 1) {
                        if (!channels[i].start()) return false;
                    }
                }
                return true;
            case CHAN_ABORT:
                for (int i = 0; i < kChannels; ++i) {
                    if ((value >> i) & 1) channels[i].abort();
                }
                return true;
            default: break;
        }
        if (host_.warn) host_.warn(host_.ctx, kDmaWarnWrite, offset, value);
        return true;
    }

    // `BasePeripheral.write_uint32_atomic`, step for step - remember the raw value, decode the alias against a *read* of
    // the register (with that read's side effects), then write.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) {
            value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        }
        return write(offset, value);
    }

    // ---- window handler entry points: what the bus's C++ window registry calls ---------------------------

    WindowHandler window_handler() noexcept { return BlockWindow<DmaBlock>::handler(this); }

private:
    friend class DmaChannel;

    Bus* bus_ = nullptr;
    Clock* clock_ = nullptr;
    DmaHost host_;
    uint32_t int_enable_[2] = {0, 0}, int_force_[2] = {0, 0};
    uint32_t timers_[4] = {0, 0, 0, 0};
    uint64_t dreq_ = 0;
    int64_t raw_write_value_ = 0;
};

// ---- the channel ----------------------------------------------------------------------------------------

inline bool DmaChannel::start() noexcept {
    using namespace dma_regs;
    if (!(ctrl & EN) || (ctrl & BUSY)) return true;
    trans_count = trans_count_reload;
    if (trans_count == 0) return true;  // no transfers, no sequence: not BUSY, no interrupt, no chain (as rp2040-emu)
    ctrl |= BUSY;
    return schedule_transfer();
}

inline bool DmaChannel::schedule_transfer() noexcept {
    Clock* clock = dma_->clock_;
    if (dma_->dreq(treq) || treq == dma_regs::TREQ_PERMANENT) {
        clock->schedule(&alarm, 0.0);
        return true;
    }
    bool ok;
    const double delay = dma_->get_timer(treq, &ok);
    if (!ok) return false;
    if (delay != 0.0) clock->schedule(&alarm, delay * 1000.0);
    return true;
}

inline void DmaChannel::abort() noexcept {
    ctrl &= ~dma_regs::BUSY;
    dma_->clock_->cancel(&alarm);
}

// One read and one write of the channel's data size, through the bus. False when either access failed (the failure
// is pending and nothing after it has happened).
inline bool DmaChannel::move_one() noexcept {
    Bus* bus = dma_->bus_;
    switch (data_size) {
        case 2: {
            uint32_t value = bus->read16(read_addr);
            if (dma_->failed()) return false;
            if (swap) value = ((value & 0xFFu) << 8) | (value >> 8);
            bus->write16(write_addr, value);
            break;
        }
        case 4: {
            uint32_t value = bus->read32(read_addr);
            if (dma_->failed()) return false;
            if (swap) {
                value = ((value & 0x000000FFu) << 24) | ((value & 0x0000FF00u) << 8) | ((value & 0x00FF0000u) >> 8) | (value >> 24);
            }
            bus->write32(write_addr, value);
            break;
        }
        default: {
            const uint32_t value = bus->read8(read_addr);
            if (dma_->failed()) return false;
            bus->write8(write_addr, value);
            break;
        }
    }
    return !dma_->failed();
}

inline bool DmaChannel::transfer() noexcept {
    using namespace dma_regs;
    const uint32_t control = ctrl, size = data_size, ring = ring_mask;
    if (!move_one()) return false;
    if (control & INCR_READ) {
        if (ring != 0 && !(control & RING_SEL)) {
            read_addr = (read_addr & ~ring) | ((read_addr + size) & ring);
        } else {
            read_addr += size;
        }
    }
    if (control & INCR_WRITE) {
        if (ring != 0 && (control & RING_SEL)) {
            write_addr = (write_addr & ~ring) | ((write_addr + size) & ring);
        } else {
            write_addr += size;
        }
    }
    trans_count -= 1;
    if (trans_count > 0) return schedule_transfer();
    ctrl &= ~BUSY;
    if (!(ctrl & IRQ_QUIET)) {
        dma_->int_raw |= 1u << index;
        if (!dma_->check_interrupts()) return false;
    }
    if (chain_to != static_cast<uint32_t>(index) && chain_to < static_cast<uint32_t>(DmaBlock::kChannels)) {
        return dma_->channels[chain_to].start();
    }
    return true;
}

inline uint32_t DmaChannel::read(uint32_t offset) const noexcept {
    using namespace dma_regs;
    switch (offset) {
        case READ_ADDR: case AL1_READ_ADDR: case AL2_READ_ADDR: case AL3_READ_ADDR_TRIG: return read_addr;
        case WRITE_ADDR: case AL1_WRITE_ADDR: case AL2_WRITE_ADDR_TRIG: case AL3_WRITE_ADDR: return write_addr;
        case TRANS_COUNT: case AL1_TRANS_COUNT_TRIG: case AL2_TRANS_COUNT: case AL3_TRANS_COUNT:
            return static_cast<uint32_t>(trans_count);
        case CTRL_TRIG: case AL1_CTRL: case AL2_CTRL: case AL3_CTRL: return ctrl;
        case DBG_CTDREQ: return dreq_counter;
        case DBG_TCR: return trans_count_reload;
        default: return 0;
    }
}

inline bool DmaChannel::write(uint32_t offset, int64_t value) noexcept {
    using namespace dma_regs;
    bool trigger = false;
    switch (offset) {
        case READ_ADDR: case AL1_READ_ADDR: case AL2_READ_ADDR: case AL3_READ_ADDR_TRIG:
            read_addr = static_cast<uint32_t>(value);
            trigger = offset == AL3_READ_ADDR_TRIG;
            break;
        case WRITE_ADDR: case AL1_WRITE_ADDR: case AL2_WRITE_ADDR_TRIG: case AL3_WRITE_ADDR:
            write_addr = static_cast<uint32_t>(value);
            trigger = offset == AL2_WRITE_ADDR_TRIG;
            break;
        case TRANS_COUNT: case AL1_TRANS_COUNT_TRIG: case AL2_TRANS_COUNT: case AL3_TRANS_COUNT:
            trans_count_reload = static_cast<uint32_t>(value);
            trigger = offset == AL1_TRANS_COUNT_TRIG;
            break;
        case CTRL_TRIG: case AL1_CTRL: case AL2_CTRL: case AL3_CTRL: {
            ctrl = (ctrl & ~CTRL_WRITE_MASK) | (static_cast<uint32_t>(value) & CTRL_WRITE_MASK);
            ctrl &= ~(static_cast<uint32_t>(value) & CTRL_WC_MASK);  // write-clear bits
            treq = (ctrl >> 15) & 0x3Fu;
            chain_to = (ctrl >> 11) & 0xFu;
            const uint32_t ring_size = (ctrl >> 6) & 0xFu;
            ring_mask = ring_size != 0 ? (1u << ring_size) - 1u : 0u;
            const uint32_t size_sel = (ctrl >> 2) & 0x3u;
            swap = false;
            if (size_sel == 1) {
                data_size = 2;
                swap = (ctrl & BSWAP) != 0;
            } else if (size_sel == 2) {
                data_size = 4;
                swap = (ctrl & BSWAP) != 0;
            } else {
                data_size = 1;
            }
            if ((ctrl & EN) && (ctrl & BUSY)) {
                if (!schedule_transfer()) return false;
            }
            if (!(ctrl & EN)) dma_->clock_->cancel(&alarm);
            trigger = offset == CTRL_TRIG;
            break;
        }
        case DBG_CTDREQ:
            dreq_counter = 0;
            break;
        default: break;
    }
    if (trigger) {
        if (value != 0) return start();
        if (ctrl & IRQ_QUIET) {  // the null trigger interrupts
            dma_->int_raw |= 1u << index;
            return dma_->check_interrupts();
        }
    }
    return true;
}

}  // namespace rp2040core

#endif  // RP2040PY_CORE_DMA_HPP
