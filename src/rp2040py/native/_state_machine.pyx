"""Native Cython port of `StateMachine` - the RP2040 PIO peripheral's per-cycle interpreter
(opcode decode, shift-register math, wait/jmp condition checks). Simulator-performance side quest
(2026-08-12): profiling a real firmware boot found this class's own self-contained bit/register
arithmetic (`execute_instruction`, `step`, `check_wait`, `jmp_condition`, the bitfield-derived
property getters) accounting for ~34% of total time, with no existing native port (unlike the CPU
core/RP2040 bus/bit-ops, which already have `native/_cortex_m0_core.pyx`/`_rp2040.pyx`/`_bit.pyx`).

**Real, accepted limitation - not a claim of the same ~4-25x the CPU-core port measured.**
`rp2040`/`pio`/`rx_fifo`/`tx_fifo` stay `object`-typed (see `_state_machine.pxd`'s own comment for
why cimporting the concrete `RP2040`/`RPPIO` classes wouldn't help): every call crossing into
GPIO/DMA/FIFO/RPPIO (`self.rp2040.gpio[i].input_value`, `self.pio.check_interrupts()`,
`self.rx_fifo.push(...)`, ...) stays at today's ordinary Python-call speed regardless of how this
class itself is typed. Only the self-contained integer/bitwise logic - opcode decode, shift
register updates, the `sideset_count`/`in_base`/etc. bitfield getters, `jmp_condition`,
`check_wait`'s own dispatch - genuinely speeds up. Measure the real effect
(`docs/BACKLOG.md`'s Cython section), don't assume the ceiling.

Faithful, mechanical transcription of `peripherals/_state_machine.py` (the pure-Python reference -
read that file's own comments for the *meaning* of each branch; this file only re-derives them
where the C types actually change something) - every masking `& 0xFFFFFFFF` present there is kept
here too even where a C `unsigned int`'s own natural modular arithmetic would make it redundant,
specifically to avoid silently changing behavior versus the reference implementation this must stay
interchangeable with (`state_machine.py`'s facade picks either one transparently). Hex literals at
or above 0x80000000 are `U`-suffixed throughout - a bare literal in that range is a boxed Python
`int` constant to Cython, not a C one (confirmed real, costly bug in this project's own first
Cython pass over the CPU core - see `docs/BACKLOG.md`'s "boxing sources" section - suffixed
proactively here rather than found the same way twice).

`wait_type` stores `pio_registers.WaitType`'s own integer values directly (`WAIT_TYPE_*` module
constants below), not the Python `IntEnum` - must be kept in sync with that enum by hand if it
ever changes (`NONE=0, PIN=1, RX_FIFO=2, TX_FIFO=3, IRQ=4, OUT=5`); nothing outside this class ever
reads `.wait_type` today (confirmed by grep), so this is a pure internal representation choice.
"""

WAIT_TYPE_NONE = 0
WAIT_TYPE_PIN = 1
WAIT_TYPE_RX_FIFO = 2
WAIT_TYPE_TX_FIFO = 3
WAIT_TYPE_IRQ = 4
WAIT_TYPE_OUT = 5

# SHIFTCTRL bits (peripherals/pio_registers.py - kept in sync by hand, same reasoning as
# WAIT_TYPE_* above: real hardware register bit positions, not expected to ever change). Plain
# `cdef const unsigned int` module constants, matching _cortex_m0_core.pyx's own convention (not
# `DEF`/`IF` - deprecated by Cython 3 in favor of ordinary typed module-level constants).
cdef const unsigned int SHIFTCTRL_AUTOPUSH = 1 << 16
cdef const unsigned int SHIFTCTRL_AUTOPULL = 1 << 17
cdef const unsigned int SHIFTCTRL_IN_SHIFTDIR = 1 << 18
cdef const unsigned int SHIFTCTRL_OUT_SHIFTDIR = 1 << 19

# EXECCTRL bits
cdef const unsigned int EXECCTRL_STATUS_SEL = 1 << 4
cdef const unsigned int EXECCTRL_SIDE_PINDIR = 1U << 29
cdef const unsigned int EXECCTRL_SIDE_EN = 1U << 30
cdef const unsigned int EXECCTRL_EXEC_STALLED = 1U << 31

