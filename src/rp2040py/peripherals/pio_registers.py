"""Register offsets, bit-flag constants, and tiny stateless helpers for the RP2040's PIO
peripheral - shared by both `RPPIO` (`pio.py`) and `StateMachine` (`state_machine.py`'s facade
over `_state_machine.py`/`native/_state_machine.pyx`). Split out into its own module (not just
defined in `pio.py`) specifically to avoid a circular import: `pio.py` needs to import
`StateMachine` from the facade, and `StateMachine`'s own implementation needs these same register/
bit constants - if they lived in `pio.py`, `_state_machine.py` importing them back would form a
cycle. Pure constants/helpers only, no classes, so both sides can depend on it safely.
"""

from enum import IntEnum

from rp2040py.peripherals.dma import DREQChannel

__all__ = (
    "CTRL",
    "DBG_CFGINFO",
    "DBG_PADOE",
    "DBG_PADOUT",
    "DREQ_RX0",
    "DREQ_RX1",
    "DREQ_TX0",
    "DREQ_TX1",
    "EXECCTRL_EXEC_STALLED",
    "EXECCTRL_SIDE_EN",
    "EXECCTRL_SIDE_PINDIR",
    "EXECCTRL_STATUS_SEL",
    "FDEBUG",
    "FDEBUG_RXSTALL",
    "FDEBUG_RXUNDER",
    "FDEBUG_TXOVER",
    "FDEBUG_TXSTALL",
    "FLEVEL",
    "FSTAT",
    "FSTAT_RXEMPTY",
    "FSTAT_RXFULL",
    "FSTAT_TXEMPTY",
    "FSTAT_TXFULL",
    "INPUT_SYNC_BYPASS",
    "INSTR_MEM0",
    "INSTR_MEM31",
    "INTR",
    "IRQ",
    "IRQ0_INTE",
    "IRQ0_INTF",
    "IRQ0_INTS",
    "IRQ1_INTE",
    "IRQ1_INTF",
    "IRQ1_INTS",
    "IRQ_FORCE",
    "RXF0",
    "RXF1",
    "RXF2",
    "RXF3",
    "SHIFTCTRL_AUTOPULL",
    "SHIFTCTRL_AUTOPUSH",
    "SHIFTCTRL_IN_SHIFTDIR",
    "SHIFTCTRL_OUT_SHIFTDIR",
    "SM0_ADDR",
    "SM0_CLKDIV",
    "SM0_EXECCTRL",
    "SM0_INSTR",
    "SM0_PINCTRL",
    "SM0_SHIFTCTRL",
    "SM1_CLKDIV",
    "SM1_PINCTRL",
    "SM2_CLKDIV",
    "SM2_PINCTRL",
    "SM3_CLKDIV",
    "SM3_PINCTRL",
    "TXF0",
    "TXF1",
    "TXF2",
    "TXF3",
    "WaitType",
    "bit_reverse",
    "irq_index",
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


def bit_reverse(x: int) -> int:
    x = ((x & 0x55555555) << 1) | ((x & 0xAAAAAAAA) >> 1)
    x = ((x & 0x33333333) << 2) | ((x & 0xCCCCCCCC) >> 2)
    x = ((x & 0x0F0F0F0F) << 4) | ((x & 0xF0F0F0F0) >> 4)
    x = ((x & 0x00FF00FF) << 8) | ((x & 0xFF00FF00) >> 8)
    x = ((x & 0x0000FFFF) << 16) | ((x & 0xFFFF0000) >> 16)
    return x & 0xFFFFFFFF


def irq_index(irq: int, machine_index: int) -> int:
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
