// The RP2040 ADC in C++ (docs/records/0096-cpp-mcu-core.md, Phase 4): a faithful translation of peripherals/_adc.py, which stays as the pure-Python reference and the oracle
// (tests/test_adc_diff.py).
//
// What it owns: the register file (CS, FCS, DIV, INTE, INTF, the last RESULT), the 4-entry conversion FIFO, the interrupt and DREQ logic, the round-robin channel selection, the
// free-running (START_MANY) schedule and the whole of a conversion's life: `start_adc_read()` marks the block busy and asks the device on the pins for a sample, and the answer
// arrives later (or at once, from inside the callback) as `complete_adc_read(value, error)`. It has two alarms of its own on the chip's clock (`Clock*`, as the DMA's): the sample
// alarm (the default device: `sample_time` microseconds after the request the analog value comes out of the host's `channel_values`) and the multi-shot alarm (the gap between two
// conversions of a free-running capture). What it does not own, reached through an `AdcHost` of plain function pointers (see core_host.hpp): the interrupt line, the DREQ, the
// device callback (`read`), the analog inputs (`channel_value`, which a host may fail on: the reference indexes a Python list) and the logger. With no device callback set the host
// completes a request through `default_adc_read()`.
//
// Failures follow the contract of core_host.hpp: a host call that fails makes the block return `false` at once, leaving the state the reference's exception would have left. A
// failure out of an alarm makes the alarm return `false` and the clock stops ticking.
//
// Quirks of the reference that are kept (each pinned by tests/test_adc_diff.py or the C++ checks):
//   - START_ONE is self-clearing (it never reads back from CS); START_MANY stays until written away;
//   - a CS write with ERR_STICKY set clears that bit of CS (write-clear); ERR follows the last conversion, STICKY stays until cleared that way;
//   - the active channel (CS.AINSEL, 3 bits) can hold 0-7 whatever `num_channels` is; a round-robin step stores the channel it found;
//   - every FIFO change and every FCS write re-publishes the DREQ (asserted while DREQ_EN is set and the level is at or above the threshold, deasserted otherwise); a FIFO read of an empty
//     FIFO sets UNDER and reads 0; a push on a full FIFO sets OVER and drops;
//   - `INTR` is raw level >= threshold (a threshold of 0 is always raised); `INTS` is (raw & enable) | force; INTE and INTF hold one bit;
//   - a free-running capture restarts at once when the divider does not exceed the sample time in 48 MHz ticks, else after the difference;
//   - `reset()` clears the registers, the FIFO and both alarms, republishes the DREQ and drops the line; the host's wiring (the device callback, the analog inputs) is not state;
//   - an unimplemented offset warns and reads 0xFFFFFFFF (a read above 0x1000 warns a second time); an unimplemented write warns.
// One deliberate difference: the reference's round-robin search loops for ever when the mask selects only channels the block does not have (num_channels lowered by hand); here
// the search gives up after a full turn and leaves the channel as it was. num_channels below 1 is treated as 1 (the reference raises ZeroDivisionError).
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract.
#ifndef RP2040PY_CORE_ADC_HPP
#define RP2040PY_CORE_ADC_HPP

#include <cstdint>

#include "clock.hpp"
#include "core_host.hpp"
#include "fifo.hpp"

