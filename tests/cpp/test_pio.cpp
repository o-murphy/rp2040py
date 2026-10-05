// Standalone checks of src/rp2040py/native/core/pio.hpp (see tests/test_core_cpp.py for the flags).
// The comparison with the pure-Python PIO on long random runs is tests/test_pio_diff.py; these are directed checks of what a Python-free build must also get right:
// the FIFO ring, each instruction, the wait kinds, the register file with its aliases, the pacing of record 0063, the order of the calls that leave the block, and
// failure propagation.
#include <cstdio>

#include "pio.hpp"

using namespace rp2040core;
using namespace rp2040core::pio_regs;

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

// --- what leaves the block, in order ---------------------------------------------------------------------------------------

enum Kind { kIrq, kDreq, kLog, kPinUpdate };
struct Event {
    Kind kind;
    uint32_t a;
    uint32_t b;
};

struct Host {
    static const int kMax = 4096;
    Event events[kMax];
    int count = 0;
    bool pin_level[32] = {};
    int fail_on_dreq_call = -1;
    int dreq_calls = 0;
    int fail_on_irq_call = -1;
    int irq_calls = 0;
    int started_calls = 0;

    void add(Kind kind, uint32_t a, uint32_t b) {
        if (count < kMax) events[count++] = Event{kind, a, b};
    }
    int count_of(Kind kind) const {
        int n = 0;
        for (int i = 0; i < count; ++i) n += events[i].kind == kind;
        return n;
    }
    bool has(Kind kind, uint32_t a, uint32_t b) const {
        for (int i = 0; i < count; ++i)
            if (events[i].kind == kind && events[i].a == a && events[i].b == b) return true;
        return false;
    }
};

static bool h_irq(void* ctx, uint32_t line, bool level) {
    Host* h = static_cast<Host*>(ctx);
    h->add(kIrq, line, level);
    return h->irq_calls++ != h->fail_on_irq_call;
}
static bool h_dreq(void* ctx, uint32_t channel, bool set) {
    Host* h = static_cast<Host*>(ctx);
    h->add(kDreq, channel, set);
    return h->dreq_calls++ != h->fail_on_dreq_call;
}
static void h_log(void* ctx, uint32_t kind, uint32_t a, uint32_t b) { static_cast<Host*>(ctx)->add(kLog, kind * 0x10000u + (a & 0xFFFF), b); }
static bool h_pin_update(void* ctx, uint32_t pin) {
    static_cast<Host*>(ctx)->add(kPinUpdate, pin, 0);
    return true;
}
static bool h_pin_input(void* ctx, uint32_t pin, bool* level) {
    *level = static_cast<Host*>(ctx)->pin_level[pin];
    return true;
}
static bool h_started(void* ctx) {
    ++static_cast<Host*>(ctx)->started_calls;
    return true;
}

struct Rig {
    Host host;
    PioBlock pio;
    explicit Rig(uint32_t index = 0) {
        PioHost h;
        h.set_irq = h_irq;
        h.dreq = h_dreq;
        h.log = h_log;
        h.pin_update = h_pin_update;
        h.pin_input = h_pin_input;
        h.started = h_started;
        h.ctx = &host;
        CHECK(pio.init(h, index == 0 ? 7 : 9, index));
        host.count = 0;
        host.dreq_calls = 0;
    }
    PioMachine& sm(uint32_t i = 0) { return pio.machines[i]; }
    void exec(uint32_t opcode, uint32_t i = 0) { CHECK(sm(i).execute_instruction(opcode)); }
};

static uint32_t op(uint32_t kind, uint32_t arg, uint32_t delay_sideset = 0) { return (kind << 13) | (delay_sideset << 8) | arg; }
enum { JMP = 0, WAIT = 1, IN = 2, OUT = 3, PUSHPULL = 4, MOV = 5, IRQ_OP = 6, SET = 7 };

// --- tests -----------------------------------------------------------------------------------------------------------------

