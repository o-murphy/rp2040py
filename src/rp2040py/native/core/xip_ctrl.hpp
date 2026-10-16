// XIP_CTRL and the XIP subsystem around it (RP2040 datasheet 2.6.3) in C++ (docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md "XIP_CTRL and the XIP subsystem"): a
// faithful translation of `peripherals/_xip_ctrl.py`, which stays as the pure-Python reference and the oracle (tests/test_xip_diff.py). The reference's docstring carries the sources and says
// what is a model choice rather than datasheet.
//
// What it owns: the registers (CTRL, FLUSH, STAT, the two counters, STREAM_ADDR/CTR/FIFO), the cache as TAGS only (1024 sets x 2 ways, 8-byte lines, least-recently-used replacement; the
// data always comes from the flash image), the cache's data RAM used as SRAM, and the flash stream with its 2-entry FIFO, its alarm on the chip's `Clock` and `DREQ_XIP_STREAM`.
// What it does not own, reached through an `XipHost`: the logger (a bus fault the model cannot raise is reported as data), the DMA request line, and the two chip numbers the model's timing
// derives from (clk_sys, and SSI.BAUDR for the stream's pace). The flash image is the chip's own buffer, read-only here.
//
// The bus reaches the block two ways: its 16 KiB register window (`window_handler()`), and `XipHooks` for the flash window itself - the memory map calls `read_access` / `write_access` for
// every access to an XIP alias and `sram_read` / `sram_write` for the `0x15...` segment (see core/memory_map.hpp). The auxiliary slave XIP_AUX is a second window onto `pop_stream_fifo`.
//
// Failures: a host call that fails parks its error with the host (the shared flag); the block stops what it was doing at once and the Cython caller re-raises.
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_XIP_CTRL_HPP
#define RP2040PY_CORE_XIP_CTRL_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"
#include "memory_map.hpp"
#include "window_map.hpp"

namespace rp2040core {

// What the host is told. The register message is the shared one (`kRegWarnWrite`: an unimplemented register write, offset and value); the three below it are about the data path - the XIP's
// memory windows, not its registers - and carry an `address`, so they start after the shared range.
inline constexpr uint32_t kXipWarnWrite = kRegWarnWrite;
inline constexpr uint32_t kXipWarnBadWrite = 3;   // a write to a non-caching alias with ERR_BADWRITE set (a bus fault); `address`
inline constexpr uint32_t kXipWarnSramRead = 4;   // a read of the cache-as-SRAM while the cache is enabled or powered down; `address`
inline constexpr uint32_t kXipWarnSramWrite = 5;  // the same for a write; `address`

using XipRateFn = bool (*)(void* ctx, double* value);       // clk_sys in Hz / SSI.BAUDR (as read); false: failure parked

struct XipHost {
    XipRateFn clk_sys = nullptr;
    XipRateFn baudr = nullptr;
    void* ctx = nullptr;
};

namespace xip_regs {
constexpr uint32_t REG_CTRL = 0x00, REG_FLUSH = 0x04, REG_STAT = 0x08, REG_CTR_HIT = 0x0C, REG_CTR_ACC = 0x10, REG_STREAM_ADDR = 0x14, REG_STREAM_CTR = 0x18, REG_STREAM_FIFO = 0x1C;
constexpr uint32_t CTRL_EN = 1u << 0, CTRL_ERR_BADWRITE = 1u << 1, CTRL_POWER_DOWN = 1u << 3;
constexpr uint32_t CTRL_MASK = CTRL_EN | CTRL_ERR_BADWRITE | CTRL_POWER_DOWN, CTRL_RESET = CTRL_EN | CTRL_ERR_BADWRITE;
constexpr uint32_t STAT_FLUSH_READY = 1u << 0, STAT_FIFO_EMPTY = 1u << 1, STAT_FIFO_FULL = 1u << 2;
constexpr uint32_t LINE_SHIFT = 3, SETS = 1024, SET_MASK = SETS - 1, FLASH_MASK = 0x00FFFFFFu;
constexpr int32_t INVALID = -1;
constexpr double FLUSH_CYCLES = 1026.0;
constexpr uint32_t SRAM_SIZE = 16 * 1024, FIFO_DEPTH = 2, STREAM_CTR_MASK = 0x3FFFFFu;
constexpr double STREAM_WORD_CLOCKS = 32.0;
constexpr uint32_t COUNTER_MAX = 0xFFFFFFFFu;
constexpr uint32_t ALIAS_ALLOCATING = 0, ALIAS_NOCACHE = 2, ALIAS_BYPASS = 3;  // 0x10... allocating, 0x12... non-cacheable, 0x13... bypass; 0x11... (non-allocating) is "none of those"
}  // namespace xip_regs

class XipBlock {
public:
    XipBlock() = default;
    XipBlock(const XipBlock&) = delete;  // the alarm node, the window handler and the hooks hold a pointer to this object
    XipBlock& operator=(const XipBlock&) = delete;