namespace rp2040core {

using AdcIrqFn = bool (*)(void* ctx, bool level);                                  // false: failure parked
using AdcDreqFn = bool (*)(void* ctx, bool asserted);                              // false: failure parked
using AdcReadFn = bool (*)(void* ctx, uint32_t channel);                           // a conversion was requested; the host runs its device or `default_adc_read()`
using AdcChannelValueFn = bool (*)(void* ctx, uint32_t channel, int64_t* value);   // the analog value of a channel; false: failure parked (an invalid channel, say)
constexpr uint32_t kAdcWarnRead = kRegWarnRead, kAdcWarnReadAtomicArea = kRegWarnReadAtomicArea, kAdcWarnWrite = kRegWarnWrite;
using AdcWarnFn = RegWarnFn;

struct AdcHost {
    AdcIrqFn irq = nullptr;
    AdcDreqFn dreq = nullptr;
    AdcReadFn read = nullptr;
    AdcChannelValueFn channel_value = nullptr;
    AdcWarnFn warn = nullptr;
    void* ctx = nullptr;
    const int* failed = nullptr;  // the shared parked-failure flag (see _pending.pyx)
};

namespace adc_regs {
constexpr uint32_t CS = 0x00, RESULT = 0x04, FCS = 0x08, FIFO = 0x0C, DIV = 0x10, INTR = 0x14, INTE = 0x18, INTF = 0x1C, INTS = 0x20;
constexpr uint32_t CS_RROBIN_MASK = 0x1F, CS_RROBIN_SHIFT = 16, CS_AINSEL_MASK = 0x7, CS_AINSEL_SHIFT = 12;
constexpr uint32_t CS_ERR_STICKY = 1u << 10, CS_ERR = 1u << 9, CS_READY = 1u << 8, CS_START_MANY = 1u << 3, CS_START_ONE = 1u << 2, CS_TS_EN = 1u << 1, CS_EN = 1u << 0;
constexpr uint32_t CS_WRITE_MASK = (CS_RROBIN_MASK << CS_RROBIN_SHIFT) | (CS_AINSEL_MASK << CS_AINSEL_SHIFT) | CS_START_MANY | CS_START_ONE | CS_TS_EN | CS_EN;
constexpr uint32_t FCS_THRES_MASK = 0xF, FCS_THRESH_SHIFT = 24, FCS_LEVEL_MASK = 0xF, FCS_LEVEL_SHIFT = 16;
constexpr uint32_t FCS_OVER = 1u << 11, FCS_UNDER = 1u << 10, FCS_FULL = 1u << 9, FCS_EMPTY = 1u << 8, FCS_DREQ_EN = 1u << 3, FCS_ERR = 1u << 2, FCS_SHIFT = 1u << 1, FCS_EN = 1u << 0;
constexpr uint32_t FCS_WRITE_MASK = (FCS_THRES_MASK << FCS_THRESH_SHIFT) | FCS_DREQ_EN | FCS_ERR | FCS_SHIFT | FCS_EN;
constexpr uint32_t FIFO_ERR = 1u << 15;
constexpr uint32_t DIV_INT_MASK = 0xFFFF, DIV_INT_SHIFT = 8, DIV_FRAC_MASK = 0xFF, DIV_FRAC_SHIFT = 0;
constexpr uint32_t FIFO_INT = 1u << 0;
constexpr double kClockMhz = 48.0;  // the ADC's clock, for the free-running schedule
}  // namespace adc_regs

class AdcBlock {
public:
    static constexpr uint32_t kFifoDepth = 4;

    AdcBlock() = default;
    AdcBlock(const AdcBlock&) = delete;  // the alarms and the window handler hold pointers to this object
    AdcBlock& operator=(const AdcBlock&) = delete;

    // The registers and the machine, public for the shell (the reference's attributes are read and, by tests, written directly).
    uint32_t cs = 0, fcs = 0, clock_div = 0, int_enable = 0, int_force = 0;
    int64_t result = 0;
    bool busy = false, err = false;
    uint32_t current_channel = 0;
    int64_t num_channels = 5;
    double sample_time = 2;  // microseconds
    Fifo<kFifoDepth> fifo;
    Alarm sample_alarm, multi_shot_alarm;

    // Binds the block to its clock and host. Must be called once, before anything else.
    void init(Clock* clock, const AdcHost& host) noexcept {
        clock_ = clock;
        host_ = host;
        sample_alarm.fire = &AdcBlock::on_sample_alarm;
        sample_alarm.ctx = this;
        multi_shot_alarm.fire = &AdcBlock::on_multi_shot_alarm;
        multi_shot_alarm.ctx = this;
    }

    // Unlinks both alarms from the clock; the owner calls it before the block goes away.
    void detach() noexcept {
        if (clock_ == nullptr) return;
        clock_->cancel(&sample_alarm);
        clock_->cancel(&multi_shot_alarm);
    }

    Clock* clock() const noexcept { return clock_; }
    bool failed() const noexcept { return host_failed(host_.failed); }
    int64_t raw_write_value() const noexcept { return raw_write_value_; }

