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

using PioPinUpdateFn = bool (*)(void* ctx, uint32_t pin);                // a pin that is not a C++ one: gpio[pin].check_for_updates()
using PioPinInputFn = bool (*)(void* ctx, uint32_t pin, bool* level);    // ... and its input_value
using PioStartedFn = bool (*)(void* ctx);                                // CTRL started a stopped block: the host runs the "no owning Simulator" fallback

// The edges of the block that are the host's own (a pin of a bank the host holds, the "no Simulator" start); the lines, the requests and the warnings are the chip's `ChipPorts`.
struct PioHost {
    PioPinUpdateFn pin_update = nullptr;
    PioPinInputFn pin_input = nullptr;
    PioStartedFn started = nullptr;
    void* ctx = nullptr;
};

#define RP2040PY_PIO_TRY(expr) \
    do {                     \
        if (!(expr)) return false; \
    } while (0)

// `utils/fifo.FIFO(size)` exactly: a ring whose start index survives reset() (reset clears only the count), push masks to 32 bits and is ignored when full,
// pull on an empty FIFO returns 0. The size is 4, or - with SHIFTCTRL.FJOIN (datasheet 3.5.3) - 8 for the FIFO that took the other's storage and 0 for the one that
// gave it up (always both full and empty); `resize()` is what the reference's "replace the FIFO" is, a new empty ring.
struct PioFifo {
    static constexpr uint32_t kMaxSize = 8;
    uint32_t buffer[kMaxSize] = {0, 0, 0, 0, 0, 0, 0, 0};
    uint32_t size = 4;
    uint32_t start = 0;
    uint32_t used = 0;

    bool empty() const noexcept { return used == 0; }
    bool full() const noexcept { return used == size; }
    void push(uint32_t value) noexcept {
        if (used < size) {
            buffer[(start + used) % size] = value;
            ++used;
        }
    }
    uint32_t pull() noexcept {
        if (used == 0) return 0;
        const uint32_t value = buffer[start];
        start = (start + 1) % size;
        --used;
        return value;
    }
    uint32_t peek() const noexcept { return used != 0 ? buffer[start] : 0; }
    void reset() noexcept { used = 0; }
    void resize(uint32_t new_size) noexcept {
        size = new_size;
        start = used = 0;
        for (uint32_t i = 0; i < kMaxSize; ++i) buffer[i] = 0;
    }
};

class PioBlock;

// One write to the pin levels or directions as the arbitration sees it (datasheet 3.5.6.1): which of the two, and the GPIOs it asserts (`mask`) with their values (`data`), both already mapped.
struct PioPinWrite {
    bool valid = false;
    bool directions = false;
    uint32_t mask = 0;
    uint32_t data = 0;
};

// EXECCTRL bits of the pin-write options (SM0_EXECCTRL).
constexpr uint32_t kExecOutSticky = 1u << 17, kExecInlineOutEn = 1u << 18;
constexpr uint32_t kExecOutEnSelLsb = 19;

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
    // This cycle's write on the OUT/SET channel and on the side-set channel, and the write `OUT_STICKY` keeps re-asserting (the most recent OUT/SET or MOV PINS): `PioBlock::collate()`.
    PioPinWrite fresh_channel, fresh_side, held_write;
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
    inline void apply_fjoin() noexcept;
    inline bool update_dma_tx() noexcept;
    inline bool update_dma_rx() noexcept;
    inline bool write_fifo(uint32_t value) noexcept;
    inline bool read_fifo(uint32_t* out) noexcept;
    inline bool execute_instruction(uint32_t opcode) noexcept;
    inline bool step() noexcept;
    inline bool check_wait() noexcept;
    inline bool inline_out_enable(uint32_t value) const noexcept;
    inline void write_pins(bool directions, uint32_t value, uint32_t first_pin, uint32_t count, bool channel, bool enable = true) noexcept;
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

class PioBlock : public RegisterBlock<PioBlock> {
public:
    PioBlock() = default;
    PioBlock(const PioBlock&) = delete;  // the machines point at their block
    PioBlock& operator=(const PioBlock&) = delete;

