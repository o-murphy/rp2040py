// The RP2040 PIO in C++ (docs/records/0096-cpp-mcu-core.md, Phase 3): the four state machines and the block around them, a faithful translation of
// native/_pio.pyx + native/_state_machine.pyx - themselves transcriptions of peripherals/_pio.py + _state_machine.py, which stay as the pure-Python reference.
// The judge is the lockstep differential of tests/utils/pio_diff.py (pure Python vs this), then the existing tests and the real workloads.
//
// What it owns: instruction memory, `irq`, the FDEBUG/stall words, the pin images (`pin_values`/`pin_directions`) the machines write, the IRQ0/IRQ1 enable and
// force words, the pacing of record 0063 (`cycle_fp`, `next_due_fp`, `backlog_drops`), and for each machine x, y, pc, the two shift registers, the three config
// words, the divider, the wait state, the pending EXEC opcode and its two 4-entry FIFOs. What it does not own: the IRQ lines, the DMA's DREQs, the logger and the
// "no Simulator owns the chip" fallback (all reached through `PioHost` function pointers - Python trampolines today), and the pins (a `PinBank*` per GPIO, or a host
// callback for a pin that is not a C++ one).
//
// Every observable of the Cython implementation is kept, in the same order - which register access warns and with what, the DREQ and interrupt calls a FIFO or
// instruction causes, what `check_wait()` does to `next_due_fp` - because the differential compares all of them after every step. Where the Cython (and Python)
// leave something undefined, this defines it: a `SIDESET_COUNT` above 5 (not a hardware configuration; the pure code raises, the Cython shifts by a platform-dependent
// amount) is treated as 5; a chain of EXEC instructions each executing the next is cut after kMaxExecChain (the recursion in the Python would overflow its stack).
//
// Header-only, C++17, no exceptions/RTTI/STL, no allocation. Single-threaded by contract. Every function that can call out returns `false` when the host reported
// failure (a Python trampoline can raise): the failure is pending with the caller, and whatever was running stops at once - as an exception would stop it.
#ifndef RP2040PY_CORE_PIO_HPP
#define RP2040PY_CORE_PIO_HPP

#include <cstdint>

#include "core_host.hpp"
#include "pin.hpp"
#include "window_map.hpp"

namespace rp2040core {

namespace pio_regs {
constexpr uint32_t CTRL = 0x000, FSTAT = 0x004, FDEBUG = 0x008, FLEVEL = 0x00C;
constexpr uint32_t TXF0 = 0x010, RXF0 = 0x020;
constexpr uint32_t IRQ = 0x030, IRQ_FORCE = 0x034, INPUT_SYNC_BYPASS = 0x038, DBG_PADOUT = 0x03C, DBG_PADOE = 0x040, DBG_CFGINFO = 0x044;
constexpr uint32_t INSTR_MEM0 = 0x48, INSTR_MEM31 = 0x0C4;
constexpr uint32_t SM0_CLKDIV = 0x0C8, SM0_EXECCTRL = 0x0CC, SM0_SHIFTCTRL = 0x0D0, SM0_ADDR = 0x0D4, SM0_INSTR = 0x0D8, SM0_PINCTRL = 0x0DC;
constexpr uint32_t SM_STRIDE = 0x18;  // SM1_CLKDIV - SM0_CLKDIV
constexpr uint32_t INTR = 0x128, IRQ0_INTE = 0x12C, IRQ0_INTF = 0x130, IRQ0_INTS = 0x134, IRQ1_INTE = 0x138, IRQ1_INTF = 0x13C, IRQ1_INTS = 0x140;
}  // namespace pio_regs

enum PioWait : uint32_t { kPioWaitNone = 0, kPioWaitPin = 1, kPioWaitRxFifo = 2, kPioWaitTxFifo = 3, kPioWaitIrq = 4, kPioWaitOut = 5 };

// What the block reports to its host for logging (the host formats the message and picks warning vs error).
enum PioLog : uint32_t {
    kPioWarnRead = 0,            // "Unimplemented peripheral read from 0x{a:x}"
    kPioWarnReadAtomicArea = 1,  // "Unimplemented read from peripheral in the atomic operation region"
    kPioWarnWrite = 2,           // "Unimplemented peripheral write to 0x{a:x}: 0x{b:x}"
    kPioErrorSmRead = 3,         // error: "Read from invalid state machine register: {a}"
    kPioErrorSmWrite = 4,        // error: "Write to invalid state machine register: {a}"
};

constexpr uint32_t kPioMachines = 4;
constexpr uint32_t kPioGpio = 30;  // pins the block can see (len(rp2040.gpio))
constexpr int64_t kPioNeverDue = int64_t{1} << 62;
constexpr int64_t kPioMaxArrearsFp = int64_t{8} << 8;
constexpr uint32_t kPioMaxExecChain = 1024;

using PioIrqFn = bool (*)(void* ctx, uint32_t line, bool level);        // rp2040.set_interrupt
using PioDreqFn = bool (*)(void* ctx, uint32_t channel, bool set);       // dma.set_dreq / clear_dreq
using PioLogFn = void (*)(void* ctx, uint32_t kind, uint32_t a, uint32_t b);
using PioPinUpdateFn = bool (*)(void* ctx, uint32_t pin);                // a pin that is not a C++ one: gpio[pin].check_for_updates()
using PioPinInputFn = bool (*)(void* ctx, uint32_t pin, bool* level);    // ... and its input_value
using PioStartedFn = bool (*)(void* ctx);                                // CTRL started a stopped block: the host runs the "no owning Simulator" fallback

struct PioHost {
    PioIrqFn set_irq = nullptr;
    PioDreqFn dreq = nullptr;
    PioLogFn log = nullptr;
    PioPinUpdateFn pin_update = nullptr;
    PioPinInputFn pin_input = nullptr;
    PioStartedFn started = nullptr;
    void* ctx = nullptr;
};

#define RP2040PY_PIO_TRY(expr) \
    do {                     \
        if (!(expr)) return false; \
    } while (0)

// `utils/fifo.FIFO(4)` exactly: a ring whose start index survives reset() (reset clears only the count), push masks to 32 bits and is ignored when full,
// pull on an empty FIFO returns 0.
struct PioFifo {
    static constexpr uint32_t kSize = 4;
    uint32_t buffer[kSize] = {0, 0, 0, 0};
    uint32_t start = 0;
    uint32_t used = 0;