    // Binds the block to its clock, the chip's flash buffer and its host. Once, before anything else. Does not reset (the owner calls reset() when the chip is complete).
    void init(Clock* clock, const uint8_t* flash, uint32_t flash_size, const ChipPorts* ports, uint32_t block, uint32_t dreq, const XipHost& host) noexcept {
        clock_ = clock;
        flash_ = flash;
        flash_size_ = flash_size;
        port_.init(ports, block, 0, dreq);
        host_ = host;
        stream_alarm_.fire = &XipBlock::on_stream_alarm;
        stream_alarm_.ctx = this;
        clock_->add_rebase_listener(&rebase_listener_, &XipBlock::on_rebase, this);  // the clock's origin moved: the flush deadline is an absolute tick count
    }

    // Unlinks the alarm from the clock; the owner calls it before the block goes away.
    void detach() noexcept {
        Clock::remove_rebase_listener(&rebase_listener_);
        cancel_alarm(&stream_alarm_);
    }

    bool failed() const noexcept { return port_.failed(); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // The registers and state, public for the shell (the reference's attributes are read and, by tests, written directly).
    uint32_t ctrl = xip_regs::CTRL_RESET, ctr_hit = 0, ctr_acc = 0, stream_addr = 0, stream_ctr = 0;
    uint32_t fifo[xip_regs::FIFO_DEPTH] = {0, 0};
    uint32_t fifo_len = 0;
    bool stream_in_flight = false;
    int64_t flush_done_ticks = 0;
    int16_t tags[xip_regs::SETS][2];
    uint8_t recent[xip_regs::SETS];
    uint8_t sram[xip_regs::SRAM_SIZE] = {};  // the cache's data RAM: it keeps its contents through a flush and a reset

    // The XIP domain's reset: registers to power-on, the stream stopped and its FIFO emptied, the cache flushed. The data RAM keeps its contents. False: a failure is pending.
    bool reset() noexcept {
        using namespace xip_regs;
        cancel_alarm(&stream_alarm_);
        ctrl = CTRL_RESET;
        ctr_hit = ctr_acc = 0;
        stream_addr = stream_ctr = 0;
        fifo_len = 0;
        stream_in_flight = false;
        for (uint32_t i = 0; i < SETS; ++i) recent[i] = 0;
        if (!start_flush()) return false;
        return update_dreq();
    }

    bool flush_ready() const noexcept { return clock_->ticks() >= flush_done_ticks; }

    // One read of an XIP alias: counted, looked up and (the alias allowing it) allocated.
    void access(uint32_t address) noexcept {
        using namespace xip_regs;
        if (ctr_acc < COUNTER_MAX) ++ctr_acc;
        const uint32_t alias = (address >> 24) & 3u;
        if (!(ctrl & CTRL_EN) || alias == ALIAS_BYPASS) return;
        const uint32_t line = (address & FLASH_MASK) >> LINE_SHIFT;
        const uint32_t index = line & SET_MASK;
        const int16_t tag = static_cast<int16_t>(line >> 10);
        int16_t* ways = tags[index];
        const int way = ways[0] == tag ? 0 : ways[1] == tag ? 1 : -1;
        if (alias == ALIAS_NOCACHE) {  // "Don't check for hit, always update cache"
            recent[index] = static_cast<uint8_t>(way < 0 ? allocate(index, tag) : way);
            return;
        }
        if (way >= 0) {
            if (ctr_hit < COUNTER_MAX) ++ctr_hit;
            recent[index] = static_cast<uint8_t>(way);
        } else if (alias == ALIAS_ALLOCATING) {
            allocate(index, tag);
        }
    }

    // One write to an XIP alias: the caching, allocating alias deletes a matching entry; any other is a bus fault with ERR_BADWRITE set (reported) and ignored without it.
    void write_access(uint32_t address) noexcept {
        using namespace xip_regs;
        const uint32_t alias = (address >> 24) & 3u;
        if (alias == ALIAS_ALLOCATING) {
            const uint32_t line = (address & FLASH_MASK) >> LINE_SHIFT;
            int16_t* ways = tags[line & SET_MASK];
            const int16_t tag = static_cast<int16_t>(line >> 10);
            if (ways[0] == tag) ways[0] = INVALID;
            if (ways[1] == tag) ways[1] = INVALID;
        } else if (ctrl & CTRL_ERR_BADWRITE) {
            port_.warn(kXipWarnBadWrite, address, 0);
        }
    }

    bool sram_available() const noexcept { return !(ctrl & (xip_regs::CTRL_EN | xip_regs::CTRL_POWER_DOWN)); }

    // The word at `address` of the `0x15...` segment; 0xFFFFFFFF (and a report) while the cache is enabled or powered down - a bus error the model cannot raise.
    uint32_t sram_read(uint32_t address) noexcept {
        if (!sram_available()) {
            port_.warn(kXipWarnSramRead, address, 0);
            return 0xFFFFFFFFu;
        }
        const uint32_t offset = address & (xip_regs::SRAM_SIZE - 1) & ~3u;
        return static_cast<uint32_t>(sram[offset]) | static_cast<uint32_t>(sram[offset + 1]) << 8 | static_cast<uint32_t>(sram[offset + 2]) << 16 | static_cast<uint32_t>(sram[offset + 3]) << 24;
    }

    void sram_write(uint32_t address, uint32_t value) noexcept {
        if (!sram_available()) {
            port_.warn(kXipWarnSramWrite, address, 0);
            return;
        }
        const uint32_t offset = address & (xip_regs::SRAM_SIZE - 1) & ~3u;
        sram[offset] = static_cast<uint8_t>(value);
        sram[offset + 1] = static_cast<uint8_t>(value >> 8);
        sram[offset + 2] = static_cast<uint8_t>(value >> 16);
        sram[offset + 3] = static_cast<uint8_t>(value >> 24);
    }

    // `STREAM_FIFO` and `XIP_AUX`: the oldest streamed word (0 if there is none); room in the FIFO lets the stream go on.
    uint32_t pop_stream_fifo() noexcept {
        uint32_t word = 0;
        if (fifo_len > 0) {
            word = fifo[0];
            fifo[0] = fifo[1];
            --fifo_len;
        }
        if (update_dreq()) maybe_start_stream();
        return word;
    }

    uint32_t stat() const noexcept {
        using namespace xip_regs;
        return (flush_ready() ? STAT_FLUSH_READY : 0u) | (fifo_len == 0 ? STAT_FIFO_EMPTY : 0u) | (fifo_len >= FIFO_DEPTH ? STAT_FIFO_FULL : 0u);
    }

    uint32_t read(uint32_t offset) noexcept {
        using namespace xip_regs;
        switch (offset) {
            case REG_CTRL: return ctrl;
            case REG_FLUSH:
                // "Reading will hold the bus (stall the processor) until the flush completes": the model has no stall, so the flush completes now. SC: the bit reads 0.
                if (clock_->ticks() < flush_done_ticks) flush_done_ticks = clock_->ticks();
                return 0;
            case REG_STAT: return stat();
            case REG_CTR_HIT: return ctr_hit;
            case REG_CTR_ACC: return ctr_acc;
            case REG_STREAM_ADDR: return stream_addr;
            case REG_STREAM_CTR: return stream_ctr;
            case REG_STREAM_FIFO: return pop_stream_fifo();
            default: return 0;  // not BasePeripheral's 0xFFFFFFFF: a real boot writes past the register block and polls what it finds there
        }
    }

    // False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace xip_regs;
        const uint32_t v = static_cast<uint32_t>(value);
        switch (offset) {
            case REG_CTRL:
                ctrl = v & CTRL_MASK;
                if (ctrl & CTRL_POWER_DOWN) ctrl &= ~CTRL_EN;  // "Writing 1 to this bit forces CTRL_EN to 0"
                return true;
            case REG_FLUSH:
                return (v & 1u) ? start_flush() : true;
            case REG_CTR_HIT: ctr_hit = 0; return true;  // WC: any value clears
            case REG_CTR_ACC: ctr_acc = 0; return true;
            case REG_STREAM_ADDR: stream_addr = v & 0xFFFFFFFCu; return true;
            case REG_STREAM_CTR:
                stream_ctr = v & STREAM_CTR_MASK;
                if (stream_ctr == 0) {  // "Write 0 to halt an in-progress stream, and discard any in-flight read"
                    cancel_alarm(&stream_alarm_);
                    stream_in_flight = false;
                    return true;
                }
                return maybe_start_stream();
            case REG_STAT:
            case REG_STREAM_FIFO: return true;  // read only
            default: break;
        }
        if (offset > REG_STREAM_FIFO) return true;  // cache maintenance past the register block: nothing to do
        port_.unimplemented_write(offset, value);
        return !failed();
    }

