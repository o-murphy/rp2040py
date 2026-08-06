import asyncio
from enum import IntEnum
from typing import TYPE_CHECKING

from rp2040py.peripherals.dma import DREQChannel
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.fifo import FIFO

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "RPPIO",
    "StateMachine",
    "WaitType",
)

# Generic registers
CTRL = 0x000
FSTAT = 0x004
FDEBUG = 0x008
FLEVEL = 0x00C
IRQ = 0x030
IRQ_FORCE = 0x034
INPUT_SYNC_BYPASS = 0x038
DBG_PADOUT = 0x03C
DBG_PADOE = 0x040
DBG_CFGINFO = 0x044
INSTR_MEM0 = 0x48
INSTR_MEM31 = 0x0C4

INTR = 0x128  # Raw Interrupts
IRQ0_INTE = 0x12C  # Interrupt Enable for irq0
IRQ0_INTF = 0x130  # Interrupt Force for irq0
IRQ0_INTS = 0x134  # Interrupt status after masking & forcing for irq0
IRQ1_INTE = 0x138  # Interrupt Enable for irq1
IRQ1_INTF = 0x13C  # Interrupt Force for irq1
IRQ1_INTS = 0x140  # Interrupt status after masking & forcing for irq1

# State-machine specific registers
TXF0 = 0x010
TXF1 = 0x014
TXF2 = 0x018
TXF3 = 0x01C
RXF0 = 0x020
RXF1 = 0x024
RXF2 = 0x028
RXF3 = 0x02C
SM0_CLKDIV = 0x0C8  # Clock divisor register for state machine 0
SM0_EXECCTRL = 0x0CC  # Execution/behavioural settings for state machine 0
SM0_SHIFTCTRL = 0x0D0  # Control behaviour of the input/output shift registers for state machine 0
SM0_ADDR = 0x0D4  # Current instruction address of state machine 0
SM0_INSTR = 0x0D8  # Write to execute an instruction immediately (including jumps) and then resume execution.
SM0_PINCTRL = 0x0DC  # State machine pin control
SM1_CLKDIV = 0x0E0
SM1_PINCTRL = 0x0F4
SM2_CLKDIV = 0x0F8
SM2_PINCTRL = 0x10C
SM3_CLKDIV = 0x110
SM3_PINCTRL = 0x124

# FSTAT bits
FSTAT_TXEMPTY = 1 << 24
FSTAT_TXFULL = 1 << 16
FSTAT_RXEMPTY = 1 << 8
FSTAT_RXFULL = 1 << 0

# FDEBUG bits
FDEBUG_TXSTALL = 1 << 24
FDEBUG_TXOVER = 1 << 16
FDEBUG_RXUNDER = 1 << 8
FDEBUG_RXSTALL = 1 << 0

# SHIFTCTRL bits
SHIFTCTRL_AUTOPUSH = 1 << 16
SHIFTCTRL_AUTOPULL = 1 << 17
SHIFTCTRL_IN_SHIFTDIR = 1 << 18  # 1 = shift input shift register to right (data enters from left). 0 = to left
SHIFTCTRL_OUT_SHIFTDIR = 1 << 19  # 1 = shift out of output shift register to right. 0 = to left

# EXECCTRL bits
EXECCTRL_STATUS_SEL = 1 << 4
EXECCTRL_SIDE_PINDIR = 1 << 29
EXECCTRL_SIDE_EN = 1 << 30
EXECCTRL_EXEC_STALLED = 1 << 31


class WaitType(IntEnum):
    NONE = 0
    PIN = 1
    RX_FIFO = 2
    TX_FIFO = 3
    IRQ = 4
    OUT = 5  # Out instruction


def _bit_reverse(x: int) -> int:
    x = ((x & 0x55555555) << 1) | ((x & 0xAAAAAAAA) >> 1)
    x = ((x & 0x33333333) << 2) | ((x & 0xCCCCCCCC) >> 2)
    x = ((x & 0x0F0F0F0F) << 4) | ((x & 0xF0F0F0F0) >> 4)
    x = ((x & 0x00FF00FF) << 8) | ((x & 0xFF00FF00) >> 8)
    x = ((x & 0x0000FFFF) << 16) | ((x & 0xFFFF0000) >> 16)
    return x & 0xFFFFFFFF


def _irq_index(irq: int, machine_index: int) -> int:
    rel = bool(irq & 0x10)
    if rel:
        return (irq & 0x4) | (((irq & 0x3) + machine_index) & 0x3)
    return irq & 0x7