    bool empty() const noexcept { return used == 0; }
    bool full() const noexcept { return used == kSize; }
    void push(uint32_t value) noexcept {
        if (used < kSize) {
            buffer[(start + used) % kSize] = value;
            ++used;
        }
    }
    uint32_t pull() noexcept {
        if (used == 0) return 0;
        const uint32_t value = buffer[start];
        start = (start + 1) % kSize;
        --used;
        return value;
    }
    uint32_t peek() const noexcept { return used != 0 ? buffer[start] : 0; }
    void reset() noexcept { used = 0; }
};

class PioBlock;

struct PioMachine {
    PioBlock* block = nullptr;
    uint32_t index = 0;
    bool enabled = false;

    uint32_t x = 0, y = 0, pc = 0;
    uint32_t input_shift_reg = 0, input_shift_count = 0, output_shift_reg = 0, output_shift_count = 32;  // reset: the OSR is empty (datasheet 3.5.4)
    int64_t cycles = 0;  // signed: check_wait() can add a still-unresolved wait_delay of -1
    uint32_t exec_opcode = 0;
    bool exec_valid = false;
    bool update_pc = true;
    uint32_t clock_div_int = 1, clock_div_frac = 0;
    int64_t div_fp = int64_t{1} << 8;
    int64_t next_due_fp = 0;
    bool due_rearmed = false;
    uint32_t exec_ctrl = 0x1Fu << 12, shift_ctrl = 0b11u << 18, pin_ctrl = 0x5u << 26;
    PioFifo rx, tx;
    uint32_t out_pin_values = 0, out_pin_direction = 0;
    bool waiting = false;
    uint32_t wait_type = kPioWaitNone;
    uint32_t wait_index = 0;
    bool wait_polarity = false;
    int32_t wait_delay = -1;
    uint32_t dreq_rx = 0, dreq_tx = 0;

    // --- the machine's own fields, as the Python properties derive them ---
    uint32_t push_threshold() const noexcept { const uint32_t v = (shift_ctrl >> 20) & 0x1F; return v ? v : 32; }
    uint32_t pull_threshold() const noexcept { const uint32_t v = (shift_ctrl >> 25) & 0x1F; return v ? v : 32; }
    uint32_t sideset_count() const noexcept { return (pin_ctrl >> 29) & 0x7; }
    uint32_t set_count() const noexcept { return (pin_ctrl >> 26) & 0x7; }
    uint32_t out_count() const noexcept { return (pin_ctrl >> 20) & 0x3F; }
    uint32_t in_base() const noexcept { return (pin_ctrl >> 15) & 0x1F; }
    uint32_t sideset_base() const noexcept { return (pin_ctrl >> 10) & 0x1F; }
    uint32_t set_base() const noexcept { return (pin_ctrl >> 5) & 0x1F; }
    uint32_t out_base() const noexcept { return pin_ctrl & 0x1F; }
    uint32_t jmp_pin() const noexcept { return (exec_ctrl >> 24) & 0x1F; }
    uint32_t wrap_top() const noexcept { return (exec_ctrl >> 12) & 0x1F; }
    uint32_t wrap_bottom() const noexcept { return (exec_ctrl >> 7) & 0x1F; }
    uint32_t status() const noexcept {
        const uint32_t n = exec_ctrl & 0xF;
        return (exec_ctrl & (1u << 4)) ? (rx.used < n ? 0xFFFFFFFFu : 0u) : (tx.used < n ? 0xFFFFFFFFu : 0u);
    }
    uint32_t fifo_stat() const noexcept {
        const uint32_t result = (tx.empty() ? 1u << 24 : 0u) | (tx.full() ? 1u << 16 : 0u) | (rx.empty() ? 1u << 8 : 0u) | (rx.full() ? 1u : 0u);
        return result << index;
    }

    // --- behaviour (defined after PioBlock, which they use) ---
    inline bool update_dma_tx() noexcept;
    inline bool update_dma_rx() noexcept;
    inline bool write_fifo(uint32_t value) noexcept;
    inline bool read_fifo(uint32_t* out) noexcept;
    inline bool execute_instruction(uint32_t opcode) noexcept;
    inline bool step() noexcept;
    inline bool check_wait() noexcept;
    inline bool restart() noexcept;
    inline void clk_div_restart() noexcept;
    inline bool reset() noexcept;
    inline bool read32(uint32_t offset, uint32_t* out) noexcept;
    inline bool write32(uint32_t offset, uint32_t value) noexcept;

private:
    inline bool jmp_condition(uint32_t condition, bool* out) noexcept;
    inline bool in_source_value(uint32_t source, uint32_t* out) noexcept;
    inline bool in_pins(uint32_t* out) noexcept;
    inline bool write_out_value(uint32_t destination, uint32_t value, uint32_t bit_count) noexcept;
    inline bool out_instruction(uint32_t arg) noexcept;
    inline bool set_mov_destination(uint32_t destination, uint32_t value) noexcept;
    inline bool execute_one(uint32_t opcode) noexcept;
    inline void wait(uint32_t type, bool polarity, uint32_t index) noexcept;
    inline void next_pc() noexcept;
    inline bool set_sideset(uint32_t value, uint32_t count) noexcept;
};

class PioBlock {
public:
    PioBlock() = default;
    PioBlock(const PioBlock&) = delete;  // the machines point at their block
    PioBlock& operator=(const PioBlock&) = delete;

