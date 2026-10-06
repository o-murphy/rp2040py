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


def _baud_rate(clk_peri: float, baud_divider: float) -> int:
    """The baud rate for a peripheral clock and a divider; also what the native UART's shell calls, so the rounding and a zero divider's `ZeroDivisionError`
    (the message included) are the same on both sides by construction."""
    return _js_round(clk_peri / (baud_divider * 16))


UARTDR = 0x0
UARTRSR = 0x4  # reads the receive status (OE, BE, PE, FE); a write is UARTECR and clears it
UARTFR = 0x18
UARTILPR = 0x20
UARTIBRD = 0x24
UARTFBRD = 0x28
UARTLCR_H = 0x2C
UARTCR = 0x30
UARTIFLS = 0x34
UARTIMSC = 0x38
UARTIRIS = 0x3C
UARTIMIS = 0x40
UARTICR = 0x44
UARTDMACR = 0x48
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
LBE = 1 << 7

# Interrupt bits
UARTOEINTR = 1 << 10
UARTTXINTR = 1 << 5
UARTRXINTR = 1 << 4
UART_ERROR_INTERRUPTS = 0x780  # UARTOEINTR, UARTBEINTR, UARTPEINTR, UARTFEINTR

# UARTRSR bits
RSR_OE = 1 << 3

# UARTDMACR bits
DMACR_DMAONERR = 1 << 2
DMACR_TXDMAE = 1 << 1
DMACR_RXDMAE = 1 << 0