static void test_fifo_is_the_python_ring() {
    PioFifo f;
    CHECK(f.empty() && !f.full() && f.pull() == 0);
    for (uint32_t i = 1; i <= 5; ++i) f.push(i);  // the fifth is ignored
    CHECK(f.full() && f.used == 4);
    CHECK(f.pull() == 1 && f.pull() == 2);
    f.push(6);
    f.push(7);
    CHECK(f.peek() == 3);
    f.reset();  // clears only the count: the start index survives
    CHECK(f.empty() && f.start == 2);
    f.push(9);
    CHECK(f.buffer[2] == 9 && f.pull() == 9);
    PioFifo g;  // draining a FIFO completely does not move the start back to 0
    g.push(1);
    CHECK(g.pull() == 1 && g.start == 1 && g.empty());
}

static void test_construction_publishes_the_dreq_levels_rx_then_tx() {
    Host host;
    PioBlock pio;
    PioHost h;
    h.dreq = h_dreq;
    h.ctx = &host;
    CHECK(pio.init(h, 9, 1));
    CHECK(host.count == 8);
    for (uint32_t m = 0; m < 4; ++m) {
        CHECK(host.events[2 * m].kind == kDreq && host.events[2 * m].a == 12 + m && host.events[2 * m].b == 0);      // RX channel, no data: clear
        CHECK(host.events[2 * m + 1].kind == kDreq && host.events[2 * m + 1].a == 8 + m && host.events[2 * m + 1].b == 1);  // TX channel, not full: set
    }
}

static void test_set_x_y_and_the_pin_images() {
    Rig r;
    r.exec(op(SET, (1 << 5) | 21));  // SET X, 21
    r.exec(op(SET, (2 << 5) | 7));   // SET Y, 7
    CHECK(r.sm().x == 21 && r.sm().y == 7);
    r.sm().pin_ctrl = (5u << 26) | (3u << 5);  // SET count 5, SET base 3
    r.exec(op(SET, (0 << 5) | 0b10101));       // SET PINS
    CHECK(r.pio.pin_values == (0b10101u << 3));
    r.exec(op(SET, (4 << 5) | 0b00111));  // SET PINDIRS
    CHECK(r.pio.pin_directions == (0b00111u << 3));
    CHECK(r.sm().cycles == 4);
    // the pins are told when the block checks what changed: each changed bit, once
    CHECK(r.pio.check_changed_pins());
    CHECK(r.host.count_of(kPinUpdate) == 4 && r.host.has(kPinUpdate, 3, 0) && r.host.has(kPinUpdate, 4, 0) && r.host.has(kPinUpdate, 5, 0) && r.host.has(kPinUpdate, 7, 0));  // bits 3,5,7 (values) and 3,4,5 (dirs)
    r.host.count = 0;
    CHECK(r.pio.check_changed_pins() && r.host.count == 0);  // nothing new
    r.pio.pin_directions |= 1u << 31;  // there are 30 GPIOs: a change in bit 31 tells no pin
    CHECK(r.pio.check_changed_pins() && r.host.count == 0);
}