    // `BasePeripheral.write_uint32_atomic`, step for step.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value_ = raw;
        int64_t value = raw;
        if (atomic_type != kAtomicNormal) {
            value = decode_atomic(atomic_type, static_cast<int64_t>(read(offset)), raw);
        }
        if (failed()) return false;
        return write(offset, value);
    }

    WindowHandler window_handler() noexcept { return BlockWindow<XipBlock>::handler(this); }

    // XIP_AUX: a read-only slave onto the stream FIFO.
    WindowHandler aux_window_handler() noexcept { return WindowHandler{&XipBlock::aux_read32, &XipBlock::aux_write32, this}; }

    // What the memory map calls for the flash window and the `0x15...` segment.
    XipHooks hooks() noexcept { return XipHooks{&XipBlock::hook_access, &XipBlock::hook_write_access, &XipBlock::hook_sram_read, &XipBlock::hook_sram_write, this}; }

private:
    static void on_rebase(void* ctx, int64_t delta) noexcept {
        auto* self = static_cast<XipBlock*>(ctx);
        self->flush_done_ticks = self->flush_done_ticks > delta ? self->flush_done_ticks - delta : 0;  // a deadline long past is just "done": it must not drift negative without bound
    }
    static bool on_stream_alarm(void* ctx) noexcept {
        XipBlock* self = static_cast<XipBlock*>(ctx);
        self->on_stream_word();
        return !self->failed();
    }
    static uint32_t aux_read32(void* ctx, uint32_t) { return static_cast<XipBlock*>(ctx)->pop_stream_fifo(); }
    static void aux_write32(void*, uint32_t, int64_t, uint32_t) {}
    static void hook_access(void* ctx, uint32_t address) { static_cast<XipBlock*>(ctx)->access(address); }
    static void hook_write_access(void* ctx, uint32_t address) { static_cast<XipBlock*>(ctx)->write_access(address); }
    static uint32_t hook_sram_read(void* ctx, uint32_t address) { return static_cast<XipBlock*>(ctx)->sram_read(address); }
    static void hook_sram_write(void* ctx, uint32_t address, uint32_t value) { static_cast<XipBlock*>(ctx)->sram_write(address, value); }