    // `first_irq` is the line of IRQ0 (IRQ1 is the next); `index` is 0 or 1 (PIO0/PIO1), which fixes the DREQ channels. Publishes each machine's FIFO state to the DMA, as the machines'
    // constructors do (RX then TX, machine by machine).
    bool init(const PioHost& host, uint32_t first_irq, uint32_t index) noexcept {
        host_ = host;
        first_irq_ = first_irq;
        index_ = index;
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            PioMachine& m = machines[i];
            m = PioMachine();
            m.block = this;
            m.index = i;
            m.dreq_tx = index * 8 + i;
            m.dreq_rx = index * 8 + 4 + i;
            RP2040PY_PIO_TRY(m.update_dma_rx());
            RP2040PY_PIO_TRY(m.update_dma_tx());
        }
        for (uint32_t i = 0; i < 32; ++i) instructions[i] = 0;
        return true;
    }

    // A pin the block reads and updates directly (a C++ pin bank); a pin left unbound goes through the host.
    void bind_pin(uint32_t gpio, PinBank* bank) noexcept {
        if (gpio < kPioGpio) pins_[gpio] = bank;
    }

    const PioHost& host() const noexcept { return host_; }
    uint32_t first_irq() const noexcept { return first_irq_; }
    uint32_t index() const noexcept { return index_; }

    // ---- state the rest of the chip (and the Python views) read and write ----
    PioMachine machines[kPioMachines];
    uint32_t instructions[32] = {};
    uint32_t irq = 0;
    uint32_t fdebug = 0, tx_stall = 0, rx_stall = 0;
    uint32_t input_sync_bypass = 0;
    uint32_t pin_values = 0, pin_directions = 0, old_pin_values = 0, old_pin_directions = 0;
    uint32_t irq0_int_enable = 0, irq0_int_force = 0, irq1_int_enable = 0, irq1_int_force = 0;
    int32_t stopped = 1;  // int, not bool: the batch loop reads it through a pointer (core/batch.hpp)
    int64_t cycle_fp = 0;
    int64_t next_due_fp = kPioNeverDue;
    int64_t backlog_drops = 0;
    int64_t raw_write_value = 0;

