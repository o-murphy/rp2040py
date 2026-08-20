import math
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from rp2040py.peripherals.dma import DREQChannel
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.fifo import FIFO

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040


__all__ = (
    "RPUART",
    "IUARTDMAChannels",
)


def _js_round(value: float) -> int:
    """Mimics JS Math.round (rounds half towards +Infinity)."""
    return math.floor(value + 0.5)


UARTDR = 0x0
UARTFR = 0x18
UARTIBRD = 0x24
UARTFBRD = 0x28
UARTLCR_H = 0x2C
UARTCR = 0x30
UARTIMSC = 0x38
UARTIRIS = 0x3C
UARTIMIS = 0x40
UARTICR = 0x44
UARTPERIPHID0 = 0xFE0
UARTPERIPHID1 = 0xFE4
UARTPERIPHID2 = 0xFE8
UARTPERIPHID3 = 0xFEC
UARTPCELLID0 = 0xFF0
UARTPCELLID1 = 0xFF4
UARTPCELLID2 = 0xFF8
UARTPCELLID3 = 0xFFC

# UARTFR bits:
TXFE = 1 << 7
RXFF = 1 << 6
RXFE = 1 << 4

# UARTLCR_H bits:
FEN = 1 << 4

# UARTCR bits:
RXE = 1 << 9
TXE = 1 << 8
UARTEN = 1 << 0

# Interrupt bits
UARTTXINTR = 1 << 5
UARTRXINTR = 1 << 4

_WORD_LENGTH_BY_LCR_WLEN = {0b00: 5, 0b01: 6, 0b10: 7, 0b11: 8}


class IUARTDMAChannels(NamedTuple):
    rx: DREQChannel
    tx: DREQChannel


class RPUART(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str, irq: int, dreq: IUARTDMAChannels):
        super().__init__(rp2040, name)
        self.irq = irq
        self.dreq = dreq

        self._ctrl_register = RXE | TXE
        self._line_ctrl_register = 0
        self.rx_fifo = FIFO(32)
        self._interrupt_mask = 0
        self._interrupt_status = 0
        self._int_divisor = 0
        self._frac_divisor = 0

        self.on_byte: Callable[[int], None] | None = None
        self.on_baud_rate_change: Callable[[int], None] | None = None

    def reset(self) -> None:
        """Registers and the RX FIFO, back to power-on (0089 Phase 5).

        `on_byte`/`on_baud_rate_change` are **not** touched: they are what a host-side consumer
        (the CLI's UART console, a test) wired to this block, and resetting the chip does not
        unplug the cable. Same rule everywhere in this phase - a reset resets registers, not
        wiring."""
        self._ctrl_register = RXE | TXE
        self._line_ctrl_register = 0
        self.rx_fifo.reset()
        self._interrupt_mask = 0
        self._interrupt_status = 0
        self._int_divisor = 0
        self._frac_divisor = 0
        self.rp2040.set_interrupt(self.irq, False)

    @property
    def enabled(self) -> bool:
        return bool(self._ctrl_register & UARTEN)

    @property
    def tx_enabled(self) -> bool:
        return bool(self._ctrl_register & TXE)

    @property
    def rx_enabled(self) -> bool:
        return bool(self._ctrl_register & RXE)

    @property
    def fifos_enabled(self) -> bool:
        return bool(self._line_ctrl_register & FEN)

    @property
    def word_length(self) -> int:
        """Number of bits per UART character"""
        return _WORD_LENGTH_BY_LCR_WLEN[(self._line_ctrl_register >> 5) & 0x3]

    @property
    def baud_divider(self) -> float:
        return self._int_divisor + self._frac_divisor / 64

    @property
    def baud_rate(self) -> int:
        return _js_round(self.rp2040.clk_peri / (self.baud_divider * 16))

    @property
    def flags(self) -> int:
        return (RXFF if self.rx_fifo.full else 0) | (RXFE if self.rx_fifo.empty else 0) | TXFE

    def check_interrupts(self) -> None:
        # TODO We should actually implement a proper FIFO for TX
        self._interrupt_status |= UARTTXINTR
        self.rp2040.set_interrupt(self.irq, bool(self._interrupt_status & self._interrupt_mask))

    def feed_byte(self, value: int) -> None:
        self.rx_fifo.push(value)
        # TODO check if the FIFO has reached the threshold level
        self._interrupt_status |= UARTRXINTR
        self.check_interrupts()

    def read_uint32(self, offset: int) -> int:
        if offset == UARTDR:
            value = self.rx_fifo.pull()
            if not self.rx_fifo.empty:
                self._interrupt_status |= UARTRXINTR
            else:
                self._interrupt_status &= ~UARTRXINTR
            self.check_interrupts()
            return value
        if offset == UARTFR:
            return self.flags
        if offset == UARTIBRD:
            return self._int_divisor
        if offset == UARTFBRD:
            return self._frac_divisor
        if offset == UARTLCR_H:
            return self._line_ctrl_register
        if offset == UARTCR:
            return self._ctrl_register
        if offset == UARTIMSC:
            return self._interrupt_mask
        if offset == UARTIRIS:
            return self._interrupt_status
        if offset == UARTIMIS:
            return self._interrupt_status & self._interrupt_mask
        if offset == UARTPERIPHID0:
            return 0x11
        if offset == UARTPERIPHID1:
            return 0x10
        if offset == UARTPERIPHID2:
            return 0x34
        if offset == UARTPERIPHID3:
            return 0x00
        if offset == UARTPCELLID0:
            return 0x0D
        if offset == UARTPCELLID1:
            return 0xF0
        if offset == UARTPCELLID2:
            return 0x05
        if offset == UARTPCELLID3:
            return 0xB1
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == UARTDR:
            if self.on_byte:
                self.on_byte(value & 0xFF)

        elif offset == UARTIBRD:
            self._int_divisor = value & 0xFFFF
            if self.on_baud_rate_change:
                self.on_baud_rate_change(self.baud_rate)

        elif offset == UARTFBRD:
            self._frac_divisor = value & 0x3F
            if self.on_baud_rate_change:
                self.on_baud_rate_change(self.baud_rate)

        elif offset == UARTLCR_H:
            self._line_ctrl_register = value

        elif offset == UARTCR:
            self._ctrl_register = value
            if self.enabled:
                self.rp2040.dma.set_dreq(self.dreq.tx)
            else:
                self.rp2040.dma.clear_dreq(self.dreq.tx)

        elif offset == UARTIMSC:
            self._interrupt_mask = value & 0x7FF
            self.check_interrupts()

        elif offset == UARTICR:
            self._interrupt_status &= ~self.raw_write_value
            self.check_interrupts()

        else:
            super().write_uint32(offset, value)