    // ---- derived ----------------------------------------------------------------------------------------------

    bool temperature_enable() const noexcept { return (cs & adc_regs::CS_TS_EN) != 0; }
    bool enabled() const noexcept { return (cs & adc_regs::CS_EN) != 0; }
    double divider() const noexcept {
        using namespace adc_regs;
        return 1.0 + static_cast<double>((clock_div >> DIV_INT_SHIFT) & DIV_INT_MASK) + static_cast<double>((clock_div >> DIV_FRAC_SHIFT) & DIV_FRAC_MASK) / 256.0;
    }
    uint32_t int_raw() const noexcept { return fifo.count() >= threshold() ? adc_regs::FIFO_INT : 0u; }
    uint32_t int_status() const noexcept { return (int_raw() & int_enable) | int_force; }
    uint32_t active_channel() const noexcept { return (cs >> adc_regs::CS_AINSEL_SHIFT) & adc_regs::CS_AINSEL_MASK; }
    void set_active_channel(int64_t channel) noexcept {
        using namespace adc_regs;
        cs &= ~(CS_AINSEL_MASK << CS_AINSEL_SHIFT);
        cs |= (static_cast<uint32_t>(channel) & CS_AINSEL_MASK) << CS_AINSEL_SHIFT;
    }

    // ---- the conversion ---------------------------------------------------------------------------------------

    // False: a failure is pending (the state is already what the reference's exception would have left).
    bool reset() noexcept {
        cs = fcs = clock_div = int_enable = int_force = 0;
        result = 0;
        busy = err = false;
        current_channel = 0;
        fifo.reset();
        clock_->cancel(&sample_alarm);
        clock_->cancel(&multi_shot_alarm);
        if (!update_dma()) return false;
        return host_.irq(host_.ctx, false);
    }

    bool check_interrupts() noexcept { return host_.irq(host_.ctx, int_status() != 0); }

    bool start_adc_read() noexcept {
        busy = true;
        return host_.read(host_.ctx, active_channel());
    }

    // The reference's own device: remember the channel and answer from `channel_values` after `sample_time` microseconds.
    void default_adc_read(uint32_t channel) noexcept {
        current_channel = channel;
        clock_->schedule(&sample_alarm, sample_time * 1000.0);
    }

    bool complete_adc_read(int64_t value, bool error) noexcept {
        using namespace adc_regs;
        busy = false;
        result = value & 0xFFF;  // RESULT is 12 bits
        if (error) {
            cs |= CS_ERR_STICKY | CS_ERR;
        } else {
            cs &= ~CS_ERR;
        }

        // FIFO
        if (fcs & FCS_EN) {
            if (fifo.full()) {
                fcs |= FCS_OVER;
            } else {
                value &= 0xFFF;  // 12 bits
                if (fcs & FCS_SHIFT) value >>= 4;
                if (error && (fcs & FCS_ERR)) value |= FIFO_ERR;
                fifo.push(static_cast<uint32_t>(value));
                if (!update_dma()) return false;
                if (!check_interrupts()) return false;
            }
        }

        // Round-robin
        const uint32_t round_mask = (cs >> CS_RROBIN_SHIFT) & CS_RROBIN_MASK;
        if (round_mask) {
            const int64_t channels = num_channels < 1 ? 1 : num_channels;
            int64_t channel = (static_cast<int64_t>(active_channel()) + 1) % channels;
            bool found = channel < 32 && (round_mask & (1u << channel));
            for (int64_t turns = 0; !found && turns < channels; ++turns) {
                channel = (channel + 1) % channels;
                found = channel < 32 && (round_mask & (1u << channel));
            }
            if (found) set_active_channel(channel);
        }

        // Multi-shot conversions
        if (cs & CS_START_MANY) {
            const double sample_ticks = kClockMhz * sample_time;
            const double div = divider();
            if (div > sample_ticks) {
                // the clock runs at 48 MHz: the capture restarts after the divider minus the sample time
                const double micros = (div - sample_ticks) / kClockMhz;
                clock_->schedule(&multi_shot_alarm, micros * 1000.0);
            } else {
                if (!start_adc_read()) return false;
            }
        }
        return true;
    }