# FDEBUG bits
cdef const unsigned int FDEBUG_TXSTALL = 1 << 24
cdef const unsigned int FDEBUG_TXOVER = 1 << 16
cdef const unsigned int FDEBUG_RXUNDER = 1 << 8
cdef const unsigned int FDEBUG_RXSTALL = 1 << 0

# FSTAT bits
cdef const unsigned int FSTAT_TXEMPTY = 1 << 24
cdef const unsigned int FSTAT_TXFULL = 1 << 16
cdef const unsigned int FSTAT_RXEMPTY = 1 << 8
cdef const unsigned int FSTAT_RXFULL = 1 << 0

# State-machine register block, relative offsets (SM0_* in pio_registers.py; StateMachine's own
# read_uint32/write_uint32 always receive an offset already relative to whichever SMn_CLKDIV the
# caller (RPPIO) subtracted, so only the SM0_* set is needed here).
cdef const unsigned int SM0_CLKDIV = 0x0C8
cdef const unsigned int SM0_EXECCTRL = 0x0CC
cdef const unsigned int SM0_SHIFTCTRL = 0x0D0
cdef const unsigned int SM0_ADDR = 0x0D4
cdef const unsigned int SM0_INSTR = 0x0D8
cdef const unsigned int SM0_PINCTRL = 0x0DC


cdef inline unsigned int bit_reverse(unsigned int x) noexcept:
    x = ((x & 0x55555555) << 1) | ((x & 0xAAAAAAAAU) >> 1)
    x = ((x & 0x33333333) << 2) | ((x & 0xCCCCCCCCU) >> 2)
    x = ((x & 0x0F0F0F0F) << 4) | ((x & 0xF0F0F0F0U) >> 4)
    x = ((x & 0x00FF00FF) << 8) | ((x & 0xFF00FF00U) >> 8)
    x = ((x & 0x0000FFFF) << 16) | ((x & 0xFFFF0000U) >> 16)
    return x & 0xFFFFFFFFU


cdef inline unsigned int irq_index(unsigned int irq, unsigned int machine_index) noexcept:
    cdef bint rel = bool(irq & 0x10)
    if rel:
        return (irq & 0x4) | (((irq & 0x3) + machine_index) & 0x3)
    return irq & 0x7