static void test_jmp_conditions() {
    Rig r;
    r.sm().pc = 0;
    r.exec(op(JMP, (0 << 5) | 9));  // always
    CHECK(r.sm().pc == 9 && !r.sm().update_pc);
    r.sm().x = 0;
    r.sm().update_pc = true;
    r.exec(op(JMP, (1 << 5) | 5));  // !X taken
    CHECK(r.sm().pc == 5);
    r.sm().x = 2;
    r.exec(op(JMP, (2 << 5) | 6));  // X-- taken, x -> 1
    CHECK(r.sm().pc == 6 && r.sm().x == 1);
    r.sm().x = 0;
    r.sm().pc = 0;
    r.exec(op(JMP, (2 << 5) | 6));  // X-- not taken (was zero), x wraps to 0xFFFFFFFF
    CHECK(r.sm().pc == 0 && r.sm().x == 0xFFFFFFFFu);
    r.sm().y = 0;
    r.exec(op(JMP, (4 << 5) | 3));  // Y-- not taken
    CHECK(r.sm().pc == 0 && r.sm().y == 0xFFFFFFFFu);
    r.sm().x = 4;
    r.sm().y = 5;
    r.exec(op(JMP, (5 << 5) | 11));  // X != Y
    CHECK(r.sm().pc == 11);
    r.sm().exec_ctrl = (r.sm().exec_ctrl & ~(0x1Fu << 24)) | (13u << 24);  // JMP PIN = GPIO 13
    r.host.pin_level[13] = true;
    r.exec(op(JMP, (6 << 5) | 17));
    CHECK(r.sm().pc == 17);
    r.sm().output_shift_count = 31;
    r.exec(op(JMP, (7 << 5) | 2));  // !OSRE: count < threshold(32)
    CHECK(r.sm().pc == 2);
}

static void test_in_out_shifting_and_autopush_autopull() {
    Rig r;
    // IN, shift left: ISR = (ISR << n) | bits
    r.sm().shift_ctrl = 0;
    r.sm().x = 0b101101;
    r.exec(op(IN, (1 << 5) | 6));  // IN X, 6
    r.exec(op(IN, (1 << 5) | 3));  // IN X, 3  (x & 7 = 0b101)
    CHECK(r.sm().input_shift_reg == ((0b101101u << 3) | 0b101) && r.sm().input_shift_count == 9);
    // IN, shift right: bits enter from the left
    r.sm().shift_ctrl = 1u << 18;
    r.sm().input_shift_reg = 0;
    r.sm().input_shift_count = 0;
    r.sm().x = 0xFF;
    r.exec(op(IN, (1 << 5) | 4));
    CHECK(r.sm().input_shift_reg == 0xF0000000u);
    // autopush at the threshold (8): the word goes to the RX FIFO, the DREQ and the interrupt are updated
    r.sm().shift_ctrl = (1u << 16) | (8u << 20);
    r.sm().input_shift_reg = 0;
    r.sm().input_shift_count = 0;
    r.host.count = 0;
    r.exec(op(IN, (1 << 5) | 8));
    CHECK(r.sm().rx.used == 1 && r.sm().input_shift_count == 0 && r.sm().input_shift_reg == 0);
    CHECK(r.host.events[0].kind == kDreq && r.host.events[0].a == 4 && r.host.events[0].b == 1);   // RX0 now has data
    CHECK(r.host.events[1].kind == kIrq && r.host.events[1].a == 7 && r.host.events[2].a == 8);    // both lines re-announced
    // a full RX FIFO stalls the machine: RXSTALL and FDEBUG set, a wait on the RX FIFO armed
    for (int i = 0; i < 3; ++i) r.sm().rx.push(1);
    r.sm().input_shift_count = 0;
    r.exec(op(IN, (1 << 5) | 8));
    CHECK(r.sm().waiting && r.sm().wait_type == kPioWaitRxFifo && (r.pio.rx_stall & 1) && (r.pio.fdebug & 1));
    // draining one word lets the wait complete on the next check: its word is pushed, the machine moves on
    uint32_t word = 0;
    CHECK(r.sm().read_fifo(&word));
    CHECK(!r.sm().waiting && r.sm().rx.full());

    // OUT, shift right with autopull from the TX FIFO
    Rig q;
    q.sm().shift_ctrl = (1u << 19) | (1u << 17) | (8u << 25);  // out right, autopull, threshold 8
    q.sm().output_shift_count = 8;
    q.sm().tx.push(0xA5);
    q.exec(op(OUT, (1 << 5) | 4));  // OUT X, 4 : pulls first (count >= threshold), then shifts
    CHECK(q.sm().x == 0x5 && q.sm().output_shift_reg == 0xA && q.sm().output_shift_count == 4 && q.sm().tx.empty());
    // OUT, shift left: the top bits go out first
    Rig left;
    left.sm().shift_ctrl = 0;
    left.sm().output_shift_reg = 0x80000001u;
    left.sm().output_shift_count = 0;
    left.exec(op(OUT, (1 << 5) | 4));  // OUT X, 4
    CHECK(left.sm().x == 0x8 && left.sm().output_shift_reg == 0x10 && left.sm().output_shift_count == 4);
    // an empty TX FIFO at the threshold stalls it: TXSTALL, a wait OUT with the instruction argument
    q.sm().output_shift_count = 8;
    q.exec(op(OUT, (1 << 5) | 4));
    CHECK(q.sm().waiting && q.sm().wait_type == kPioWaitOut && (q.pio.tx_stall & (1u << 24)) && (q.pio.fdebug & (1u << 24)));
    CHECK(q.sm().write_fifo(0x3C));  // data arrives: the wait completes, the OUT runs
    CHECK(!q.sm().waiting && q.sm().x == 0xC);
}