DREQ_RX0 = [
    DREQChannel.DREQ_PIO0_RX0,
    DREQChannel.DREQ_PIO0_RX1,
    DREQChannel.DREQ_PIO0_RX2,
    DREQChannel.DREQ_PIO0_RX3,
]
DREQ_TX0 = [
    DREQChannel.DREQ_PIO0_TX0,
    DREQChannel.DREQ_PIO0_TX1,
    DREQChannel.DREQ_PIO0_TX2,
    DREQChannel.DREQ_PIO0_TX3,
]
DREQ_RX1 = [
    DREQChannel.DREQ_PIO1_RX0,
    DREQChannel.DREQ_PIO1_RX1,
    DREQChannel.DREQ_PIO1_RX2,
    DREQChannel.DREQ_PIO1_RX3,
]
DREQ_TX1 = [
    DREQChannel.DREQ_PIO1_TX0,
    DREQChannel.DREQ_PIO1_TX1,
    DREQChannel.DREQ_PIO1_TX2,
    DREQChannel.DREQ_PIO1_TX3,
]


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
        gpio_values = self.rp2040.gpio_values
        in_base = self.in_base
        if in_base:
            return (gpio_values << (32 - in_base)) | (gpio_values >> in_base)
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
                self.wait(WaitType.IRQ, polarity, _irq_index(index, self.index))

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
                irq = _irq_index(arg & 0x1F, self.index)
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
        if self.waiting:
            self.check_wait()
            if self.waiting:
                return

        self.update_pc = True
        self.execute_instruction(self.pio.instructions[self.pc])
        if self.update_pc:
            self.next_pc()

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
            return _bit_reverse(value)
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
        self.input_shift_count = 0
        self.output_shift_count = 32
        self.input_shift_reg = 0
        self.waiting = False
        # TODO any pin write left asserted due to OUT_STICKY.

    def clk_div_restart(self) -> None:
        self.pio.warn("clkDivRestart not implemented")

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