    // `first_irq` is the line of IRQ0 (IRQ1 is the next); `index` is 0 or 1 (PIO0/PIO1), which fixes the DREQ channels (8 per block: TX0..3, then RX0..3). Publishes each machine's FIFO state
    // to the DMA, as the machines' constructors do (RX then TX, machine by machine).
    bool init(const ChipPorts* ports, uint32_t block, const PioHost& host, uint32_t first_irq, uint32_t index) noexcept {
        port_.init(ports, block, first_irq, index * 8);
        host_ = host;
        first_irq_ = first_irq;
        index_ = index;
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            PioMachine& m = machines[i];
            m = PioMachine();
            m.block = this;
            m.index = i;
            m.dreq_tx = i;       // (relative to the block's first request)
            m.dreq_rx = 4 + i;
            RP2040PY_PIO_TRY(m.update_dma_rx());
            RP2040PY_PIO_TRY(m.update_dma_tx());
        }
        for (uint32_t i = 0; i < 32; ++i) instructions[i] = 0;
        return true;
    }

    // A pin the block reads and updates directly (a C++ pin bank); a pin left unbound goes through the host.
    void bind_pin(uint32_t gpio, PinBank* bank, uint32_t slot = 0) noexcept {
        if (gpio >= kPioGpio) return;
        pins_[gpio] = bank;
        pin_slots_[gpio] = slot;
    }

    const PioHost& host() const noexcept { return host_; }
    const BlockPort& port() const noexcept { return port_; }
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
    // Pin-write arbitration (datasheet 3.5.6.1): `in_cycle` while `run_due()` steps the machines (their writes are collected, then collated once); `resolve_due_fp` is the cycle at which
    // the image falls back to the held (`OUT_STICKY`) writes after a higher-numbered machine overwrote them for one cycle.
    bool in_cycle = false;
    int64_t resolve_due_fp = kPioNeverDue;

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
        RP2040PY_PIO_TRY(port_.irq(irq0_int_status() != 0, 0));
        return port_.irq(irq1_int_status() != 0, 1);
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
            *level = pins_[gpio]->input_value(pin_slots_[gpio]);
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
    static void pin_mapping(uint32_t value, uint32_t first_pin, uint32_t count, uint32_t* mask_out, uint32_t* data_out) noexcept {
        uint32_t mask = count > 31 ? 0xFFFFFFFFu : ((1u << count) - 1u);
        if (first_pin != 0) {
            value = (value << first_pin) | (value >> (32 - first_pin));
            mask = (mask << first_pin) | (mask >> (32 - first_pin));
        }
        *mask_out = mask;
        *data_out = value;
    }
    // The pins in `mask` take `data`, the others keep their value ("If no state machine asserts a write to a GPIO's level or direction, the value does not change").
    void apply_pin_write(bool directions, uint32_t mask, uint32_t data) noexcept {
        uint32_t& image = directions ? pin_directions : pin_values;
        image = ((image & ~mask) | (data & mask)) & 0x3FFFFFFFu;
    }
    void pin_values_changed(uint32_t value, uint32_t first_pin, uint32_t count) noexcept {
        uint32_t mask, data;
        pin_mapping(value, first_pin, count, &mask, &data);
        apply_pin_write(false, mask, data);
    }
    void pin_directions_changed(uint32_t value, uint32_t first_pin, uint32_t count) noexcept {
        uint32_t mask, data;
        pin_mapping(value, first_pin, count, &mask, &data);
        apply_pin_write(true, mask, data);
    }

    // Do pin writes need the arbitration of `collate()`? Only while some machine has OUT_STICKY set or a write held - otherwise applying each write as it happens, in machine order, is the
    // same thing (the highest-numbered machine's write is the last one applied).
    bool pin_routing() const noexcept {
        for (uint32_t i = 0; i < kPioMachines; ++i)
            if (machines[i].held_write.valid || (machines[i].exec_ctrl & kExecOutSticky) != 0) return true;
        return false;
    }

    // One system-clock cycle of pin writes: "For each GPIO, PIO collates the writes from all four state machines, and applies the write from the highest-numbered state machine. This occurs
    // separately for output levels and output directions." A machine's write of the cycle is what it wrote on that cycle or - on the OUT/SET channel, with OUT_STICKY - the write it holds;
    // a side-set write comes on top of its own OUT/SET in the overlap ("the side-set takes precedence").
    void collate() noexcept {
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            PioMachine& m = machines[i];
            const PioPinWrite& write = m.fresh_channel.valid ? m.fresh_channel : m.held_write;
            if (write.valid) apply_pin_write(write.directions, write.mask, write.data);
            if (m.fresh_side.valid) apply_pin_write(m.fresh_side.directions, m.fresh_side.mask, m.fresh_side.data);
            m.fresh_channel = PioPinWrite();
            m.fresh_side = PioPinWrite();
        }
    }

    // The pin levels and directions after one more cycle in which no machine writes anything: every held write re-asserted, in machine order.
    void held_image(uint32_t* values, uint32_t* directions) const noexcept {
        uint32_t v = pin_values, d = pin_directions;
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            const PioPinWrite& w = machines[i].held_write;
            if (!w.valid) continue;
            uint32_t& image = w.directions ? d : v;
            image = ((image & ~w.mask) | (w.data & w.mask)) & 0x3FFFFFFFu;
        }
        *values = v;
        *directions = d;
    }

    // The block stops (no machine enabled) and nothing will step it to the next cycle: a held write that was overwritten for one cycle is back on the pins now, as it would be a cycle
    // later (sticky writes are asserted whether or not their machine is enabled).
    bool settle_held_writes() noexcept {
        if (resolve_due_fp == kPioNeverDue) return true;
        held_image(&pin_values, &pin_directions);
        resolve_due_fp = kPioNeverDue;
        recompute_due();
        return check_changed_pins();
    }

    // After a cycle's collation: if a held write was overwritten (by a higher-numbered machine's write, or by its own machine's side-set) the image falls back to the held writes one cycle
    // later - unless something writes again.
    void schedule_resolve() noexcept {
        uint32_t v, d;
        held_image(&v, &d);
        resolve_due_fp = (pin_values != v || pin_directions != d) ? cycle_fp + (int64_t{1} << 8) : kPioNeverDue;
    }

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
                    RP2040PY_PIO_TRY(pins_[gpio]->check_for_updates(pin_slots_[gpio]));
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
        next_due_fp = resolve_due_fp < next ? resolve_due_fp : next;
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
        if (resolve_due_fp <= now) {
            // A held write was overwritten for one cycle and that cycle is over: back to the held image, and the pins hear it before this cycle's writes
            held_image(&pin_values, &pin_directions);
            resolve_due_fp = kPioNeverDue;
            RP2040PY_PIO_TRY(check_changed_pins());
        }
        const bool routing = pin_routing();
        in_cycle = routing;
        for (uint32_t i = 0; i < kPioMachines; ++i) {
            PioMachine& m = machines[i];
            if (m.enabled && !m.waiting && m.next_due_fp <= now) {
                if (now - m.next_due_fp > kPioMaxArrearsFp) {
                    ++backlog_drops;
                    m.next_due_fp = now;
                }
                if (!m.step()) {
                    in_cycle = false;
                    return false;
                }
            }
        }
        in_cycle = false;
        if (routing || pin_routing()) {
            collate();
            schedule_resolve();
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
        port_.unimplemented_read(offset);
        *out = 0xFFFFFFFFu;
        return true;
    }

    // `write_uint32`: `value` is the value after any alias decode; the raw value the guest wrote is `raw_write_value()`.
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
                fdebug &= ~static_cast<uint32_t>(raw_write_value_);
                fdebug |= tx_stall | rx_stall;
                return true;
            case TXF0: case TXF0 + 4: case TXF0 + 8: case TXF0 + 12: return machines[(offset - TXF0) / 4].write_fifo(value);
            case IRQ:
                irq &= ~static_cast<uint32_t>(raw_write_value_);
                return irq_updated();
            case INPUT_SYNC_BYPASS: input_sync_bypass = value; return true;
            case IRQ_FORCE:
                irq |= value & 0xFFu;  // IRQ_FORCE and IRQ have eight bits (3.7: 7:0, the rest reserved)
                return irq_updated();
            case FSTAT: case FLEVEL: case RXF0: case RXF0 + 4: case RXF0 + 8: case RXF0 + 12: case DBG_PADOUT: case DBG_PADOE: case DBG_CFGINFO: case INTR: case IRQ0_INTS: case IRQ1_INTS:
                return true;  // read only (tables 371-387): a write is ignored, silently, as for the other read-only registers
            case IRQ0_INTE: irq0_int_enable = value & 0xFFF; return check_interrupts();
            case IRQ0_INTF: irq0_int_force = value & 0xFFF; return check_interrupts();
            case IRQ1_INTE: irq1_int_enable = value & 0xFFF; return check_interrupts();
            case IRQ1_INTF: irq1_int_force = value & 0xFFF; return check_interrupts();
            default: break;
        }
        port_.unimplemented_write(offset, value);
        return true;
    }

    // `RegisterBlock` supplies `write_atomic`: the alias is decoded against `read32` (the fallible read), then written here; only the low 32 bits count.
    bool write(uint32_t offset, int64_t value) noexcept { return write32(offset, static_cast<uint32_t>(value)); }
    bool decode_read(uint32_t offset, uint32_t* out) noexcept { return read32(offset, out); }

    // The whole block back to power-on (0089 Phase 5): instruction memory, the four machines, the IRQ flags, the pacing state. `raw_write_value()` is not state.
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
        in_cycle = false;
        resolve_due_fp = kPioNeverDue;
        irq0_int_enable = irq0_int_force = irq1_int_enable = irq1_int_force = 0;
        return check_interrupts();
    }

    void stop() noexcept {
        for (uint32_t i = 0; i < kPioMachines; ++i) machines[i].enabled = false;
        stopped = 1;
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
        if (should_run == 0) {
            stopped = 1;
            return settle_held_writes();
        }
        return true;
    }

    PioHost host_;
    uint32_t first_irq_ = 0;
    uint32_t index_ = 0;
    PinBank* pins_[kPioGpio] = {};
    uint32_t pin_slots_[kPioGpio] = {};  // each pin's position in its bank
};