    uint32_t int_raw() const noexcept {
        uint32_t result = (irq & 0xF) << 8;
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            if (!machines[i].tx.full()) result |= 0x10u << i;
            if (!machines[i].rx.empty()) result |= 0x01u << i;
        }
        return result;
    }
    uint32_t irq0_int_status() const noexcept { return (int_raw() & irq0_int_enable) | irq0_int_force; }
    uint32_t irq1_int_status() const noexcept { return (int_raw() & irq1_int_enable) | irq1_int_force; }

    bool check_interrupts() noexcept {
        RP2040PY_PIO_TRY(host_.set_irq == nullptr || host_.set_irq(host_.ctx, first_irq_, irq0_int_status() != 0));
        return host_.set_irq == nullptr || host_.set_irq(host_.ctx, first_irq_ + 1, irq1_int_status() != 0);
    }

    bool irq_updated() noexcept {
        for (uint32_t i = 0; i < kPioMachines; ++i) RP2040PY_PIO_TRY(machines[i].check_wait());
        return check_interrupts();
    }

    // ---- the pins ----
    bool read_pin(uint32_t gpio, bool* level) noexcept {
        if (gpio >= kPioGpio) {
            *level = false;
            return true;
        }
        if (pins_[gpio] != nullptr) {
            *level = pins_[gpio]->input_value(0);
            return true;
        }
        *level = false;
        return host_.pin_input == nullptr || host_.pin_input(host_.ctx, gpio, level);
    }

    // `rp2040.gpio_values`: one bit per GPIO, the effective input level.
    bool gpio_values(uint32_t* out) noexcept {
        uint32_t result = 0;
        for (uint32_t i = 0; i < kPioGpio; ++i) {
            bool level = false;
            RP2040PY_PIO_TRY(read_pin(i, &level));
            if (level) result |= 1u << i;
        }
        *out = result;
        return true;
    }

    // "The least-significant bit of OUT data is mapped to PINCTRL_OUT_BASE, and this mapping continues for PINCTRL_OUT_COUNT bits, wrapping after GPIO31" (datasheet 3.5.6): the data and
    // the mask are rotated left by `first_pin` within 32 bits, not shifted.
    static uint32_t pin_write(uint32_t old, uint32_t value, uint32_t first_pin, uint32_t count) noexcept {
        uint32_t mask = count > 31 ? 0xFFFFFFFFu : ((1u << count) - 1u);
        if (first_pin != 0) {
            value = (value << first_pin) | (value >> (32 - first_pin));
            mask = (mask << first_pin) | (mask >> (32 - first_pin));
        }
        return ((old & ~mask) | (value & mask)) & 0x3FFFFFFFu;
    }
    void pin_values_changed(uint32_t value, uint32_t first_pin, uint32_t count) noexcept { pin_values = pin_write(pin_values, value, first_pin, count); }
    void pin_directions_changed(uint32_t value, uint32_t first_pin, uint32_t count) noexcept { pin_directions = pin_write(pin_directions, value, first_pin, count); }

    // The set bits of what changed since the last call, each pin told to re-announce itself.
    bool check_changed_pins() noexcept {
        const uint32_t changed = (old_pin_directions ^ pin_directions) | (old_pin_values ^ pin_values);
        if (changed == 0) return true;
        old_pin_directions = pin_directions;
        old_pin_values = pin_values;
        uint32_t remaining = changed;
        while (remaining != 0) {
            uint32_t gpio = 0;
            while (((remaining >> gpio) & 1u) == 0) ++gpio;
            if (gpio < kPioGpio) {
                if (pins_[gpio] != nullptr) {
                    RP2040PY_PIO_TRY(pins_[gpio]->check_for_updates(0));
                } else if (host_.pin_update != nullptr) {
                    RP2040PY_PIO_TRY(host_.pin_update(host_.ctx, gpio));
                }
            }
            remaining &= remaining - 1;
        }
        return true;
    }

    // ---- pacing (record 0063) ----
    void recompute_due() noexcept {
        int64_t next = kPioNeverDue;
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            const PioMachine& m = machines[i];
            if (m.enabled && !m.waiting && m.next_due_fp < next) next = m.next_due_fp;
        }
        next_due_fp = next;
    }
    void notify_due(int64_t due_fp) noexcept {
        if (due_fp < next_due_fp) next_due_fp = due_fp;
    }

    // One call per CPU instruction (or idle jump) from the batch loop: at most one instruction per machine.
    bool advance(int64_t cycles) noexcept {
        cycle_fp += cycles << 8;
        if (cycle_fp < next_due_fp) return true;
        return run_due();
    }
    bool step() noexcept { return advance(1); }

    bool run_due() noexcept {
        const int64_t now = cycle_fp;
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            PioMachine& m = machines[i];
            if (m.enabled && !m.waiting && m.next_due_fp <= now) {
                if (now - m.next_due_fp > kPioMaxArrearsFp) {
                    ++backlog_drops;
                    m.next_due_fp = now;
                }
                RP2040PY_PIO_TRY(m.step());
            }
        }
        recompute_due();
        return check_changed_pins();
    }

    // ---- the register file ----
    uint32_t read(uint32_t offset) noexcept {  // an unimplemented offset warns and reads as 0xFFFFFFFF; a failure leaves the result 0xFFFFFFFF too (pending with the caller)
        uint32_t value = 0xFFFFFFFFu;
        if (!read32(offset, &value)) return 0xFFFFFFFFu;
        return value;
    }

    bool read32(uint32_t offset, uint32_t* out) noexcept {
        using namespace pio_regs;
        for (uint32_t m = 0; m < kPioMachines; ++m) {
            const uint32_t first = SM0_CLKDIV + m * SM_STRIDE;
            if (offset >= first && offset <= first + (SM0_PINCTRL - SM0_CLKDIV)) return machines[m].read32(offset - first, out);
        }
        switch (offset) {
            case CTRL: {
                uint32_t result = 0;
                for (uint32_t m = 0; m < kPioMachines; ++m)
                    if (machines[m].enabled) result |= 1u << m;
                *out = result;
                return true;
            }
            case FSTAT: {
                uint32_t result = 0;
                for (uint32_t m = 0; m < kPioMachines; ++m) result |= machines[m].fifo_stat();
                *out = result;
                return true;
            }
            case FDEBUG: *out = fdebug; return true;
            case FLEVEL: {
                uint32_t result = 0;
                for (uint32_t m = 0; m < kPioMachines; ++m)
                    result |= ((machines[m].tx.used & 0xFu) << (8 * m)) | ((machines[m].rx.used & 0xFu) << (8 * m + 4));
                *out = result;
                return true;
            }
            case RXF0: case RXF0 + 4: case RXF0 + 8: case RXF0 + 12: return machines[(offset - RXF0) / 4].read_fifo(out);
            case IRQ: *out = irq; return true;
            case IRQ_FORCE: *out = 0; return true;
            case INPUT_SYNC_BYPASS: *out = input_sync_bypass; return true;
            case DBG_PADOUT: *out = pin_values; return true;
            case DBG_PADOE: *out = pin_directions; return true;
            case DBG_CFGINFO: *out = 0x200404; return true;
            case INTR: *out = int_raw(); return true;
            case IRQ0_INTE: *out = irq0_int_enable; return true;
            case IRQ0_INTF: *out = irq0_int_force; return true;
            case IRQ0_INTS: *out = irq0_int_status(); return true;
            case IRQ1_INTE: *out = irq1_int_enable; return true;
            case IRQ1_INTF: *out = irq1_int_force; return true;
            case IRQ1_INTS: *out = irq1_int_status(); return true;
            default: break;
        }
        if (host_.log != nullptr) {
            host_.log(host_.ctx, kPioWarnRead, offset, 0);
            if (offset > 0x1000) host_.log(host_.ctx, kPioWarnReadAtomicArea, offset, 0);
        }
        *out = 0xFFFFFFFFu;
        return true;
    }

    // `write_uint32`: `value` is the value after any alias decode; the raw value the guest wrote is `raw_write_value`.
    bool write32(uint32_t offset, uint32_t value) noexcept {
        using namespace pio_regs;
        if (offset >= INSTR_MEM0 && offset <= INSTR_MEM31) {
            instructions[(offset - INSTR_MEM0) >> 2] = value & 0xFFFF;
            return true;
        }
        for (uint32_t m = 0; m < kPioMachines; ++m) {
            const uint32_t first = SM0_CLKDIV + m * SM_STRIDE;
            if (offset >= first && offset <= first + (SM0_PINCTRL - SM0_CLKDIV)) return machines[m].write32(offset - first, value);
        }
        switch (offset) {
            case CTRL: return write_ctrl(value);
            case FDEBUG:
                fdebug &= ~static_cast<uint32_t>(raw_write_value);
                fdebug |= tx_stall | rx_stall;
                return true;
            case TXF0: case TXF0 + 4: case TXF0 + 8: case TXF0 + 12: return machines[(offset - TXF0) / 4].write_fifo(value);
            case IRQ:
                irq &= ~static_cast<uint32_t>(raw_write_value);
                return irq_updated();
            case INPUT_SYNC_BYPASS: input_sync_bypass = value; return true;
            case IRQ_FORCE:
                irq |= value;
                return irq_updated();
            case IRQ0_INTE: irq0_int_enable = value & 0xFFF; return check_interrupts();
            case IRQ0_INTF: irq0_int_force = value & 0xFFF; return check_interrupts();
            case IRQ1_INTE: irq1_int_enable = value & 0xFFF; return check_interrupts();
            case IRQ1_INTF: irq1_int_force = value & 0xFFF; return check_interrupts();
            default: break;
        }
        if (host_.log != nullptr) host_.log(host_.ctx, kPioWarnWrite, offset, value);
        return true;
    }

    // A write through the atomic aliases: `write_uint32_atomic` step for step - remember the raw value, decode the alias against a *read* of the register (with that
    // read's side effects), then write.
    bool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept {
        raw_write_value = raw;
        uint32_t value = static_cast<uint32_t>(raw);
        if (atomic_type != kAtomicNormal) {
            uint32_t current = 0;
            RP2040PY_PIO_TRY(read32(offset, &current));
            value = static_cast<uint32_t>(decode_atomic(atomic_type, static_cast<int64_t>(current), static_cast<int64_t>(static_cast<uint32_t>(raw))));
        }
        return write32(offset, value);
    }

    // The whole block back to power-on (0089 Phase 5): instruction memory, the four machines, the IRQ flags, the pacing state. `raw_write_value` is not state.
    bool reset() noexcept {
        for (uint32_t i = 0; i < kPioMachines; ++i) RP2040PY_PIO_TRY(machines[i].reset());
        for (uint32_t i = 0; i < 32; ++i) instructions[i] = 0;
        stopped = 1;
        cycle_fp = 0;
        next_due_fp = kPioNeverDue;
        backlog_drops = 0;
        fdebug = tx_stall = rx_stall = 0;
        input_sync_bypass = 0;
        irq = 0;
        pin_values = pin_directions = old_pin_values = old_pin_directions = 0;
        irq0_int_enable = irq0_int_force = irq1_int_enable = irq1_int_force = 0;
        return check_interrupts();
    }

    void stop() noexcept {
        for (uint32_t i = 0; i < kPioMachines; ++i) machines[i].enabled = false;
        stopped = 1;
    }

    // ---- window handler entry points: what the bus's C++ window registry calls ----
    WindowHandler window_handler() noexcept { return BlockWindow<PioBlock>::handler(this); }

    // Used by the machines.
    bool update_pin(uint32_t gpio) noexcept {
        if (gpio >= kPioGpio) return true;
        if (pins_[gpio] != nullptr) return pins_[gpio]->check_for_updates(0);
        return host_.pin_update == nullptr || host_.pin_update(host_.ctx, gpio);
    }

