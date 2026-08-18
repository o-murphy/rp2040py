"""Pure-Python reference implementation of `StateMachine` - the PIO peripheral's actual per-cycle
interpreter (opcode decode, shift-register math, wait/jmp condition checks). `state_machine.py` is
the public facade that picks this or the native `rp2040py.native._state_machine` port; callers
should import `StateMachine` from there (or from `rp2040py`/`rp2040py.peripherals.pio`, which
re-export it), never from here directly - same relationship `_cortex_m0_core.py` has to
`cortex_m0_core.py`.
"""

from typing import TYPE_CHECKING

from rp2040py.peripherals.pio_registers import (
    EXECCTRL_EXEC_STALLED,
    EXECCTRL_SIDE_EN,
    EXECCTRL_SIDE_PINDIR,
    EXECCTRL_STATUS_SEL,
    FDEBUG_RXSTALL,
    FDEBUG_RXUNDER,
    FDEBUG_TXOVER,
    FDEBUG_TXSTALL,
    FSTAT_RXEMPTY,
    FSTAT_RXFULL,
    FSTAT_TXEMPTY,
    FSTAT_TXFULL,
    SHIFTCTRL_AUTOPULL,
    SHIFTCTRL_AUTOPUSH,
    SHIFTCTRL_IN_SHIFTDIR,
    SHIFTCTRL_OUT_SHIFTDIR,
    SM0_ADDR,
    SM0_CLKDIV,
    SM0_EXECCTRL,
    SM0_INSTR,
    SM0_PINCTRL,
    SM0_SHIFTCTRL,
    WaitType,
    bit_reverse,
    irq_index,
)
from rp2040py.utils.fifo import FIFO

if TYPE_CHECKING:
    from rp2040py.peripherals.pio import RPPIO
    from rp2040py.rp2040 import RP2040

__all__ = ("StateMachine",)