class RPPIO(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str, first_irq: int, index: int):
        super().__init__(rp2040, name)
        self.first_irq = first_irq
        self.index = index

        self.instructions = [0] * 32
        self.dreq_rx = DREQ_RX1 if self.index else DREQ_RX0
        self.dreq_tx = DREQ_TX1 if self.index else DREQ_TX0
        self.machines = [StateMachine(self.rp2040, self, i) for i in range(4)]

        # No lock needed here (there used to be one - see git history / CHANGELOG for the real,
        # reproduced race a background threading.Timer caused): run()'s continuation is now an
        # asyncio coroutine scheduled on Simulator's own engine-room event loop
        # (rp2040py.simulator.Simulator._ensure_loop()), the same loop write_uint32() itself always
        # runs on (it's only ever called from execute_instruction()'s bus dispatch, which only ever
        # runs inside Simulator.execute() on that loop). Exactly one thread can reach this state
        # now, by construction - not "races are unlikely," structurally impossible.
        self._run_task: asyncio.Task[None] | None = None

        self.stopped = True
        self.fdebug = 0
        self.tx_stall = 0
        self.rx_stall = 0
        self.input_sync_bypass = 0
        self.irq = 0
        self.pin_values = 0
        self.pin_directions = 0
        self.old_pin_values = 0
        self.old_pin_directions = 0

        self.irq0_int_enable = 0
        self.irq0_int_force = 0
        self.irq1_int_enable = 0
        self.irq1_int_force = 0

    @property
    def int_raw(self) -> int:
        return (
            ((self.irq & 0xF) << 8)
            | (0x80 if not self.machines[3].tx_fifo.full else 0)
            | (0x40 if not self.machines[2].tx_fifo.full else 0)
            | (0x20 if not self.machines[1].tx_fifo.full else 0)
            | (0x10 if not self.machines[0].tx_fifo.full else 0)
            | (0x08 if not self.machines[3].rx_fifo.empty else 0)
            | (0x04 if not self.machines[2].rx_fifo.empty else 0)
            | (0x02 if not self.machines[1].rx_fifo.empty else 0)
            | (0x01 if not self.machines[0].rx_fifo.empty else 0)
        )

    @property
    def irq0_int_status(self) -> int:
        return (self.int_raw & self.irq0_int_enable) | self.irq0_int_force

    @property
    def irq1_int_status(self) -> int:
        return (self.int_raw & self.irq1_int_enable) | self.irq1_int_force

    def read_uint32(self, offset: int) -> int:
        if SM0_CLKDIV <= offset <= SM0_PINCTRL:
            return self.machines[0].read_uint32(offset - SM0_CLKDIV)
        if SM1_CLKDIV <= offset <= SM1_PINCTRL:
            return self.machines[1].read_uint32(offset - SM1_CLKDIV)
        if SM2_CLKDIV <= offset <= SM2_PINCTRL:
            return self.machines[2].read_uint32(offset - SM2_CLKDIV)
        if SM3_CLKDIV <= offset <= SM3_PINCTRL:
            return self.machines[3].read_uint32(offset - SM3_CLKDIV)

        if offset == CTRL:
            return (
                (1 << 0 if self.machines[0].enabled else 0)
                | (1 << 1 if self.machines[1].enabled else 0)
                | (1 << 2 if self.machines[2].enabled else 0)
                | (1 << 3 if self.machines[3].enabled else 0)
            )
        if offset == FSTAT:
            return (
                self.machines[0].fifo_stat
                | self.machines[1].fifo_stat
                | self.machines[2].fifo_stat
                | self.machines[3].fifo_stat
            )
        if offset == FDEBUG:
            return self.fdebug
        if offset == FLEVEL:
            return (
                (self.machines[0].tx_fifo.item_count & 0xF)
                | ((self.machines[0].rx_fifo.item_count & 0xF) << 4)
                | ((self.machines[1].tx_fifo.item_count & 0xF) << 8)
                | ((self.machines[1].rx_fifo.item_count & 0xF) << 12)
                | ((self.machines[2].tx_fifo.item_count & 0xF) << 16)
                | ((self.machines[2].rx_fifo.item_count & 0xF) << 20)
                | ((self.machines[3].tx_fifo.item_count & 0xF) << 24)
                | ((self.machines[3].rx_fifo.item_count & 0xF) << 28)
            )

        if offset == RXF0:
            return self.machines[0].read_fifo()
        if offset == RXF1:
            return self.machines[1].read_fifo()
        if offset == RXF2:
            return self.machines[2].read_fifo()
        if offset == RXF3:
            return self.machines[3].read_fifo()
        if offset == IRQ:
            return self.irq
        if offset == IRQ_FORCE:
            return 0
        if offset == INPUT_SYNC_BYPASS:
            return self.input_sync_bypass
        if offset == DBG_PADOUT:
            return self.pin_values
        if offset == DBG_PADOE:
            return self.pin_directions
        if offset == DBG_CFGINFO:
            return 0x200404
        if offset == INTR:
            return self.int_raw
        if offset == IRQ0_INTE:
            return self.irq0_int_enable
        if offset == IRQ0_INTF:
            return self.irq0_int_force
        if offset == IRQ0_INTS:
            return self.irq0_int_status
        if offset == IRQ1_INTE:
            return self.irq1_int_enable
        if offset == IRQ1_INTF:
            return self.irq1_int_force
        if offset == IRQ1_INTS:
            return self.irq1_int_status
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if INSTR_MEM0 <= offset <= INSTR_MEM31:
            index = (offset - INSTR_MEM0) >> 2
            self.instructions[index] = value & 0xFFFF
            return
        if SM0_CLKDIV <= offset <= SM0_PINCTRL:
            self.machines[0].write_uint32(offset - SM0_CLKDIV, value)
            return
        if SM1_CLKDIV <= offset <= SM1_PINCTRL:
            self.machines[1].write_uint32(offset - SM1_CLKDIV, value)
            return
        if SM2_CLKDIV <= offset <= SM2_PINCTRL:
            self.machines[2].write_uint32(offset - SM2_CLKDIV, value)
            return
        if SM3_CLKDIV <= offset <= SM3_PINCTRL:
            self.machines[3].write_uint32(offset - SM3_CLKDIV, value)
            return

        if offset == CTRL:
            for index in range(4):
                self.machines[index].enabled = bool(value & (1 << index))
                if value & (1 << (4 + index)):
                    self.machines[index].restart()
                if value & (1 << (8 + index)):
                    self.machines[index].clk_div_restart()
            should_run = value & 0xF
            if self.stopped and should_run:
                self.stopped = False
                # First batch runs synchronously inline, same as always (most PIO programs are
                # short enough that a caller reading FIFO/register state right after this write
                # already sees the result, with no yield in between). Only the continuation - if
                # the program is still going after 1000 steps, e.g. it wraps/waits - needs
                # scheduling as a task: write_uint32() itself can't `await` (it's called
                # synchronously from execute_instruction()'s bus dispatch, which must stay
                # synchronous), so run() picks up from here instead. get_running_loop() always
                # resolves to Simulator's own engine-room loop here, since that's the only place
                # write_uint32() is ever called from (see __init__'s _run_task comment) - except
                # tests driving RPPIO directly with no Simulator at all, which need their own loop
                # running for this specific case (see tests/test_pio.py's fixture).
                self._step_batch()
                if not self.stopped:
                    self._run_task = asyncio.get_running_loop().create_task(self.run())
            if not should_run:
                self.stopped = True

        elif offset == FDEBUG:
            self.fdebug &= ~self.raw_write_value
            self.fdebug |= self.tx_stall | self.rx_stall

        elif offset == TXF0:
            self.machines[0].write_fifo(value)
        elif offset == TXF1:
            self.machines[1].write_fifo(value)
        elif offset == TXF2:
            self.machines[2].write_fifo(value)
        elif offset == TXF3:
            self.machines[3].write_fifo(value)

        elif offset == IRQ:
            self.irq &= ~self.raw_write_value
            self.irq_updated()

        elif offset == INPUT_SYNC_BYPASS:
            self.input_sync_bypass = value

        elif offset == IRQ_FORCE:
            self.irq |= value
            self.irq_updated()

        elif offset == IRQ0_INTE:
            self.irq0_int_enable = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ0_INTF:
            self.irq0_int_force = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ1_INTE:
            self.irq1_int_enable = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ1_INTF:
            self.irq1_int_force = value & 0xFFF
            self.check_interrupts()

        else:
            super().write_uint32(offset, value)

    def pin_values_changed(self, value: int, first_pin: int, count: int) -> None:
        # TODO: wrapping after pin 31
        mask = 0xFFFFFFFF if count > 31 else ((1 << count) - 1) << first_pin
        new_value = ((self.pin_values & ~mask) | ((value << first_pin) & mask)) & 0x3FFFFFFF
        self.pin_values = new_value

    def pin_directions_changed(self, value: int, first_pin: int, count: int) -> None:
        # TODO: wrapping after pin 31
        mask = 0xFFFFFFFF if count > 31 else ((1 << count) - 1) << first_pin
        new_value = ((self.pin_directions & ~mask) | ((value << first_pin) & mask)) & 0x3FFFFFFF
        self.pin_directions = new_value

    def check_interrupts(self) -> None:
        first_irq = self.first_irq
        self.rp2040.set_interrupt(first_irq, bool(self.irq0_int_status))
        self.rp2040.set_interrupt(first_irq + 1, bool(self.irq1_int_status))

    def irq_updated(self) -> None:
        for machine in self.machines:
            machine.check_wait()
        self.check_interrupts()

    def check_changed_pins(self) -> None:
        changed_pins = (self.old_pin_directions ^ self.pin_directions) | (self.old_pin_values ^ self.pin_values)
        if changed_pins:
            self.old_pin_directions = self.pin_directions
            self.old_pin_values = self.pin_values

            # Notify GPIO about the changed pins
            gpio = self.rp2040.gpio
            for gpio_index in range(len(gpio)):
                if changed_pins & (1 << gpio_index):
                    gpio[gpio_index].check_for_updates()

    def step(self) -> None:
        for machine in self.machines:
            machine.step()
        self.check_changed_pins()

    def _step_batch(self) -> None:
        i = 0
        while i < 1000 and not self.stopped:
            self.step()
            i += 1

    async def run(self) -> None:
        # Continuation of the first batch write_uint32()'s CTRL branch already ran synchronously -
        # this only exists to keep making progress past that first 1000 steps without blocking
        # whatever's driving Simulator.execute() (the only thing that ever schedules this, see
        # __init__'s _run_task comment). Upstream rp2040js uses `setTimeout(() => this.run(), 0)`
        # for the same reason, to yield back to the single-threaded JS event loop every 1000 steps
        # so external stop() calls get a chance to run - `await asyncio.sleep(0)` is the direct
        # analogue here, on Simulator's own engine-room loop rather than JS's single process-wide
        # one.
        while not self.stopped:
            await asyncio.sleep(0)
            self._step_batch()

    def stop(self) -> None:
        for machine in self.machines:
            machine.enabled = False
        self.stopped = True
        # No task cancellation needed - run() notices self.stopped on its next iteration, same
        # "benign staleness, not corruption" tradeoff this always made (one extra scheduled batch
        # may still run).