private:
    bool write_ctrl(uint32_t value) noexcept {
        for (uint32_t m = 0; m < kPioMachines; ++m) {
            PioMachine& machine = machines[m];
            const bool enable = (value & (1u << m)) != 0;
            // Starts running now, not at a due time left behind when it was disabled.
            if (enable && !machine.enabled) machine.next_due_fp = cycle_fp;
            machine.enabled = enable;
            if (value & (1u << (4 + m))) RP2040PY_PIO_TRY(machine.restart());
            if (value & (1u << (8 + m))) machine.clk_div_restart();
        }
        recompute_due();
        const uint32_t should_run = value & 0xF;
        if (stopped != 0 && should_run != 0) {
            stopped = 0;
            RP2040PY_PIO_TRY(host_.started == nullptr || host_.started(host_.ctx));
        }
        if (should_run == 0) stopped = 1;
        return true;
    }

    PioHost host_;
    uint32_t first_irq_ = 0;
    uint32_t index_ = 0;
    PinBank* pins_[kPioGpio] = {};
};

// ======================================================================================================================================
// The machine

inline bool PioMachine::update_dma_tx() noexcept {
    const PioHost& h = block->host();
    return h.dreq == nullptr || h.dreq(h.ctx, dreq_tx, !tx.full());
}
inline bool PioMachine::update_dma_rx() noexcept {
    const PioHost& h = block->host();
    return h.dreq == nullptr || h.dreq(h.ctx, dreq_rx, !rx.empty());
}

inline bool PioMachine::write_fifo(uint32_t value) noexcept {
    if (tx.full()) {
        block->fdebug |= (1u << 16) << index;  // TXOVER
        return true;
    }
    tx.push(value);
    block->tx_stall &= ~((1u << 24) << index);
    RP2040PY_PIO_TRY(update_dma_tx());
    RP2040PY_PIO_TRY(check_wait());
    if (tx.full()) RP2040PY_PIO_TRY(block->check_interrupts());
    return true;
}

inline bool PioMachine::read_fifo(uint32_t* out) noexcept {
    if (rx.empty()) {
        block->fdebug |= (1u << 8) << index;  // RXUNDER
        *out = 0;
        return true;
    }
    const uint32_t result = rx.pull();
    block->rx_stall &= ~(1u << index);
    RP2040PY_PIO_TRY(update_dma_rx());
    RP2040PY_PIO_TRY(check_wait());
    if (rx.empty()) RP2040PY_PIO_TRY(block->check_interrupts());
    *out = result;
    return true;
}

inline bool PioMachine::jmp_condition(uint32_t condition, bool* out) noexcept {
    switch (condition) {
        case 0b000: *out = true; return true;
        case 0b001: *out = x == 0; return true;
        case 0b010: {  // X--: non-zero, post-decrement
            const uint32_t old = x;
            x = x - 1u;
            *out = old != 0;
            return true;
        }
        case 0b011: *out = y == 0; return true;
        case 0b100: {  // Y--
            const uint32_t old = y;
            y = y - 1u;
            *out = old != 0;
            return true;
        }
        case 0b101: *out = x != y; return true;
        case 0b110: {  // PIN
            bool level = false;
            RP2040PY_PIO_TRY(block->read_pin(jmp_pin(), &level));
            *out = level;
            return true;
        }
        default: *out = output_shift_count < pull_threshold(); return true;  // !OSRE
    }
}

inline bool PioMachine::in_pins(uint32_t* out) noexcept {
    uint32_t values = 0;
    RP2040PY_PIO_TRY(block->gpio_values(&values));
    const uint32_t base = in_base();
    // A 32-bit rotate-right by in_base (real hardware reads GPIOs starting at in_base).
    *out = base != 0 ? ((values << (32 - base)) | (values >> base)) : values;
    return true;
}

inline bool PioMachine::in_source_value(uint32_t source, uint32_t* out) noexcept {
    switch (source) {
        case 0b000: return in_pins(out);
        case 0b001: *out = x; return true;
        case 0b010: *out = y; return true;
        case 0b011: *out = 0; return true;
        case 0b100: *out = 0; return true;  // reserved
        case 0b101: *out = status(); return true;
        case 0b110: *out = input_shift_reg; return true;
        default: *out = output_shift_reg; return true;
    }
}