static void test_push_pull_and_mov() {
    Rig r;
    r.sm().input_shift_reg = 0x1234;
    r.sm().input_shift_count = 7;
    r.exec(op(PUSHPULL, 0));  // PUSH, noblock
    CHECK(r.sm().rx.used == 1 && r.sm().input_shift_reg == 0 && r.sm().input_shift_count == 0);
    r.sm().shift_ctrl = (1u << 16) | (8u << 20);  // autopush, threshold 8
    r.sm().input_shift_count = 3;
    r.exec(op(PUSHPULL, 1 << 6));  // PUSH iffull: below the threshold, a no-op
    CHECK(r.sm().rx.used == 1);
    // PULL noblock on an empty TX FIFO copies X into the OSR
    r.sm().x = 77;
    r.exec(op(PUSHPULL, 0x80));
    CHECK(r.sm().output_shift_reg == 77 && r.sm().output_shift_count == 0 && (r.pio.tx_stall & (1u << 24)));
    // PULL block on an empty TX FIFO waits
    r.exec(op(PUSHPULL, 0x80 | (1 << 5)));
    CHECK(r.sm().waiting && r.sm().wait_type == kPioWaitTxFifo);
    r.sm().waiting = false;
    // MOV: invert and bit-reverse
    Rig m;
    m.sm().x = 0x0000000F;
    m.exec(op(MOV, (2 << 5) | (1 << 3) | 1));  // MOV Y, ~X
    CHECK(m.sm().y == 0xFFFFFFF0u);
    m.exec(op(MOV, (2 << 5) | (2 << 3) | 1));  // MOV Y, ::X
    CHECK(m.sm().y == 0xF0000000u);
    m.exec(op(MOV, (7 << 5) | (0 << 3) | 1));  // MOV OSR, X : counter back to 0
    CHECK(m.sm().output_shift_reg == 0xF && m.sm().output_shift_count == 0);
    m.exec(op(MOV, (6 << 5) | (0 << 3) | 2));  // MOV ISR, Y
    CHECK(m.sm().input_shift_reg == 0xF0000000u && m.sm().input_shift_count == 0);
}

static void test_exec_runs_the_data_as_an_instruction_and_a_wide_opcode_matches_nothing() {
    Rig r;
    r.sm().x = op(SET, (2 << 5) | 9);  // an instruction word held in X
    r.exec(op(MOV, (4 << 5) | 1));      // MOV EXEC, X
    CHECK(r.sm().y == 9 && !r.sm().exec_valid);
    CHECK(r.sm().cycles == 2);  // the MOV and the executed SET
    // a 32-bit value executed as an opcode: opcode >> 13 > 7 matches no instruction (not SET)
    Rig w;
    w.sm().x = 0x00040000u | op(SET, (2 << 5) | 9);
    w.exec(op(MOV, (4 << 5) | 1));
    CHECK(w.sm().y == 0);
    // a runaway EXEC chain (the executed instruction executes the next) is cut, and the call returns
    Rig c;
    c.sm().x = op(MOV, (4 << 5) | 1);  // MOV EXEC, X : executes itself forever
    c.exec(op(MOV, (4 << 5) | 1));
    CHECK(c.sm().cycles >= 1);
}