# What each register keeps (RP2040 datasheet, 4.2.8): the rest is reserved
LCR_H_MASK = 0xFF  # SPS, WLEN, FEN, STP2, EPS, PEN, BRK
CR_MASK = 0xFF87  # CTSEN .. LBE (15:7), SIRLP, SIREN, UARTEN; 6:3 are reserved
IFLS_MASK = 0x3F  # RXIFLSEL 5:3, TXIFLSEL 2:0
IFLS_RESET = 0x12  # both b010: 1/2 full
DMACR_MASK = 0x7
ILPR_MASK = 0xFF

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
        # Stored, not acted on: the RX interrupt comes with every byte (a superset of any trigger level, so a driver that drains the FIFO in its handler works the same), the TX FIFO never
        # fills, and there is no IrDA. They read back what was written, as the datasheet's registers do.
        self._ifls = IFLS_RESET
        self._ilpr = 0
        self._dmacr = 0
        self._rsr = 0

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
        self._ifls = IFLS_RESET
        self._ilpr = 0
        self._dmacr = 0
        self._rsr = 0
        self.rp2040.set_interrupt(self.irq, False)
        self._update_dreq()

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
        return _baud_rate(self.rp2040.clk_peri, self.baud_divider)

    def clk_peri_changed(self) -> None:
        """The peripheral clock changed: re-announce the baud rate, unless the firmware has not set the divider yet (rp2040js 1.4.0)."""
        if self.baud_divider and self.on_baud_rate_change:
            self.on_baud_rate_change(self.baud_rate)

    @property
    def flags(self) -> int:
        return (RXFF if self.rx_fifo.full else 0) | (RXFE if self.rx_fifo.empty else 0) | TXFE

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(self.irq, bool(self._interrupt_status & self._interrupt_mask))

    def _update_dreq(self) -> None:
        """The two DMA requests. TX: the transmit FIFO never fills, so it is asking whenever the transmitter is enabled and TXDMAE is set. RX: whenever the receiver is enabled, RXDMAE is set
        and the FIFO holds a byte - unless DMAONERR is set and an error interrupt is up ("the DMA receive request outputs ... are disabled when the UART error interrupt is asserted")."""
        ready = self.enabled
        tx = ready and self.tx_enabled and bool(self._dmacr & DMACR_TXDMAE)
        rx = (
            ready
            and self.rx_enabled
            and bool(self._dmacr & DMACR_RXDMAE)
            and not self.rx_fifo.empty
            and not (self._dmacr & DMACR_DMAONERR and self._interrupt_status & UART_ERROR_INTERRUPTS)
        )
        (self.rp2040.dma.set_dreq if tx else self.rp2040.dma.clear_dreq)(self.dreq.tx)
        (self.rp2040.dma.set_dreq if rx else self.rp2040.dma.clear_dreq)(self.dreq.rx)

    def feed_byte(self, value: int) -> None:
        # "RXE: Receive enable": a disabled receiver (or UART) takes nothing from the line
        if not (self.enabled and self.rx_enabled):
            return
        if self.rx_fifo.full:
            # "OE: Overrun error. This bit is set to 1 if data is received and the FIFO is already full ... no more data is written when the FIFO is full"
            self._rsr |= RSR_OE
            self._interrupt_status |= UARTOEINTR
        else:
            self.rx_fifo.push(value)
            # The RX interrupt is not held back until a trigger level (UARTIFLS): see the note on `_ifls`
            self._interrupt_status |= UARTRXINTR
        self.check_interrupts()
        self._update_dreq()

    def read_uint32(self, offset: int) -> int:
        if offset == UARTDR:
            value = self.rx_fifo.pull()
            if not self.rx_fifo.empty:
                self._interrupt_status |= UARTRXINTR
            else:
                self._interrupt_status &= ~UARTRXINTR
            self.check_interrupts()
            self._update_dreq()
            return value
        if offset == UARTRSR:
            return self._rsr
        if offset == UARTFR:
            return self.flags
        if offset == UARTILPR:
            return self._ilpr
        if offset == UARTIBRD:
            return self._int_divisor
        if offset == UARTFBRD:
            return self._frac_divisor
        if offset == UARTLCR_H:
            return self._line_ctrl_register
        if offset == UARTCR:
            return self._ctrl_register
        if offset == UARTIFLS:
            return self._ifls
        if offset == UARTDMACR:
            return self._dmacr
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
            # "TXE: Transmit enable": a disabled transmitter (or UART) sends nothing - the byte goes nowhere, as the FIFO behind it is never read
            if self.enabled and self.tx_enabled:
                if self._ctrl_register & LBE:
                    # "LBE: Loop back enable ... the UARTTXD path is fed back to the UARTRXD path": the byte reaches the receiver, not the device
                    self.feed_byte(value & 0xFF)
                elif self.on_byte:
                    self.on_byte(value & 0xFF)
                # The byte leaves the (never-filling) TX FIFO at once; the PL011 TX interrupt is
                # edge-like - set by that, never while the FIFO merely stays empty - so UARTICR clears
                # it for good instead of it being re-asserted (an IRQ storm in the guest otherwise).
                self._interrupt_status |= UARTTXINTR
                self.check_interrupts()

        elif offset == UARTRSR:
            self._rsr = 0  # the write is UARTECR: "cleared to 0 by a write to UARTECR"

        elif offset == UARTILPR:
            self._ilpr = value & ILPR_MASK

        elif offset == UARTIBRD:
            self._int_divisor = value & 0xFFFF
            if self.on_baud_rate_change:
                self.on_baud_rate_change(self.baud_rate)

        elif offset == UARTFBRD:
            self._frac_divisor = value & 0x3F
            if self.on_baud_rate_change:
                self.on_baud_rate_change(self.baud_rate)

        elif offset == UARTLCR_H:
            self._line_ctrl_register = value & LCR_H_MASK

        elif offset == UARTCR:
            self._ctrl_register = value & CR_MASK
            self._update_dreq()

        elif offset == UARTIFLS:
            self._ifls = value & IFLS_MASK

        elif offset == UARTDMACR:
            self._dmacr = value & DMACR_MASK
            self._update_dreq()

        elif offset == UARTIMSC:
            self._interrupt_mask = value & 0x7FF
            self.check_interrupts()

        elif offset == UARTICR:
            self._interrupt_status &= ~self.raw_write_value
            self.check_interrupts()
            self._update_dreq()  # DMAONERR: clearing the error interrupt lets the receive request through again

        else:
            super().write_uint32(offset, value)