inline bool PioMachine::write_out_value(uint32_t destination, uint32_t value, uint32_t bit_count) noexcept {
    switch (destination) {
        case 0b000:  // PINS
            out_pin_values = value;
            block->pin_values_changed(value, out_base(), out_count());
            break;
        case 0b001: x = value; break;
        case 0b010: y = value; break;
        case 0b011: break;  // NULL
        case 0b100:         // PINDIRS
            out_pin_direction = value;
            block->pin_directions_changed(value, out_base(), out_count());
            break;
        case 0b101:  // PC
            pc = value & 0x1F;
            update_pc = false;
            break;
        case 0b110:  // ISR, and the ISR shift counter becomes the bit count
            input_shift_reg = value;
            input_shift_count = bit_count;
            break;
        default:  // EXEC: execute the OSR data as an instruction
            exec_opcode = value;
            exec_valid = true;
            break;
    }
    return true;
}

inline bool PioMachine::out_instruction(uint32_t arg) noexcept {
    const uint32_t bit_count = arg & 0x1F;
    const uint32_t destination = arg >> 5;
    if (bit_count == 0) {
        RP2040PY_PIO_TRY(write_out_value(destination, output_shift_reg, 32));
        output_shift_reg = 0;  // all 32 bits were shifted out, and the OSR shifts in zeroes
        output_shift_count = 32;
        return true;
    }
    uint32_t value;
    if (shift_ctrl & (1u << 19)) {  // OUT_SHIFTDIR: shift right
        value = output_shift_reg & ((1u << bit_count) - 1u);
        output_shift_reg = output_shift_reg >> bit_count;
    } else {
        value = output_shift_reg >> (32 - bit_count);
        output_shift_reg = output_shift_reg << bit_count;
    }
    RP2040PY_PIO_TRY(write_out_value(destination, value, bit_count));
    output_shift_count += bit_count;
    if (output_shift_count > 32) output_shift_count = 32;
    return true;
}

inline void PioMachine::wait(uint32_t type, bool polarity, uint32_t index_) noexcept {
    waiting = true;
    wait_type = type;
    wait_polarity = polarity;
    wait_index = index_;
    wait_delay = -1;
    update_pc = false;
}

inline void PioMachine::next_pc() noexcept {
    pc = pc == wrap_top() ? wrap_bottom() : ((pc + 1) & 0x1F);
}

inline bool PioMachine::set_sideset(uint32_t value, uint32_t count) noexcept {
    if (exec_ctrl & (1u << 29)) block->pin_directions_changed(value, sideset_base(), count);
    else block->pin_values_changed(value, sideset_base(), count);
    return true;
}

inline bool PioMachine::set_mov_destination(uint32_t destination, uint32_t value) noexcept {
    switch (destination) {
        case 0b000:
            out_pin_values = value;
            block->pin_values_changed(value, out_base(), out_count());
            break;
        case 0b001: x = value; break;
        case 0b010: y = value; break;
        case 0b011: break;
        case 0b100:
            exec_opcode = value;
            exec_valid = true;
            break;
        case 0b101:
            pc = value & 0x1F;
            update_pc = false;
            break;
        case 0b110:  // ISR: the input shift counter is reset to 0 (empty)
            input_shift_reg = value;
            input_shift_count = 0;
            break;
        default:  // OSR: the output shift counter is reset to 0 (full)
            output_shift_reg = value;
            output_shift_count = 0;
            break;
    }
    return true;
}