static void test_wait_gpio_pin_and_irq_and_the_irq_instruction() {
    Rig r;
    r.sm().enabled = true;
    r.exec(op(WAIT, 0x80 | 5));  // WAIT 1 GPIO 5
    CHECK(r.sm().waiting && r.sm().wait_type == kPioWaitPin && r.sm().wait_index == 5 && r.sm().wait_polarity);
    CHECK(r.sm().check_wait() && r.sm().waiting);
    r.host.pin_level[5] = true;
    r.pio.cycle_fp = 5000;
    r.sm().wait_delay = 3;
    r.sm().div_fp = 512;
    r.pio.next_due_fp = kPioNeverDue;
    CHECK(r.sm().check_wait() && !r.sm().waiting);
    CHECK(r.sm().next_due_fp == 5000 + (1 + 3) * 512 && r.sm().due_rearmed && r.pio.next_due_fp == r.sm().next_due_fp);  // absolute, and the block is told
    // WAIT PIN is relative to IN_BASE
    Rig p;
    p.sm().pin_ctrl = 3u << 15;
    p.exec(op(WAIT, 0x80 | (1 << 5) | 4));
    CHECK(p.sm().waiting && p.sm().wait_index == 7);
    // IRQ instruction: set, then a wait for it to be cleared; relative index adds the machine number
    Rig q;
    q.exec(op(IRQ_OP, 0x10 | 1), 2);  // IRQ rel 1 on machine 2 -> flag 3
    CHECK(q.pio.irq == (1u << 3));
    q.exec(op(IRQ_OP, (1 << 6) | 3), 0);  // IRQ clear 3
    CHECK(q.pio.irq == 0);
    q.exec(op(IRQ_OP, (1 << 5) | 2), 0);  // IRQ set 2 and wait
    CHECK(q.pio.irq == (1u << 2) && q.sm().waiting && q.sm().wait_type == kPioWaitIrq);
    CHECK(q.pio.write32(IRQ, 0));
    q.pio.raw_write_value = 1u << 2;  // the guest cleared flag 2
    CHECK(q.pio.write32(IRQ, 0));
    CHECK(q.pio.irq == 0 && !q.sm().waiting);
    // WAIT IRQ with polarity 1 consumes the flag
    Rig w;
    w.pio.irq = 1u << 4;
    w.exec(op(WAIT, 0x80 | (2 << 5) | 4));
    CHECK(w.sm().check_wait() && !w.sm().waiting && w.pio.irq == 0);
}