    // Clears the tags and starts the flush clock. False: a failure is pending.
    bool start_flush() noexcept {
        using namespace xip_regs;
        for (uint32_t i = 0; i < SETS; ++i) tags[i][0] = tags[i][1] = static_cast<int16_t>(INVALID);
        double hz = 0.0;
        if (!host_.clk_sys(host_.ctx, &hz)) return false;
        flush_done_ticks = clock_->ticks() + nanos_to_ticks(FLUSH_CYCLES * 1e9 / hz);
        return true;
    }

    int allocate(uint32_t index, int16_t tag) noexcept {
        int16_t* ways = tags[index];
        const int way = ways[0] == xip_regs::INVALID ? 0 : ways[1] == xip_regs::INVALID ? 1 : 1 - recent[index];
        ways[way] = tag;
        recent[index] = static_cast<uint8_t>(way);
        return way;
    }

    // False: a failure is pending.
    bool update_dreq() noexcept { return port_.dreq(0, fifo_len > 0); }

    bool maybe_start_stream() noexcept {
        using namespace xip_regs;
        if (stream_ctr != 0 && !stream_in_flight && fifo_len < FIFO_DEPTH) {
            double hz = 0.0, sckdv = 0.0;
            if (!host_.clk_sys(host_.ctx, &hz) || !host_.baudr(host_.ctx, &sckdv)) return false;
            if (sckdv < 2.0) sckdv = 2.0;
            stream_in_flight = true;
            clock_->schedule(&stream_alarm_, sckdv * STREAM_WORD_CLOCKS * 1e9 / hz);
        }
        return true;
    }

    void on_stream_word() noexcept {
        using namespace xip_regs;
        stream_in_flight = false;
        if (stream_ctr == 0) return;
        const uint32_t offset = stream_addr & FLASH_MASK;
        uint32_t word = 0;
        for (uint32_t i = 0; i < 4; ++i) {  // bytes past the end of the image read as nothing, as a short slice does in the reference
            if (offset + i < flash_size_) word |= static_cast<uint32_t>(flash_[offset + i]) << (8 * i);
        }
        fifo[fifo_len++] = word;
        stream_addr += 4;
        --stream_ctr;
        if (update_dreq()) maybe_start_stream();
    }

    BlockPort port_;
    XipHost host_;
    Clock* clock_ = nullptr;
    const uint8_t* flash_ = nullptr;
    uint32_t flash_size_ = 0;
    Alarm stream_alarm_;
    RebaseListener rebase_listener_;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_XIP_CTRL_HPP