// The body of one instruction up to (not including) the cycle count, delay and side-set bookkeeping.
inline bool PioMachine::execute_one(uint32_t opcode) noexcept {
    const uint32_t arg = opcode & 0xFF;
    const uint32_t instruction = opcode >> 13;
    PioBlock& pio = *block;
    switch (instruction) {
        case 0b000: {  // JMP
            bool taken = false;
            RP2040PY_PIO_TRY(jmp_condition(arg >> 5, &taken));
            if (taken) {
                pc = arg & 0x1F;
                update_pc = false;
            }
            break;
        }
        case 0b001: {  // WAIT
            const bool polarity = (arg & 0x80) != 0;
            const uint32_t source = (arg >> 5) & 0x3;
            const uint32_t idx = arg & 0x1F;
            if (source == 0b00) wait(kPioWaitPin, polarity, idx);
            else if (source == 0b01) wait(kPioWaitPin, polarity, (idx + in_base()) % 32);
            else if (source == 0b10) {
                uint32_t irq_idx = (idx & 0x10) ? ((idx & 0x4) | (((idx & 0x3) + index) & 0x3)) : (idx & 0x7);
                wait(kPioWaitIrq, polarity, irq_idx);
            }
            break;
        }
        case 0b010: {  // IN
            const uint32_t bit_count = arg & 0x1F;
            uint32_t source_value = 0;
            RP2040PY_PIO_TRY(in_source_value(arg >> 5, &source_value));
            if (bit_count == 0) {
                input_shift_reg = source_value;
                input_shift_count = 32;
            } else {
                source_value &= (1u << bit_count) - 1u;
                if (shift_ctrl & (1u << 18)) {  // IN_SHIFTDIR: shift right, data enters from the left
                    input_shift_reg = input_shift_reg >> bit_count;
                    input_shift_reg |= source_value << (32 - bit_count);
                } else {
                    input_shift_reg = input_shift_reg << bit_count;
                    input_shift_reg |= source_value;
                }
                input_shift_count += bit_count;
                if (input_shift_count > 32) input_shift_count = 32;
            }
            if ((shift_ctrl & (1u << 16)) && input_shift_count >= push_threshold()) {  // AUTOPUSH
                if (!rx.full()) {
                    rx.push(input_shift_reg);
                    RP2040PY_PIO_TRY(update_dma_rx());
                    RP2040PY_PIO_TRY(pio.check_interrupts());
                } else {
                    pio.rx_stall |= 1u << index;
                    pio.fdebug |= pio.rx_stall;
                    wait(kPioWaitRxFifo, false, input_shift_reg);
                }
                input_shift_count = 0;
                input_shift_reg = 0;
            }
            break;
        }
        case 0b011: {  // OUT
            if ((shift_ctrl & (1u << 17)) && output_shift_count >= pull_threshold()) {  // AUTOPULL
                output_shift_count = 0;
                if (!tx.empty()) {
                    output_shift_reg = tx.pull();
                    RP2040PY_PIO_TRY(update_dma_tx());
                    RP2040PY_PIO_TRY(pio.check_interrupts());
                } else {
                    pio.tx_stall |= (1u << 24) << index;
                    pio.fdebug |= pio.tx_stall;
                    wait(kPioWaitOut, false, arg);
                }
            }
            if (!waiting) RP2040PY_PIO_TRY(out_instruction(arg));
            break;
        }
        case 0b100: {  // PUSH / PULL
            const bool block_flag = (arg & (1u << 5)) != 0;
            const bool if_full_or_empty = (arg & (1u << 6)) != 0;
            if (arg & 0x1F) break;  // unknown instruction
            if (arg & 0x80) {       // PULL
                // IfEmpty: "do nothing unless the total output shift count has reached its threshold" - whether or not autopull is on; and "When autopull is enabled, any PULL
                // instruction is a no-op when the OSR is full" (the shift count is 0), so that it acts as a barrier behind the autopull (datasheet 3.4.7 and 3.5.4.2)
                if ((if_full_or_empty && output_shift_count < pull_threshold()) || ((shift_ctrl & (1u << 17)) && output_shift_count == 0)) break;
                if (!tx.empty()) {
                    output_shift_reg = tx.pull();
                    RP2040PY_PIO_TRY(update_dma_tx());
                    RP2040PY_PIO_TRY(pio.check_interrupts());
                } else {
                    pio.tx_stall |= (1u << 24) << index;
                    pio.fdebug |= pio.tx_stall;
                    if (block_flag) wait(kPioWaitTxFifo, false, 0);
                    else output_shift_reg = x;
                }
                output_shift_count = 0;
            } else {  // PUSH
                if (if_full_or_empty && input_shift_count < push_threshold()) break;  // IfFull: "do nothing unless the total input shift count has reached its threshold"; autopush or not
                if (!rx.full()) {
                    rx.push(input_shift_reg);
                    RP2040PY_PIO_TRY(update_dma_rx());
                    RP2040PY_PIO_TRY(pio.check_interrupts());
                } else {
                    pio.rx_stall |= 1u << index;
                    pio.fdebug |= pio.rx_stall;
                    if (block_flag) wait(kPioWaitRxFifo, false, input_shift_reg);
                }
                input_shift_reg = 0;
                input_shift_count = 0;
            }
            break;
        }
        case 0b101: {  // MOV
            const uint32_t source = arg & 0x7;
            const uint32_t op = (arg >> 3) & 0x3;
            const uint32_t destination = (arg >> 5) & 0x7;
            uint32_t value = 0;
            RP2040PY_PIO_TRY(in_source_value(source, &value));
            uint32_t transformed = value;
            if (op == 0b01) {
                transformed = ~value;
            } else if (op == 0b10) {  // bit reverse
                uint32_t v = value;
                v = ((v & 0x55555555u) << 1) | ((v & 0xAAAAAAAAu) >> 1);
                v = ((v & 0x33333333u) << 2) | ((v & 0xCCCCCCCCu) >> 2);
                v = ((v & 0x0F0F0F0Fu) << 4) | ((v & 0xF0F0F0F0u) >> 4);
                v = ((v & 0x00FF00FFu) << 8) | ((v & 0xFF00FF00u) >> 8);
                transformed = (v << 16) | (v >> 16);
            }
            RP2040PY_PIO_TRY(set_mov_destination(destination, transformed));
            break;
        }
        case 0b110: {  // IRQ
            if (arg & 0x80) break;  // unknown instruction
            const bool clear = (arg & 0x40) != 0;
            const bool wait_flag = (arg & 0x20) != 0;
            const uint32_t idx = arg & 0x1F;
            const uint32_t irq_idx = (idx & 0x10) ? ((idx & 0x4) | (((idx & 0x3) + index) & 0x3)) : (idx & 0x7);
            if (clear) {
                pio.irq &= ~(1u << irq_idx);
                RP2040PY_PIO_TRY(pio.irq_updated());
            } else {
                pio.irq |= 1u << irq_idx;
                RP2040PY_PIO_TRY(pio.irq_updated());
                if (wait_flag) wait(kPioWaitIrq, false, irq_idx);
            }
            break;
        }
        case 0b111: {  // SET
            const uint32_t data = arg & 0x1F;
            const uint32_t destination = arg >> 5;
            if (destination == 0b000) pio.pin_values_changed(data, set_base(), set_count());
            else if (destination == 0b001) x = data;
            else if (destination == 0b010) y = data;
            else if (destination == 0b100) pio.pin_directions_changed(data, set_base(), set_count());
            break;
        }
        default: break;  // an opcode wider than 16 bits (MOV EXEC / OUT EXEC of a 32-bit value) matches no instruction, as in the Python
    }
    return true;
}

inline bool PioMachine::execute_instruction(uint32_t opcode) noexcept {
    for (uint32_t chain = 0;; ++chain) {
        RP2040PY_PIO_TRY(execute_one(opcode));
        ++cycles;
        uint32_t sideset_n = sideset_count();
        if (sideset_n > 5) sideset_n = 5;  // not a hardware configuration; defined here (see the header comment)
        const uint32_t delay_sideset = (opcode >> 8) & 0x1F;
        const bool side_en = (exec_ctrl & (1u << 30)) != 0;
        const uint32_t delay = delay_sideset & ((1u << (5 - sideset_n)) - 1u);
        if (sideset_n != 0 && (!side_en || (delay_sideset & 0x10))) {
            const uint32_t sideset = delay_sideset >> (5 - sideset_n);
            RP2040PY_PIO_TRY(set_sideset(sideset, side_en ? sideset_n - 1 : sideset_n));
        }
        if (exec_valid) {
            exec_valid = false;
            if (chain + 1 >= kPioMaxExecChain) return true;  // a runaway EXEC chain: cut here (the Python would overflow its stack)
            opcode = exec_opcode;
            continue;
        }
        if (waiting) {
            if (wait_delay < 0) wait_delay = static_cast<int32_t>(delay);
            return check_wait();
        }
        cycles += delay;
        return true;
    }
}