static void test_pacing_enable_restart_arrears() {
    Rig r;
    CHECK(r.pio.write_atomic(CTRL, 0b0001, kAtomicNormal));
    CHECK(r.host.started_calls == 1 && !r.pio.stopped && r.sm().enabled && r.sm().next_due_fp == r.pio.cycle_fp);
    // one instruction per advance: SET X,1 with a 1-cycle clock divider
    r.pio.instructions[0] = op(SET, (1 << 5) | 1);
    r.pio.instructions[1] = op(SET, (1 << 5) | 2);
    CHECK(r.pio.advance(1));
    CHECK(r.sm().x == 1 && r.sm().pc == 1);
    CHECK(r.pio.advance(1));
    CHECK(r.sm().x == 2);
    // a long idle jump: the backlog is written off, still only one instruction runs
    const int64_t before = r.pio.backlog_drops;
    CHECK(r.pio.advance(100000));
    CHECK(r.pio.backlog_drops == before + 1);
    // the arrears threshold is 8 cycles: a machine 8 behind keeps its schedule, 9 behind is written off
    Rig b;
    CHECK(b.pio.write_atomic(CTRL, 1, kAtomicNormal));
    b.pio.instructions[0] = op(SET, (1 << 5) | 1);
    b.pio.instructions[1] = op(SET, (1 << 5) | 1);
    b.pio.instructions[2] = op(SET, (1 << 5) | 1);
    CHECK(b.pio.advance(1) && b.sm().next_due_fp == b.pio.cycle_fp);
    CHECK(b.pio.advance(8) && b.pio.backlog_drops == 0);
    CHECK(b.pio.advance(9) && b.pio.backlog_drops == 1);
    // enabling a machine starts it now, not at a due time left over from before
    Rig e;
    e.pio.cycle_fp = 1000;
    e.sm().next_due_fp = 12345678;
    CHECK(e.pio.write_atomic(CTRL, 1, kAtomicNormal));
    CHECK(e.sm().next_due_fp == 1000);
    // the divider paces: with CLKDIV 4 a machine is due every fourth cycle
    Rig d;
    d.sm().clock_div_int = 4;
    d.sm().div_fp = 4 << 8;
    CHECK(d.pio.write_atomic(CTRL, 1, kAtomicNormal));
    d.pio.instructions[0] = op(SET, (1 << 5) | 1);
    d.pio.instructions[1] = op(SET, (2 << 5) | 1);
    d.pio.advance(1);
    CHECK(d.sm().x == 1 && d.sm().y == 0);
    d.pio.advance(1);
    d.pio.advance(1);
    CHECK(d.sm().y == 0);  // three cycles after the first instruction: not due yet
    d.pio.advance(1);
    CHECK(d.sm().y == 1);  // the fourth: due
    // stopping and a CTRL write with no machine enabled
    CHECK(d.pio.write_atomic(CTRL, 0, kAtomicNormal));
    CHECK(d.pio.stopped);
}