class StateMachine:
    def __init__(self, rp2040: "RP2040", pio: "RPPIO", index: int):
        self.rp2040 = rp2040
        self.pio = pio
        self.index = index

        self.enabled = False

        # State machine registers
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
        # `SM_CLKDIV` as a single 16.8 fixed-point divisor in 1/256ths of a system clock, kept in
        # sync with the two halves above by every write that touches them (`div_fp` is what the
        # pacing below actually multiplies by - the halves stay because the register reads them
        # back). `CLKDIV_INT == 0` means /65536 on real hardware, not /0.
        self.div_fp = 1 << 8
        # Absolute system-clock cycle, in the same 1/256ths, at which this machine may execute its
        # next instruction - the "due time" `RPPIO.advance()` compares against. Meaningless while
        # `enabled` is False or `waiting` is True (both are filtered before it is read); re-armed
        # from the current cycle when either of those ends. See docs/records/0063.
        self.next_due_fp = 0
        # Set by check_wait() when it re-arms next_due_fp itself, so step() knows not to also add
        # this instruction's own cycles on top of an already-absolute due time.
        self.due_rearmed = False
        self.exec_ctrl = 0x1F << 12
        self.shift_ctrl = 0b11 << 18
        self.pin_ctrl = 0x5 << 26
        self.rx_fifo = FIFO(4)
        self.tx_fifo = FIFO(4)

        self.out_pin_values = 0
        self.out_pin_direction = 0

        self.waiting = False
        self.wait_type = WaitType.NONE
        self.wait_index = 0
        self.wait_polarity = False
        self.wait_delay = -1

        self.dreq_rx = self.pio.dreq_rx[self.index]
        self.dreq_tx = self.pio.dreq_tx[self.index]

        self._update_dma_rx()
        self._update_dma_tx()

    def _update_dma_tx(self) -> None:
        if self.tx_fifo.full:
            self.rp2040.dma.clear_dreq(self.dreq_tx)
        else:
            self.rp2040.dma.set_dreq(self.dreq_tx)

    def _update_dma_rx(self) -> None:
        if self.rx_fifo.empty:
            self.rp2040.dma.clear_dreq(self.dreq_rx)
        else:
            self.rp2040.dma.set_dreq(self.dreq_rx)

    def write_fifo(self, value: int) -> None:
        if self.tx_fifo.full:
            self.pio.fdebug |= FDEBUG_TXOVER << self.index
            return
        self.tx_fifo.push(value)
        self.pio.tx_stall &= ~(FDEBUG_TXSTALL << self.index)
        self._update_dma_tx()
        self.check_wait()
        if self.tx_fifo.full:
            self.pio.check_interrupts()

    def read_fifo(self) -> int:
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
    def status(self) -> int:
        status_n = self.exec_ctrl & 0xF
        if self.exec_ctrl & EXECCTRL_STATUS_SEL:
            return 0xFFFFFFFF if self.rx_fifo.item_count < status_n else 0
        return 0xFFFFFFFF if self.tx_fifo.item_count < status_n else 0

    def jmp_condition(self, condition: int) -> bool:
        # (no condition): Always
        if condition == 0b000:
            return True

        # !X: scratch X zero
        if condition == 0b001:
            return self.x == 0

        # X--: scratch X non-zero, post-decrement
        if condition == 0b010:
            old_x = self.x
            self.x = (self.x - 1) & 0xFFFFFFFF
            return old_x != 0

        # !Y: scratch Y zero
        if condition == 0b011:
            return self.y == 0

        # Y--: scratch Y non-zero, post-decrement
        if condition == 0b100:
            old_y = self.y
            self.y = (self.y - 1) & 0xFFFFFFFF
            return old_y != 0

        # X!=Y: scratch X not equal scratch Y
        if condition == 0b101:
            return (self.x & 0xFFFFFFFF) != (self.y & 0xFFFFFFFF)

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
    def in_pins(self) -> int:
        # A 32-bit rotate-right by in_base (real hardware reads GPIOs starting at in_base,
        # wrapping around the 32-bit bus) - the `& 0xFFFFFFFF` matters: found via the native port
        # (docs/... perf side quest), whose typed `unsigned int` return raised OverflowError where
        # this Python version silently carried extra high bits in its arbitrary-precision int
        # instead (invisible here, but the IN instruction's own bit_count==0 case stores whatever
        # in_source_value() returns into input_shift_reg with no masking of its own - a real, if
        # narrow, latent bug this closes rather than a native-port workaround).
        gpio_values = self.rp2040.gpio_values
        in_base = self.in_base
        if in_base:
            return ((gpio_values << (32 - in_base)) | (gpio_values >> in_base)) & 0xFFFFFFFF
        return gpio_values

    def in_source_value(self, source: int) -> int:
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

    def write_out_value(self, destination: int, value: int, bit_count: int) -> None:
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
    def push_threshold(self) -> int:
        value = (self.shift_ctrl >> 20) & 0x1F
        return value if value else 32

    @property
    def pull_threshold(self) -> int:
        value = (self.shift_ctrl >> 25) & 0x1F
        return value if value else 32

    @property
    def sideset_count(self) -> int:
        return (self.pin_ctrl >> 29) & 0x7

    @property
    def set_count(self) -> int:
        return (self.pin_ctrl >> 26) & 0x7

    @property
    def out_count(self) -> int:
        return (self.pin_ctrl >> 20) & 0x3F

    @property
    def in_base(self) -> int:
        return (self.pin_ctrl >> 15) & 0x1F

    @property
    def sideset_base(self) -> int:
        return (self.pin_ctrl >> 10) & 0x1F

    @property
    def set_base(self) -> int:
        return (self.pin_ctrl >> 5) & 0x1F

    @property
    def out_base(self) -> int:
        return (self.pin_ctrl >> 0) & 0x1F

    @property
    def jmp_pin(self) -> int:
        return (self.exec_ctrl >> 24) & 0x1F

    @property
    def wrap_top(self) -> int:
        return (self.exec_ctrl >> 12) & 0x1F

    @property
    def wrap_bottom(self) -> int:
        return (self.exec_ctrl >> 7) & 0x1F

    def set_out_pin_dirs(self, value: int) -> None:
        self.out_pin_direction = value
        self.pio.pin_directions_changed(value, self.out_base, self.out_count)

    def set_out_pins(self, value: int) -> None:
        self.out_pin_values = value
        self.pio.pin_values_changed(value, self.out_base, self.out_count)

    def out_instruction(self, arg: int) -> None:
        bit_count = arg & 0x1F
        destination = arg >> 5

        if bit_count == 0:
            self.write_out_value(destination, self.output_shift_reg, 32)
            self.output_shift_count = 32
        else:
            if self.shift_ctrl & SHIFTCTRL_OUT_SHIFTDIR:
                value = self.output_shift_reg & ((1 << bit_count) - 1)
                self.output_shift_reg = (self.output_shift_reg & 0xFFFFFFFF) >> bit_count
                self.write_out_value(destination, value, bit_count)
            else:
                value = (self.output_shift_reg & 0xFFFFFFFF) >> (32 - bit_count)
                self.output_shift_reg <<= bit_count
                self.write_out_value(destination, value, bit_count)
            self.output_shift_count += bit_count
            self.output_shift_count = min(self.output_shift_count, 32)

    def execute_instruction(self, opcode: int) -> None:
        arg = opcode & 0xFF
        instruction = opcode >> 13

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
                self.wait(WaitType.PIN, polarity, index)
            # PIN:
            elif source == 0b01:
                self.wait(WaitType.PIN, polarity, (index + self.in_base) % 32)
            # IRQ:
            elif source == 0b10:
                self.wait(WaitType.IRQ, polarity, irq_index(index, self.index))

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
                    self.input_shift_reg = (self.input_shift_reg & 0xFFFFFFFF) >> bit_count
                    self.input_shift_reg |= source_value << (32 - bit_count)
                else:
                    self.input_shift_reg <<= bit_count
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
                    self.wait(WaitType.RX_FIFO, False, self.input_shift_reg)
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
                    self.wait(WaitType.OUT, False, arg)

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
                            self.wait(WaitType.TX_FIFO, False, 0)
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
                            self.wait(WaitType.RX_FIFO, False, self.input_shift_reg)
                    self.input_shift_reg = 0
                    self.input_shift_count = 0

        # MOV
        elif instruction == 0b101:
            source = arg & 0x7
            op = (arg >> 3) & 0x3
            destination = (arg >> 5) & 0x7
            value = self.in_source_value(source)
            transformed_value = self.transform_mov_value(value, op) & 0xFFFFFFFF
            self.set_mov_destination(destination, transformed_value)

        # IRQ
        elif instruction == 0b110:
            if arg & 0x80:
                pass  # Unknown instruction
            else:
                clear = bool(arg & 0x40)
                wait = bool(arg & 0x20)
                irq = irq_index(arg & 0x1F, self.index)
                if clear:
                    self.pio.irq &= ~(1 << irq)
                    self.pio.irq_updated()
                else:
                    self.pio.irq |= 1 << irq
                    self.pio.irq_updated()
                    if wait:
                        self.wait(WaitType.IRQ, False, irq)

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

    def wait(self, wait_type: WaitType, polarity: bool, index: int) -> None:
        self.waiting = True
        self.wait_type = wait_type
        self.wait_polarity = polarity
        self.wait_index = index
        self.wait_delay = -1
        self.update_pc = False

    def next_pc(self) -> None:
        if self.pc == self.wrap_top:
            self.pc = self.wrap_bottom
        else:
            self.pc = (self.pc + 1) & 0x1F

    def step(self) -> None:
        """Executes one instruction and re-arms `next_due_fp` for the next one.

        `RPPIO.advance()` is what decides *when* this runs (docs/records/0063): it only calls this
        on a machine whose due time has arrived, so the pacing lives entirely in the two fields
        this maintains. The cycles the instruction actually costs are read straight off `cycles`,
        which `execute_instruction()` already accumulated correctly (1 for the instruction plus
        its `[delay]`, or nothing extra when it stalled) - the divider turns those PIO cycles into
        system-clock ones. `check_wait()` re-arming `next_due_fp` absolutely (an unstall lands on
        the cycle the condition became true, not on a delta from when the stall began) wins over
        the delta, which is what `due_rearmed` says."""
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

    def set_set_pin_dirs(self, value: int) -> None:
        self.pio.pin_directions_changed(value, self.set_base, self.set_count)

    def set_set_pins(self, value: int) -> None:
        self.pio.pin_values_changed(value, self.set_base, self.set_count)

    def set_sideset(self, value: int, count: int) -> None:
        if self.exec_ctrl & EXECCTRL_SIDE_PINDIR:
            self.pio.pin_directions_changed(value, self.sideset_base, count)
        else:
            self.pio.pin_values_changed(value, self.sideset_base, count)

    def transform_mov_value(self, value: int, op: int) -> int:
        if op == 0b00:
            return value
        if op == 0b01:
            return ~value
        if op == 0b10:
            return bit_reverse(value)
        # 0b11 and any other value: reserved
        return value

    def set_mov_destination(self, destination: int, value: int) -> None:
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

    def read_uint32(self, offset: int) -> int:
        absolute_offset = offset + SM0_CLKDIV
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

    def write_uint32(self, offset: int, value: int) -> None:
        absolute_offset = offset + SM0_CLKDIV
        if absolute_offset == SM0_CLKDIV:
            self.clock_div_frac = (value >> 8) & 0xFF
            self.clock_div_int = value >> 16
            # CLKDIV_INT == 0 divides by 65536 (RP2040 datasheet 3.5.5), not by zero.
            self.div_fp = ((self.clock_div_int or 65536) << 8) | self.clock_div_frac
            self.pio.recompute_due()
        elif absolute_offset == SM0_EXECCTRL:
            self.exec_ctrl = ((value & 0x7FFFFFFF) | (self.exec_ctrl & 0x80000000)) & 0xFFFFFFFF
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
    def fifo_stat(self) -> int:
        result = (
            (FSTAT_TXEMPTY if self.tx_fifo.empty else 0)
            | (FSTAT_TXFULL if self.tx_fifo.full else 0)
            | (FSTAT_RXEMPTY if self.rx_fifo.empty else 0)
            | (FSTAT_RXFULL if self.rx_fifo.full else 0)
        )
        return result << self.index

    def restart(self) -> None:
        self.cycles = 0
        self.next_due_fp = self.pio.cycle_fp
        self.input_shift_count = 0
        self.output_shift_count = 32
        self.input_shift_reg = 0
        self.waiting = False
        # TODO any pin write left asserted due to OUT_STICKY.

    def clk_div_restart(self) -> None:
        """`CTRL.CLKDIV_RESTART`: restarts the clock divider "from an initial phase of 0" (RP2040
        datasheet 3.5.6), so the next PIO cycle is a whole divider period away rather than wherever
        the fractional accumulator happened to have got to. Was a "not implemented" warning for as
        long as nothing paced anything by the divider at all (docs/records/0063)."""
        self.next_due_fp = self.pio.cycle_fp + self.div_fp

    def check_wait(self) -> None:
        if not self.waiting:
            return

        if self.wait_type == WaitType.IRQ:
            irq_value = bool(self.pio.irq & (1 << self.wait_index))
            if irq_value == self.wait_polarity:
                self.waiting = False
                if irq_value:
                    self.pio.irq &= ~(1 << self.wait_index)

        elif self.wait_type == WaitType.PIN:
            gpio = self.rp2040.gpio
            if self.wait_index < len(gpio) and gpio[self.wait_index].input_value == self.wait_polarity:
                self.waiting = False

        elif self.wait_type == WaitType.RX_FIFO:
            if not self.rx_fifo.full:
                self.rx_fifo.push(self.wait_index)
                self.waiting = False
                self._update_dma_rx()
                self.pio.check_interrupts()

        elif self.wait_type == WaitType.TX_FIFO:
            if not self.tx_fifo.empty:
                self.output_shift_reg = self.tx_fifo.pull()
                self.waiting = False
                self._update_dma_tx()
                self.pio.check_interrupts()

        elif self.wait_type == WaitType.OUT:
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
            # A stall ends on the cycle its condition becomes true, whenever that is - so the due
            # time is absolute (from *now*), not a delta from whenever the stall started: one PIO
            # cycle to retire the stalled instruction, then its own `[delay]`. `wait_delay` is -1
            # until execute_instruction() resolves it, which is why it is clamped here.
            wait_delay = self.wait_delay
            wait_delay = max(wait_delay, 0)
            self.next_due_fp = self.pio.cycle_fp + (1 + wait_delay) * self.div_fp
            self.due_rearmed = True
            self.pio.notify_due(self.next_due_fp)