inline bool PioMachine::step() noexcept {
    if (waiting) {
        RP2040PY_PIO_TRY(check_wait());
        if (waiting) return true;
    }
    const int64_t before = cycles;
    due_rearmed = false;
    update_pc = true;
    RP2040PY_PIO_TRY(execute_instruction(block->instructions[pc]));
    if (update_pc) next_pc();
    if (!due_rearmed) next_due_fp += (cycles - before) * div_fp;
    return true;
}

inline bool PioMachine::check_wait() noexcept {
    if (!waiting) return true;
    PioBlock& pio = *block;
    if (wait_type == kPioWaitIrq) {
        const bool irq_value = (pio.irq & (1u << wait_index)) != 0;
        if (irq_value == wait_polarity) {
            waiting = false;
            if (irq_value) pio.irq &= ~(1u << wait_index);
        }
    } else if (wait_type == kPioWaitPin) {
        if (wait_index < kPioGpio) {
            bool level = false;
            RP2040PY_PIO_TRY(pio.read_pin(wait_index, &level));
            if (level == wait_polarity) waiting = false;
        }
    } else if (wait_type == kPioWaitRxFifo) {
        if (!rx.full()) {
            rx.push(wait_index);
            waiting = false;
            RP2040PY_PIO_TRY(update_dma_rx());
            RP2040PY_PIO_TRY(pio.check_interrupts());
        }
    } else if (wait_type == kPioWaitTxFifo) {
        if (!tx.empty()) {
            output_shift_reg = tx.pull();
            waiting = false;
            RP2040PY_PIO_TRY(update_dma_tx());
            RP2040PY_PIO_TRY(pio.check_interrupts());
        }
    } else if (wait_type == kPioWaitOut) {
        if (!tx.empty()) {
            output_shift_reg = tx.pull();
            RP2040PY_PIO_TRY(out_instruction(wait_index));
            waiting = false;
            RP2040PY_PIO_TRY(update_dma_tx());
            RP2040PY_PIO_TRY(pio.check_interrupts());
        }
    }
    if (!waiting) {
        next_pc();
        cycles += wait_delay;
        exec_ctrl &= ~(1u << 31);
        // Absolute, not a delta (record 0063).
        const int64_t delay = wait_delay < 0 ? 0 : wait_delay;
        next_due_fp = pio.cycle_fp + (1 + delay) * div_fp;
        due_rearmed = true;
        pio.notify_due(next_due_fp);
    }
    return true;
}

inline bool PioMachine::restart() noexcept {
    cycles = 0;
    next_due_fp = block->cycle_fp;
    input_shift_count = 0;
    output_shift_count = 32;
    input_shift_reg = 0;
    waiting = false;
    exec_ctrl &= ~0x80000000u;  // EXEC_STALLED: "any stalled instruction written to SMx_INSTR or run by OUT/MOV EXEC" is cleared too
    return true;
}

inline void PioMachine::clk_div_restart() noexcept { next_due_fp = block->cycle_fp + div_fp; }

inline bool PioMachine::reset() noexcept {
    enabled = false;
    x = y = pc = 0;
    input_shift_reg = input_shift_count = output_shift_reg = 0;
    output_shift_count = 32;
    cycles = 0;
    exec_opcode = 0;
    exec_valid = false;
    update_pc = true;
    clock_div_int = 1;
    clock_div_frac = 0;
    div_fp = int64_t{1} << 8;
    next_due_fp = 0;
    due_rearmed = false;
    exec_ctrl = 0x1Fu << 12;
    shift_ctrl = 0b11u << 18;
    pin_ctrl = 0x5u << 26;
    rx.reset();
    tx.reset();
    out_pin_values = out_pin_direction = 0;
    waiting = false;
    wait_index = 0;
    wait_polarity = false;
    wait_delay = -1;
    RP2040PY_PIO_TRY(update_dma_rx());
    RP2040PY_PIO_TRY(update_dma_tx());
    wait_type = kPioWaitNone;
    return true;
}

inline bool PioMachine::read32(uint32_t offset, uint32_t* out) noexcept {
    using namespace pio_regs;
    switch (offset + SM0_CLKDIV) {
        case SM0_CLKDIV: *out = (clock_div_int << 16) | (clock_div_frac << 8); return true;
        case SM0_EXECCTRL: *out = exec_ctrl; return true;
        case SM0_SHIFTCTRL: *out = shift_ctrl; return true;
        case SM0_ADDR: *out = pc; return true;
        case SM0_INSTR: *out = block->instructions[pc]; return true;
        case SM0_PINCTRL: *out = pin_ctrl; return true;
        default: break;
    }
    if (block->host().log != nullptr) block->host().log(block->host().ctx, kPioErrorSmRead, offset, 0);
    *out = 0;
    return true;
}

inline bool PioMachine::write32(uint32_t offset, uint32_t value) noexcept {
    using namespace pio_regs;
    switch (offset + SM0_CLKDIV) {
        case SM0_CLKDIV:
            clock_div_frac = (value >> 8) & 0xFF;
            clock_div_int = value >> 16;
            div_fp = (static_cast<int64_t>(clock_div_int ? clock_div_int : 65536u) << 8) | clock_div_frac;
            block->recompute_due();
            return true;
        case SM0_EXECCTRL: exec_ctrl = (value & 0x7FFFFF9Fu) | (exec_ctrl & 0x80000000u); return true;  // 6:5 reserved, 31 read only
        case SM0_SHIFTCTRL: shift_ctrl = value & 0xFFFF0000u; return true;  // 15:0 reserved
        case SM0_ADDR: return true;  // read-only
        case SM0_INSTR:
            RP2040PY_PIO_TRY(execute_instruction(value & 0xFFFF));
            if (waiting) exec_ctrl |= 1u << 31;
            block->recompute_due();
            return true;
        case SM0_PINCTRL: pin_ctrl = value; return true;
        default: break;
    }
    if (block->host().log != nullptr) block->host().log(block->host().ctx, kPioErrorSmWrite, offset, 0);
    return true;
}

#undef RP2040PY_PIO_TRY

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PIO_HPP