static void test_registers_aliases_and_warnings() {
    Rig r;
    CHECK(r.pio.read(DBG_CFGINFO) == 0x200404);
    CHECK(r.pio.write_atomic(INSTR_MEM0 + 8, 0x1FFFF, kAtomicNormal));
    CHECK(r.pio.instructions[2] == 0xFFFF);  // instruction memory is 16 bits wide (and write-only: a read is an unimplemented register)
    // set / xor / clear against a read of the register
    CHECK(r.pio.write_atomic(IRQ0_INTE, 0b0011, kAtomicNormal));
    CHECK(r.pio.write_atomic(IRQ0_INTE, 0b0110, kAtomicXor) && r.pio.irq0_int_enable == 0b0101);
    CHECK(r.pio.write_atomic(IRQ0_INTE, 0b1000, kAtomicSet) && r.pio.irq0_int_enable == 0b1101);
    CHECK(r.pio.write_atomic(IRQ0_INTE, 0b0001, kAtomicClear) && r.pio.irq0_int_enable == 0b1100);
    CHECK(r.pio.write_atomic(IRQ1_INTF, 0xFFFFF, kAtomicNormal) && r.pio.irq1_int_force == 0xFFF);  // 12 bits
    // IRQ is write-1-to-clear through the raw value; IRQ_FORCE sets
    CHECK(r.pio.write_atomic(IRQ_FORCE, 0b101, kAtomicNormal) && r.pio.irq == 0b101);
    CHECK(r.pio.write_atomic(IRQ, 0b001, kAtomicNormal) && r.pio.irq == 0b100);
    // FDEBUG is write-1-to-clear too, but the stall bits come straight back
    r.pio.fdebug = 0xFFFFFFFF;
    r.pio.tx_stall = 1u << 24;
    CHECK(r.pio.write_atomic(FDEBUG, 0xFFFFFFFF, kAtomicNormal) && r.pio.fdebug == (1u << 24));
    r.pio.fdebug = 0b11;
    r.pio.tx_stall = r.pio.rx_stall = 0;
    CHECK(r.pio.write_atomic(FDEBUG, 0b01, kAtomicSet) && r.pio.fdebug == 0b10);  // cleared by what the guest wrote (raw), not by the aliased value (0b11)
    CHECK(r.pio.write_atomic(IRQ0_INTE, 0xFFFFF, kAtomicNormal) && r.pio.irq0_int_enable == 0xFFF);
    // unimplemented registers warn; in the atomic area also say so; a state-machine register that does not exist is an error
    r.host.count = 0;
    CHECK(r.pio.read(0x1F0) == 0xFFFFFFFFu);
    CHECK(r.host.count == 1 && r.host.events[0].kind == kLog && r.host.events[0].a == (uint32_t{kPioWarnRead} << 16 | 0x1F0));
    r.host.count = 0;
    CHECK(r.pio.read(0x1010) == 0xFFFFFFFFu);
    CHECK(r.host.count == 2 && r.host.events[1].a >> 16 == kPioWarnReadAtomicArea);
    r.host.count = 0;
    CHECK(r.pio.write_atomic(0x1F0, 0x55, kAtomicNormal));
    CHECK(r.host.count == 1 && (r.host.events[0].a >> 16) == kPioWarnWrite && r.host.events[0].b == 0x55);
    r.host.count = 0;
    CHECK(r.pio.read(SM0_CLKDIV + 1) == 0);  // an unaligned offset inside a machine's block
    CHECK(r.host.count == 1 && (r.host.events[0].a >> 16) == kPioErrorSmRead);
    // a machine's registers
    CHECK(r.pio.write_atomic(SM0_CLKDIV + SM_STRIDE, (3u << 16) | (128u << 8), kAtomicNormal));
    CHECK(r.sm(1).div_fp == ((3 << 8) | 128) && r.pio.read(SM0_CLKDIV + SM_STRIDE) == ((3u << 16) | (128u << 8)));
    CHECK(r.pio.write_atomic(SM0_CLKDIV + 2 * SM_STRIDE, 0, kAtomicNormal));
    CHECK(r.sm(2).div_fp == (65536 << 8));  // CLKDIV_INT 0 divides by 65536
    r.sm().exec_ctrl = 1u << 31;
    CHECK(r.pio.write_atomic(SM0_EXECCTRL, 0xFFFFFFFF, kAtomicNormal));
    CHECK(r.sm().exec_ctrl == 0xFFFFFFFFu);  // EXEC_STALLED is read-only: kept as it was
    r.sm().exec_ctrl = 0;
    CHECK(r.pio.write_atomic(SM0_EXECCTRL, 0xFFFFFFFF, kAtomicNormal));
    CHECK(r.sm().exec_ctrl == 0x7FFFFFFFu);
    // SM_INSTR executes now and marks a stalled machine
    CHECK(r.pio.write_atomic(SM0_INSTR, op(WAIT, 0x80 | 9), kAtomicNormal));
    CHECK(r.sm().waiting && (r.sm().exec_ctrl & (1u << 31)));
}