// ======================================================================================================================================
// The machine

inline bool PioMachine::update_dma_tx() noexcept {
    return block->port().dreq(dreq_tx, !tx.full());
}
inline bool PioMachine::update_dma_rx() noexcept {
    return block->port().dreq(dreq_rx, !rx.empty());
}

inline void PioMachine::apply_fjoin() noexcept {
    const bool join_rx = (shift_ctrl & (1u << 31)) != 0, join_tx = (shift_ctrl & (1u << 30)) != 0;
    rx.resize(join_tx ? 0u : join_rx ? 8u : 4u);
    tx.resize(join_rx ? 0u : join_tx ? 8u : 4u);
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
            write_pins(false, value, out_base(), out_count(), /*channel=*/true, inline_out_enable(value));
            break;
        case 0b001: x = value; break;
        case 0b010: y = value; break;
        case 0b011: break;  // NULL
        case 0b100:         // PINDIRS
            out_pin_direction = value;
            write_pins(true, value, out_base(), out_count(), /*channel=*/true, inline_out_enable(value));
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

// EXECCTRL.INLINE_OUT_EN: "use a bit of OUT data as an auxiliary write enable", the bit OUT_EN_SEL of the data an OUT instruction shifted out. Without the option every OUT write is enabled.
inline bool PioMachine::inline_out_enable(uint32_t value) const noexcept {
    if ((exec_ctrl & kExecInlineOutEn) == 0) return true;
    return ((value >> ((exec_ctrl >> kExecOutEnSelLsb) & 0x1Fu)) & 1u) != 0;
}

// One write of `count` bits at `first_pin` to the pin levels or directions, through the arbitration of datasheet 3.5.6.1. `channel` is the OUT/SET (and MOV PINS) channel - the one
// OUT_STICKY holds; the other is side-set. A write with `enable` false is a write with an empty mask: it asserts nothing, and (sticky) it replaces the held write.
inline void PioMachine::write_pins(bool directions, uint32_t value, uint32_t first_pin, uint32_t count, bool channel, bool enable) noexcept {
    PioPinWrite write;
    write.valid = true;
    write.directions = directions;
    PioBlock::pin_mapping(value, first_pin, count, &write.mask, &write.data);
    if (!enable) write.mask = 0;
    if (!block->pin_routing()) {
        block->apply_pin_write(directions, write.mask, write.data);
        return;
    }
    if (channel) {
        fresh_channel = write;
        if (exec_ctrl & kExecOutSticky) held_write = write;
    } else {
        fresh_side = write;
    }
    if (!block->in_cycle) {  // a write outside a stepped cycle (SMx_INSTR, a woken machine): its own cycle
        block->collate();
        block->schedule_resolve();
        block->recompute_due();
    }
}

inline bool PioMachine::set_sideset(uint32_t value, uint32_t count) noexcept {
    write_pins((exec_ctrl & (1u << 29)) != 0, value, sideset_base(), count, /*channel=*/false);
    return true;
}

inline bool PioMachine::set_mov_destination(uint32_t destination, uint32_t value) noexcept {
    switch (destination) {
        case 0b000:
            out_pin_values = value;
            write_pins(false, value, out_base(), out_count(), /*channel=*/true);
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
            if (destination == 0b000) write_pins(false, data, set_base(), set_count(), /*channel=*/true);
            else if (destination == 0b001) x = data;
            else if (destination == 0b010) y = data;
            else if (destination == 0b100) write_pins(true, data, set_base(), set_count(), /*channel=*/true);
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
    held_write = PioPinWrite();  // "any pin write left asserted due to OUT_STICKY" (SM_RESTART, datasheet CTRL)
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
    apply_fjoin();  // (the reset SHIFTCTRL joins nothing: both back to 4 deep, empty)
    out_pin_values = out_pin_direction = 0;
    fresh_channel = fresh_side = held_write = PioPinWrite();
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
    block->port().warn(kPioErrorSmRead, offset, 0);
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
        case SM0_EXECCTRL:
            exec_ctrl = (value & 0x7FFFFF9Fu) | (exec_ctrl & 0x80000000u);  // 6:5 reserved, 31 read only
            if ((exec_ctrl & kExecOutSticky) == 0) held_write = PioPinWrite();  // nothing is re-asserted without OUT_STICKY
            return true;
        case SM0_SHIFTCTRL: {
            const uint32_t changed = (shift_ctrl ^ value) & ((1u << 31) | (1u << 30));
            shift_ctrl = value & 0xFFFF0000u;  // 15:0 reserved
            if (changed) {  // FJOIN_RX / FJOIN_TX: "FIFOs are flushed when this bit is changed"
                apply_fjoin();
                RP2040PY_PIO_TRY(update_dma_rx());
                RP2040PY_PIO_TRY(update_dma_tx());
                RP2040PY_PIO_TRY(check_wait());  // a stalled PUSH / PULL may find its FIFO different
                RP2040PY_PIO_TRY(block->check_interrupts());
            }
            return true;
        }
        case SM0_ADDR: return true;  // read-only
        case SM0_INSTR:
            RP2040PY_PIO_TRY(execute_instruction(value & 0xFFFF));
            if (waiting) exec_ctrl |= 1u << 31;
            block->recompute_due();
            return true;
        case SM0_PINCTRL: pin_ctrl = value; return true;
        default: break;
    }
    block->port().warn(kPioErrorSmWrite, offset, 0);
    return true;
}

#undef RP2040PY_PIO_TRY

}  // namespace rp2040core

#endif  // RP2040PY_CORE_PIO_HPP