    // ---- registers --------------------------------------------------------------------------------------------

    // Reads a register. A failure of a host call leaves the flag raised and the value already computed (the reference had raised after the same side effects).
    uint32_t read(uint32_t offset) noexcept {
        using namespace adc_regs;
        switch (offset) {
            case CS: return cs | (err ? CS_ERR : 0u) | (busy ? 0u : CS_READY);
            case RESULT: return static_cast<uint32_t>(result);
            case adc_regs::FCS:
                return fcs | ((fifo.count() & FCS_LEVEL_MASK) << FCS_LEVEL_SHIFT) | (fifo.full() ? FCS_FULL : 0u) | (fifo.empty() ? FCS_EMPTY : 0u);
            case adc_regs::FIFO: {
                if (fifo.empty()) {
                    fcs |= FCS_UNDER;
                    return 0;
                }
                const uint32_t value = fifo.pull();
                (void)update_dma();
                return value;
            }
            case DIV: return clock_div;
            case INTR: return int_raw();
            case INTE: return int_enable;
            case INTF: return int_force;
            case INTS: return int_status();
            default: break;
        }
        if (host_.warn) {
            host_.warn(host_.ctx, kAdcWarnRead, offset, 0);
            if (offset > 0x1000) host_.warn(host_.ctx, kAdcWarnReadAtomicArea, offset, 0);
        }
        return 0xFFFFFFFFu;
    }

    // `value` is the value after any atomic-alias decode. False: a failure is pending.
    bool write(uint32_t offset, int64_t value) noexcept {
        using namespace adc_regs;
        const uint32_t word = static_cast<uint32_t>(value);
        switch (offset) {
            case CS:
                cs &= ~(word & CS_ERR_STICKY);  // write-clear bit
                cs = (cs & ~CS_WRITE_MASK) | (word & CS_WRITE_MASK);
                cs &= ~CS_START_ONE;  // self-clearing
                if ((word & CS_EN) && !busy && ((word & CS_START_ONE) || (word & CS_START_MANY))) return start_adc_read();
                return true;
            case adc_regs::FCS:
                fcs &= ~(word & (FCS_OVER | FCS_UNDER));  // write-clear bits
                fcs = (fcs & ~FCS_WRITE_MASK) | (word & FCS_WRITE_MASK);
                if (!update_dma()) return false;  // DREQ_EN or the threshold may have changed
                return check_interrupts();
            case DIV: clock_div = word; return true;
            case INTE:
                int_enable = word & FIFO_INT;
                return check_interrupts();
            case INTF:
                int_force = word & FIFO_INT;
                return check_interrupts();
            default: break;
        }
        if (host_.warn) host_.warn(host_.ctx, kAdcWarnWrite, offset, value);
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

    WindowHandler window_handler() noexcept { return BlockWindow<AdcBlock>::handler(this); }

private:
    uint32_t threshold() const noexcept { return (fcs >> adc_regs::FCS_THRESH_SHIFT) & adc_regs::FCS_THRES_MASK; }

    // The DREQ is asserted while DREQ_EN is set and the level is at or above the threshold, and deasserted otherwise (also when DREQ_EN is switched off).
    bool update_dma() noexcept { return host_.dreq(host_.ctx, (fcs & adc_regs::FCS_DREQ_EN) != 0 && fifo.count() >= threshold()); }

    static bool on_sample_alarm(void* ctx) noexcept {
        AdcBlock* block = static_cast<AdcBlock*>(ctx);
        int64_t value = 0;
        if (!block->host_.channel_value(block->host_.ctx, block->current_channel, &value)) return false;
        return block->complete_adc_read(value, false);
    }

    static bool on_multi_shot_alarm(void* ctx) noexcept {
        AdcBlock* block = static_cast<AdcBlock*>(ctx);
        if (block->cs & adc_regs::CS_START_MANY) return block->start_adc_read();
        return true;
    }

    AdcHost host_;
    Clock* clock_ = nullptr;
    int64_t raw_write_value_ = 0;
};

}  // namespace rp2040core

#endif  // RP2040PY_CORE_ADC_HPP