static void test_fifo_registers_dreq_and_interrupts() {
    Rig r;
    r.pio.irq0_int_enable = 0xFF;
    r.host.count = 0;
    CHECK(r.pio.write_atomic(TXF0, 0x11, kAtomicNormal));
    CHECK(r.sm().tx.used == 1 && r.host.events[0].kind == kDreq && r.host.events[0].a == 0 && r.host.events[0].b == 1);
    for (int i = 0; i < 3; ++i) CHECK(r.pio.write_atomic(TXF0, 0x20 + i, kAtomicNormal));
    CHECK(r.sm().tx.full());
    r.host.count = 0;
    CHECK(r.pio.write_atomic(TXF0, 0x99, kAtomicNormal));  // overflow: ignored, TXOVER set
    CHECK(r.sm().tx.used == 4 && (r.pio.fdebug & (1u << 16)));
    CHECK(r.pio.read(FLEVEL) == 4);
    CHECK((r.pio.read(FSTAT) & (1u << 16)) && !(r.pio.read(FSTAT) & (1u << 24)));
    // reading an empty RX FIFO: RXUNDER, value 0
    CHECK(r.pio.read(RXF0) == 0 && (r.pio.fdebug & (1u << 8)));
    r.sm().rx.push(0xBEEF);
    r.host.count = 0;
    CHECK(r.pio.read(RXF0 + 0) == 0xBEEF);
    CHECK(r.host.count_of(kIrq) == 2);  // it became empty: both lines re-announced
    CHECK(r.pio.read(INTR) == (0x10u << 0 | 0x20 | 0x40 | 0x80) - 0x10);  // TX0 full, RX empty: only the other TX flags
}

static void test_failures_stop_at_once_and_leave_the_state_updated() {
    Rig r;
    r.host.fail_on_dreq_call = r.host.dreq_calls;  // the next DREQ call fails
    CHECK(!r.sm().write_fifo(5));
    CHECK(r.sm().tx.used == 1);  // the word is in
    Rig q;
    q.host.fail_on_irq_call = q.host.irq_calls;
    q.pio.irq0_int_enable = 1;
    CHECK(!q.pio.write_atomic(IRQ0_INTE, 3, kAtomicNormal));
    CHECK(q.pio.irq0_int_enable == 3);
}

static void test_reset_and_stop() {
    Rig r;
    r.sm().enabled = true;
    r.sm().x = 5;
    r.sm().tx.push(1);
    r.pio.instructions[3] = 7;
    r.pio.irq = 3;
    r.pio.pin_values = 0xFF;
    r.pio.raw_write_value = 99;
    r.pio.stopped = 0;
    r.host.count = 0;
    CHECK(r.pio.reset());
    CHECK(!r.sm().enabled && r.sm().x == 0 && r.sm().tx.empty() && r.pio.instructions[3] == 0 && r.pio.irq == 0 && r.pio.pin_values == 0 && r.pio.stopped);
    CHECK(r.pio.raw_write_value == 99);  // not state
    CHECK(r.host.count_of(kDreq) == 8 && r.host.count_of(kIrq) == 2);  // every machine republishes its DREQs; both lines re-announced
    r.sm().enabled = true;
    r.pio.stopped = 0;
    r.pio.stop();
    CHECK(!r.sm().enabled && r.pio.stopped);
}

static void test_a_sideset_count_above_five_is_clamped_not_undefined() {
    Rig r;
    r.sm().pin_ctrl = (7u << 29) | (0u << 10);  // SIDESET_COUNT 7: not a hardware value
    r.sm().pin_ctrl |= 4u << 10;  // SIDESET_BASE 4
    r.exec(op(SET, (1 << 5) | 1, 0x1F));
    CHECK(r.sm().x == 1);  // it runs, deterministically
    CHECK(r.pio.pin_values == (0x1Fu << 4));  // as a count of 5: all five side-set bits, no delay
}

int main() {
    test_fifo_is_the_python_ring();
    test_construction_publishes_the_dreq_levels_rx_then_tx();
    test_set_x_y_and_the_pin_images();
    test_jmp_conditions();
    test_in_out_shifting_and_autopush_autopull();
    test_push_pull_and_mov();
    test_exec_runs_the_data_as_an_instruction_and_a_wide_opcode_matches_nothing();
    test_wait_gpio_pin_and_irq_and_the_irq_instruction();
    test_pacing_enable_restart_arrears();
    test_registers_aliases_and_warnings();
    test_fifo_registers_dreq_and_interrupts();
    test_failures_stop_at_once_and_leave_the_state_updated();
    test_reset_and_stop();
    test_a_sideset_count_above_five_is_clamped_not_undefined();
    if (failures == 0) std::printf("test_pio: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