cdef class StateMachine:
    def __init__(self, rp2040, pio, unsigned int index):
        self.rp2040 = rp2040
        self.pio = pio
        self.index = index

        self.enabled = False

        self.x = 0
        self.y = 0
        self.pc = 0
        self.input_shift_reg = 0
        self.input_shift_count = 0
        self.output_shift_reg = 0
        self.output_shift_count = 0
        self.cycles = 0

        self.exec_opcode = 0
        self.exec_valid = False
        self.update_pc = True

        self.clock_div_int = 1
        self.clock_div_frac = 0
        # Divider/due-time pacing - see _state_machine.py's own comments on these three for what
        # they mean and docs/records/0063 for why they exist.
        self.div_fp = 1 << 8
        self.next_due_fp = 0
        self.due_rearmed = False
        self.exec_ctrl = 0x1F << 12
        self.shift_ctrl = 0b11 << 18
        self.pin_ctrl = 0x5 << 26

        # Deliberately the same plain-Python rp2040py.utils.fifo.FIFO used by the pure-Python
        # reference implementation, not a typed/inlined replacement - see this file's own module
        # docstring for why (real, but partial, win; inlining FIFO is a documented possible
        # follow-up, not done here).
        from rp2040py.utils.fifo import FIFO
        self.rx_fifo = FIFO(4)
        self.tx_fifo = FIFO(4)

        self.out_pin_values = 0
        self.out_pin_direction = 0

        self.waiting = False
        self.wait_type = WAIT_TYPE_NONE
        self.wait_index = 0
        self.wait_polarity = False
        self.wait_delay = -1

        self.dreq_rx = self.pio.dreq_rx[self.index]
        self.dreq_tx = self.pio.dreq_tx[self.index]

        self._update_dma_rx()
        self._update_dma_tx()

    cdef void _update_dma_tx(self):
        if self.tx_fifo.full:
            self.rp2040.dma.clear_dreq(self.dreq_tx)
        else:
            self.rp2040.dma.set_dreq(self.dreq_tx)

    cdef void _update_dma_rx(self):
        if self.rx_fifo.empty:
            self.rp2040.dma.clear_dreq(self.dreq_rx)
        else:
            self.rp2040.dma.set_dreq(self.dreq_rx)

    cpdef write_fifo(self, unsigned int value):
        if self.tx_fifo.full:
            self.pio.fdebug |= FDEBUG_TXOVER << self.index
            return
        self.tx_fifo.push(value)
        self.pio.tx_stall &= ~(FDEBUG_TXSTALL << self.index)
        self._update_dma_tx()
        self.check_wait()
        if self.tx_fifo.full:
            self.pio.check_interrupts()

    cpdef unsigned int read_fifo(self) except? 0xFFFFFFFF:
        if self.rx_fifo.empty:
            self.pio.fdebug |= FDEBUG_RXUNDER << self.index
            return 0
        result = self.rx_fifo.pull()
        self.pio.rx_stall &= ~(FDEBUG_RXSTALL << self.index)
        self._update_dma_rx()
        self.check_wait()
        if self.rx_fifo.empty:
            self.pio.check_interrupts()
        return result

    @property
    def status(self):
        cdef unsigned int status_n = self.exec_ctrl & 0xF
        if self.exec_ctrl & EXECCTRL_STATUS_SEL:
            return 0xFFFFFFFF if self.rx_fifo.item_count < status_n else 0
        return 0xFFFFFFFF if self.tx_fifo.item_count < status_n else 0

    cdef bint jmp_condition(self, unsigned int condition) except -1:
        # (no condition): Always
        if condition == 0b000:
            return True

        # !X: scratch X zero
        if condition == 0b001:
            return self.x == 0

        # X--: scratch X non-zero, post-decrement
        if condition == 0b010:
            old_x = self.x
            self.x = (self.x - 1) & 0xFFFFFFFFU
            return old_x != 0

        # !Y: scratch Y zero
        if condition == 0b011:
            return self.y == 0

        # Y--: scratch Y non-zero, post-decrement
        if condition == 0b100:
            old_y = self.y
            self.y = (self.y - 1) & 0xFFFFFFFFU
            return old_y != 0

        # X!=Y: scratch X not equal scratch Y
        if condition == 0b101:
            return (self.x & 0xFFFFFFFFU) != (self.y & 0xFFFFFFFFU)

        # PIN: branch on input pin
        if condition == 0b110:
            gpio = self.rp2040.gpio
            jmp_pin = self.jmp_pin
            return gpio[jmp_pin].input_value if jmp_pin < len(gpio) else False

        # !OSRE: output shift register not empty
        if condition == 0b111:
            return self.output_shift_count < self.pull_threshold

        self.pio.error(f"jmpCondition with unsupported condition: {condition}")
        return False

    @property
    def in_pins(self):
        # A 32-bit rotate-right by in_base - see peripherals/_state_machine.py's own copy of this
        # comment for the real latent bug this `& 0xFFFFFFFFU` closes (found here first: this
        # property's untyped Python-level result feeds straight into in_source_value()'s typed
        # `unsigned int` return, which raised a real OverflowError on realistic GPIO values before
        # this fix - Python's arbitrary-precision ints had silently carried the extra high bits).
        gpio_values = self.rp2040.gpio_values
        in_base = self.in_base
        if in_base:
            return ((gpio_values << (32 - in_base)) | (gpio_values >> in_base)) & 0xFFFFFFFFU
        return gpio_values

    cdef unsigned int in_source_value(self, unsigned int source) except? 0xFFFFFFFF:
        # PINS
        if source == 0b000:
            return self.in_pins

        # X (scratch register X)
        if source == 0b001:
            return self.x

        # Y (scratch register Y)
        if source == 0b010:
            return self.y

        # NULL (all zeroes)
        if source == 0b011:
            return 0

        # Reserved
        if source == 0b100:
            return 0

        # Reserved for IN, STATUS for MOV
        if source == 0b101:
            return self.status

        # ISR
        if source == 0b110:
            return self.input_shift_reg

        # OSR
        if source == 0b111:
            return self.output_shift_reg

        self.pio.error(f"inSourceValue with unsupported source: {source}")
        return 0

    cdef void write_out_value(self, unsigned int destination, unsigned int value, unsigned int bit_count):
        # PINS
        if destination == 0b000:
            self.set_out_pins(value)

        # X (scratch register X)
        elif destination == 0b001:
            self.x = value

        # Y (scratch register Y)
        elif destination == 0b010:
            self.y = value

        # NULL (discard data)
        elif destination == 0b011:
            pass

        # PINDIRS
        elif destination == 0b100:
            self.set_out_pin_dirs(value)

        # PC
        elif destination == 0b101:
            self.pc = value & 0x1F
            self.update_pc = False

        # ISR (also sets ISR shift counter to Bit count)
        elif destination == 0b110:
            self.input_shift_reg = value
            self.input_shift_count = bit_count

        # EXEC (Execute OSR shift data as instruction)
        elif destination == 0b111:
            self.exec_opcode = value
            self.exec_valid = True

    @property
    def push_threshold(self):
        cdef unsigned int value = (self.shift_ctrl >> 20) & 0x1F
        return value if value else 32

    @property
    def pull_threshold(self):
        cdef unsigned int value = (self.shift_ctrl >> 25) & 0x1F
        return value if value else 32

    @property
    def sideset_count(self):
        return (self.pin_ctrl >> 29) & 0x7

    @property
    def set_count(self):
        return (self.pin_ctrl >> 26) & 0x7

    @property
    def out_count(self):
        return (self.pin_ctrl >> 20) & 0x3F

    @property
    def in_base(self):
        return (self.pin_ctrl >> 15) & 0x1F

    @property
    def sideset_base(self):
        return (self.pin_ctrl >> 10) & 0x1F

    @property
    def set_base(self):
        return (self.pin_ctrl >> 5) & 0x1F

    @property
    def out_base(self):
        return (self.pin_ctrl >> 0) & 0x1F

    @property
    def jmp_pin(self):
        return (self.exec_ctrl >> 24) & 0x1F

    @property
    def wrap_top(self):
        return (self.exec_ctrl >> 12) & 0x1F

    @property
    def wrap_bottom(self):
        return (self.exec_ctrl >> 7) & 0x1F

    cdef void set_out_pin_dirs(self, unsigned int value):
        self.out_pin_direction = value
        self.pio.pin_directions_changed(value, self.out_base, self.out_count)

    cdef void set_out_pins(self, unsigned int value):
        self.out_pin_values = value
        self.pio.pin_values_changed(value, self.out_base, self.out_count)

    cdef void out_instruction(self, unsigned int arg):
        cdef unsigned int bit_count = arg & 0x1F
        cdef unsigned int destination = arg >> 5
        cdef unsigned int value

        if bit_count == 0:
            self.write_out_value(destination, self.output_shift_reg, 32)
            self.output_shift_count = 32
        else:
            if self.shift_ctrl & SHIFTCTRL_OUT_SHIFTDIR:
                value = self.output_shift_reg & ((1 << bit_count) - 1)
                self.output_shift_reg = (self.output_shift_reg & 0xFFFFFFFFU) >> bit_count
                self.write_out_value(destination, value, bit_count)
            else:
                value = (self.output_shift_reg & 0xFFFFFFFFU) >> (32 - bit_count)
                self.output_shift_reg = (self.output_shift_reg << bit_count) & 0xFFFFFFFFU
                self.write_out_value(destination, value, bit_count)
            self.output_shift_count += bit_count
            self.output_shift_count = min(self.output_shift_count, 32)

    cpdef execute_instruction(self, unsigned int opcode):
        cdef unsigned int arg = opcode & 0xFF
        cdef unsigned int instruction = opcode >> 13
        cdef unsigned int bit_count, source_value, source, op, destination, data, irq, index
        cdef unsigned int sideset_count, exec_ctrl, delay_sideset, delay, sideset
        cdef unsigned int value, transformed_value
        cdef bint polarity, block, if_full_or_empty, side_en, clear, wait_flag

        # JMP
        if instruction == 0b000:
            if self.jmp_condition(arg >> 5):
                self.pc = arg & 0x1F
                self.update_pc = False

        # WAIT
        elif instruction == 0b001:
            polarity = bool(arg & 0x80)
            source = (arg >> 5) & 0x3
            index = arg & 0x1F
            # GPIO:
            if source == 0b00:
                self.wait(WAIT_TYPE_PIN, polarity, index)
            # PIN:
            elif source == 0b01:
                self.wait(WAIT_TYPE_PIN, polarity, (index + self.in_base) % 32)
            # IRQ:
            elif source == 0b10:
                self.wait(WAIT_TYPE_IRQ, polarity, irq_index(index, self.index))

        # IN
        elif instruction == 0b010:
            bit_count = arg & 0x1F
            source_value = self.in_source_value(arg >> 5)

            if bit_count == 0:
                self.input_shift_reg = source_value
                self.input_shift_count = 32
            else:
                source_value &= (1 << bit_count) - 1
                if self.shift_ctrl & SHIFTCTRL_IN_SHIFTDIR:
                    self.input_shift_reg = (self.input_shift_reg & 0xFFFFFFFFU) >> bit_count
                    self.input_shift_reg |= source_value << (32 - bit_count)
                else:
                    self.input_shift_reg = (self.input_shift_reg << bit_count) & 0xFFFFFFFFU
                    self.input_shift_reg |= source_value
                self.input_shift_count += bit_count
                self.input_shift_count = min(self.input_shift_count, 32)

            if self.shift_ctrl & SHIFTCTRL_AUTOPUSH and self.input_shift_count >= self.push_threshold:
                if not self.rx_fifo.full:
                    self.rx_fifo.push(self.input_shift_reg)
                    self._update_dma_rx()
                    self.pio.check_interrupts()
                else:
                    self.pio.rx_stall |= FDEBUG_RXSTALL << self.index
                    self.pio.fdebug |= self.pio.rx_stall
                    self.wait(WAIT_TYPE_RX_FIFO, False, self.input_shift_reg)
                self.input_shift_count = 0
                self.input_shift_reg = 0

        # OUT
        elif instruction == 0b011:
            if self.shift_ctrl & SHIFTCTRL_AUTOPULL and self.output_shift_count >= self.pull_threshold:
                self.output_shift_count = 0
                if not self.tx_fifo.empty:
                    self.output_shift_reg = self.tx_fifo.pull()
                    self._update_dma_tx()
                    self.pio.check_interrupts()
                else:
                    self.pio.tx_stall |= FDEBUG_TXSTALL << self.index
                    self.pio.fdebug |= self.pio.tx_stall
                    self.wait(WAIT_TYPE_OUT, False, arg)

            if not self.waiting:
                self.out_instruction(arg)

        # PUSH/PULL
        elif instruction == 0b100:
            block = bool(arg & (1 << 5))
            if_full_or_empty = bool(arg & (1 << 6))
            if arg & 0x1F:
                # Unknown instruction
                pass
            elif arg & 0x80:
                # PULL
                if (
                    if_full_or_empty
                    and self.shift_ctrl & SHIFTCTRL_AUTOPULL
                    and self.output_shift_count < self.pull_threshold
                ):
                    pass
                else:
                    if not self.tx_fifo.empty:
                        self.output_shift_reg = self.tx_fifo.pull()
                        self._update_dma_tx()
                        self.pio.check_interrupts()
                    else:
                        self.pio.tx_stall |= FDEBUG_TXSTALL << self.index
                        self.pio.fdebug |= self.pio.tx_stall
                        if block:
                            self.wait(WAIT_TYPE_TX_FIFO, False, 0)
                        else:
                            self.output_shift_reg = self.x
                    self.output_shift_count = 0
            else:
                # PUSH
                if (
                    if_full_or_empty
                    and self.shift_ctrl & SHIFTCTRL_AUTOPUSH
                    and self.input_shift_count < self.push_threshold
                ):
                    pass
                else:
                    if not self.rx_fifo.full:
                        self.rx_fifo.push(self.input_shift_reg)
                        self._update_dma_rx()
                        self.pio.check_interrupts()
                    else:
                        self.pio.rx_stall |= FDEBUG_RXSTALL << self.index
                        self.pio.fdebug |= self.pio.rx_stall
                        if block:
                            self.wait(WAIT_TYPE_RX_FIFO, False, self.input_shift_reg)
                    self.input_shift_reg = 0
                    self.input_shift_count = 0

        # MOV
        elif instruction == 0b101:
            source = arg & 0x7
            op = (arg >> 3) & 0x3
            destination = (arg >> 5) & 0x7
            value = self.in_source_value(source)
            transformed_value = self.transform_mov_value(value, op) & 0xFFFFFFFFU
            self.set_mov_destination(destination, transformed_value)

        # IRQ
        elif instruction == 0b110:
            if arg & 0x80:
                pass  # Unknown instruction
            else:
                clear = bool(arg & 0x40)
                wait_flag = bool(arg & 0x20)
                irq = irq_index(arg & 0x1F, self.index)
                if clear:
                    self.pio.irq &= ~(1 << irq)
                    self.pio.irq_updated()
                else:
                    self.pio.irq |= 1 << irq
                    self.pio.irq_updated()
                    if wait_flag:
                        self.wait(WAIT_TYPE_IRQ, False, irq)

        # SET
        elif instruction == 0b111:
            data = arg & 0x1F
            destination = arg >> 5
            if destination == 0b000:
                self.set_set_pins(data)
            elif destination == 0b001:
                self.x = data
            elif destination == 0b010:
                self.y = data
            elif destination == 0b100:
                self.set_set_pin_dirs(data)

        self.cycles += 1

        sideset_count, exec_ctrl = self.sideset_count, self.exec_ctrl
        delay_sideset = (opcode >> 8) & 0x1F
        side_en = bool(exec_ctrl & EXECCTRL_SIDE_EN)
        delay = delay_sideset & ((1 << (5 - sideset_count)) - 1)

        if sideset_count and (not side_en or delay_sideset & 0x10):
            sideset = delay_sideset >> (5 - sideset_count)
            self.set_sideset(sideset, sideset_count - 1 if side_en else sideset_count)

        if self.exec_valid:
            self.exec_valid = False
            self.execute_instruction(self.exec_opcode)
        elif self.waiting:
            if self.wait_delay < 0:
                self.wait_delay = delay
            self.check_wait()
        else:
            self.cycles += delay

    cdef void wait(self, unsigned int wait_type, bint polarity, unsigned int index):
        self.waiting = True
        self.wait_type = wait_type
        self.wait_polarity = polarity
        self.wait_index = index
        self.wait_delay = -1
        self.update_pc = False

    cdef void next_pc(self):
        if self.pc == self.wrap_top:
            self.pc = self.wrap_bottom
        else:
            self.pc = (self.pc + 1) & 0x1F

    cpdef step(self):
        """See _state_machine.py's own step() docstring - `RPPIO.advance()` decides when this
        runs, this re-arms `next_due_fp` for the instruction after (docs/records/0063)."""
        cdef long long before
        if self.waiting:
            self.check_wait()
            if self.waiting:
                return

        before = self.cycles
        self.due_rearmed = False
        self.update_pc = True
        self.execute_instruction(self.pio.instructions[self.pc])
        if self.update_pc:
            self.next_pc()
        if not self.due_rearmed:
            self.next_due_fp += (self.cycles - before) * self.div_fp

    cdef void set_set_pin_dirs(self, unsigned int value):
        self.pio.pin_directions_changed(value, self.set_base, self.set_count)

    cdef void set_set_pins(self, unsigned int value):
        self.pio.pin_values_changed(value, self.set_base, self.set_count)

    cdef void set_sideset(self, unsigned int value, unsigned int count):
        if self.exec_ctrl & EXECCTRL_SIDE_PINDIR:
            self.pio.pin_directions_changed(value, self.sideset_base, count)
        else:
            self.pio.pin_values_changed(value, self.sideset_base, count)

    cdef unsigned int transform_mov_value(self, unsigned int value, unsigned int op):
        if op == 0b00:
            return value
        if op == 0b01:
            return (~value) & 0xFFFFFFFFU
        if op == 0b10:
            return bit_reverse(value)
        # 0b11 and any other value: reserved
        return value

    cdef void set_mov_destination(self, unsigned int destination, unsigned int value):
        # PINS
        if destination == 0b000:
            self.set_out_pins(value)

        # X (scratch register X)
        elif destination == 0b001:
            self.x = value

        # Y (scratch register Y)
        elif destination == 0b010:
            self.y = value

        # reserved (discard data)
        elif destination == 0b011:
            pass

        # EXEC
        elif destination == 0b100:
            self.exec_opcode = value
            self.exec_valid = True

        # PC
        elif destination == 0b101:
            self.pc = value & 0x1F
            self.update_pc = False

        # ISR (Input shift counter is reset to 0 by this operation, i.e. empty)
        elif destination == 0b110:
            self.input_shift_reg = value
            self.input_shift_count = 0

        # OSR (Output shift counter is reset to 0 by this operation, i.e. full)
        elif destination == 0b111:
            self.output_shift_reg = value
            self.output_shift_count = 0

    cpdef unsigned int read_uint32(self, unsigned int offset) except? 0xFFFFFFFF:
        cdef unsigned int absolute_offset = offset + SM0_CLKDIV
        if absolute_offset == SM0_CLKDIV:
            return (self.clock_div_int << 16) | (self.clock_div_frac << 8)
        if absolute_offset == SM0_EXECCTRL:
            return self.exec_ctrl
        if absolute_offset == SM0_SHIFTCTRL:
            return self.shift_ctrl
        if absolute_offset == SM0_ADDR:
            return self.pc
        if absolute_offset == SM0_INSTR:
            return self.pio.instructions[self.pc]
        if absolute_offset == SM0_PINCTRL:
            return self.pin_ctrl
        self.pio.error(f"Read from invalid state machine register: {offset}")
        return 0

    cpdef write_uint32(self, unsigned int offset, unsigned int value):
        cdef unsigned int absolute_offset = offset + SM0_CLKDIV
        if absolute_offset == SM0_CLKDIV:
            self.clock_div_frac = (value >> 8) & 0xFF
            self.clock_div_int = value >> 16
            # CLKDIV_INT == 0 divides by 65536 (RP2040 datasheet 3.5.5), not by zero.
            self.div_fp = ((self.clock_div_int if self.clock_div_int else 65536) << 8) | self.clock_div_frac
            self.pio.recompute_due()
        elif absolute_offset == SM0_EXECCTRL:
            self.exec_ctrl = ((value & 0x7FFFFFFF) | (self.exec_ctrl & 0x80000000U)) & 0xFFFFFFFFU
        elif absolute_offset == SM0_SHIFTCTRL:
            self.shift_ctrl = value
        elif absolute_offset == SM0_ADDR:
            pass  # read-only
        elif absolute_offset == SM0_INSTR:
            self.execute_instruction(value & 0xFFFF)
            if self.waiting:
                self.exec_ctrl |= EXECCTRL_EXEC_STALLED
            self.pio.recompute_due()
        elif absolute_offset == SM0_PINCTRL:
            self.pin_ctrl = value
        else:
            self.pio.error(f"Write to invalid state machine register: {offset}")

    @property
    def fifo_stat(self):
        cdef unsigned int result = (
            (FSTAT_TXEMPTY if self.tx_fifo.empty else 0)
            | (FSTAT_TXFULL if self.tx_fifo.full else 0)
            | (FSTAT_RXEMPTY if self.rx_fifo.empty else 0)
            | (FSTAT_RXFULL if self.rx_fifo.full else 0)
        )
        return result << self.index

    def reset(self):
        """Every state-machine register back to power-on (0089 Phase 5).

        Distinct from `restart()` above, which is `CTRL.SM_RESTART` - a *guest-requested* partial
        restart that deliberately keeps `x`/`y`/the OSR and the divider phase. A chip reset keeps
        nothing: this is what makes a PIO-driven pin stop driving after a reset instead of the
        state machine picking up mid-waveform.

        `rp2040`/`pio`/`index` and the two DREQ channel numbers are identity, not state. The DMA
        re-publish at the end matters for the same reason it does in `RPSPI.reset()` - DREQ state
        lives in the DMA block, so a silently-cleared FIFO would otherwise leave DMA still
        believing this machine wants service."""
        self.enabled = False
        self.x = 0
        self.y = 0
        self.pc = 0
        self.input_shift_reg = 0
        self.input_shift_count = 0
        self.output_shift_reg = 0
        self.output_shift_count = 0
        self.cycles = 0
        self.exec_opcode = 0
        self.exec_valid = False
        self.update_pc = True
        self.clock_div_int = 1
        self.clock_div_frac = 0
        self.div_fp = 1 << 8
        self.next_due_fp = 0
        self.due_rearmed = False
        self.exec_ctrl = 0x1F << 12
        self.shift_ctrl = 0b11 << 18
        self.pin_ctrl = 0x5 << 26
        self.rx_fifo.reset()
        self.tx_fifo.reset()
        self.out_pin_values = 0
        self.out_pin_direction = 0
        self.waiting = False
        self.wait_index = 0
        self.wait_polarity = False
        self.wait_delay = -1
        self._update_dma_rx()
        self._update_dma_tx()
        self.wait_type = WAIT_TYPE_NONE

    cpdef restart(self):
        self.cycles = 0
        self.next_due_fp = self.pio.cycle_fp
        self.input_shift_count = 0
        self.output_shift_count = 32
        self.input_shift_reg = 0
        self.waiting = False
        # TODO any pin write left asserted due to OUT_STICKY.

    cpdef clk_div_restart(self):
        """`CTRL.CLKDIV_RESTART`: restarts the clock divider from phase 0 - see
        _state_machine.py's own clk_div_restart() (docs/records/0063)."""
        self.next_due_fp = <long long>self.pio.cycle_fp + self.div_fp

    cpdef check_wait(self):
        if not self.waiting:
            return

        if self.wait_type == WAIT_TYPE_IRQ:
            irq_value = bool(self.pio.irq & (1 << self.wait_index))
            if irq_value == self.wait_polarity:
                self.waiting = False
                if irq_value:
                    self.pio.irq &= ~(1 << self.wait_index)

        elif self.wait_type == WAIT_TYPE_PIN:
            gpio = self.rp2040.gpio
            if self.wait_index < len(gpio) and gpio[self.wait_index].input_value == self.wait_polarity:
                self.waiting = False

        elif self.wait_type == WAIT_TYPE_RX_FIFO:
            if not self.rx_fifo.full:
                self.rx_fifo.push(self.wait_index)
                self.waiting = False
                self._update_dma_rx()
                self.pio.check_interrupts()

        elif self.wait_type == WAIT_TYPE_TX_FIFO:
            if not self.tx_fifo.empty:
                self.output_shift_reg = self.tx_fifo.pull()
                self.waiting = False
                self._update_dma_tx()
                self.pio.check_interrupts()

        elif self.wait_type == WAIT_TYPE_OUT:
            if not self.tx_fifo.empty:
                self.output_shift_reg = self.tx_fifo.pull()
                self.out_instruction(self.wait_index)
                self.waiting = False
                self._update_dma_tx()
                self.pio.check_interrupts()

        if not self.waiting:
            self.next_pc()
            self.cycles += self.wait_delay
            self.exec_ctrl &= ~EXECCTRL_EXEC_STALLED
            # Absolute, not a delta - see _state_machine.py's own comment here.
            wait_delay = self.wait_delay
            if wait_delay < 0:
                wait_delay = 0
            self.next_due_fp = <long long>self.pio.cycle_fp + (1 + wait_delay) * self.div_fp
            self.due_rearmed = True
            self.pio.notify_due(self.next_due_fp)
