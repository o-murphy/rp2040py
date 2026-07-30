from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from rp2040py.peripherals.dma import DREQChannel
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.fifo import FIFO

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "RPSPI",
    "ISPIDMAChannels",
)

SSPCR0 = 0x000  # Control register 0, SSPCR0 on page 3-4
SSPCR1 = 0x004  # Control register 1, SSPCR1 on page 3-5
SSPDR = 0x008  # Data register, SSPDR on page 3-6
SSPSR = 0x00C  # Status register, SSPSR on page 3-7
SSPCPSR = 0x010  # Clock prescale register, SSPCPSR on page 3-8
SSPIMSC = 0x014  # Interrupt mask set or clear register, SSPIMSC on page 3-9
SSPRIS = 0x018  # Raw interrupt status register, SSPRIS on page 3-10
SSPMIS = 0x01C  # Masked interrupt status register, SSPMIS on page 3-11
SSPICR = 0x020  # Interrupt clear register, SSPICR on page 3-11
SSPDMACR = 0x024  # DMA control register, SSPDMACR on page 3-12
SSPPERIPHID0 = 0xFE0  # Peripheral identification registers, SSPPeriphID0-3 on page 3-13
SSPPERIPHID1 = 0xFE4  # Peripheral identification registers, SSPPeriphID0-3 on page 3-13
SSPPERIPHID2 = 0xFE8  # Peripheral identification registers, SSPPeriphID0-3 on page 3-13
SSPPERIPHID3 = 0xFEC  # Peripheral identification registers, SSPPeriphID0-3 on page 3-13
SSPPCELLID0 = 0xFF0  # PrimeCell identification registers, SSPPCellID0-3 on page 3-16
SSPPCELLID1 = 0xFF4  # PrimeCell identification registers, SSPPCellID0-3 on page 3-16
SSPPCELLID2 = 0xFF8  # PrimeCell identification registers, SSPPCellID0-3 on page 3-16
SSPPCELLID3 = 0xFFC  # PrimeCell identification registers, SSPPCellID0-3 on page 3-16

# SSPCR0 bits:
SCR_MASK = 0xFF
SCR_SHIFT = 8
SPH = 1 << 7
SPO = 1 << 6
FRF_MASK = 0x3
FRF_SHIFT = 4
DSS_MASK = 0xF
DSS_SHIFT = 0

# SSPCR1 bits:
SOD = 1 << 3
MS = 1 << 2
SSE = 1 << 1
LBM = 1 << 0

# SSPSR bits:
BSY = 1 << 4
RFF = 1 << 3
RNE = 1 << 2
TNF = 1 << 1
TFE = 1 << 0

# SSPCPSR bits:
CPSDVSR_MASK = 0xFE
CPSDVSR_SHIFT = 0

# SSPDMACR bits:
TXDMAE = 1 << 1
RXDMAE = 1 << 0

# Interrupts:
SSPTXINTR = 1 << 3
SSPRXINTR = 1 << 2
SSPRTINTR = 1 << 1
SSPRORINTR = 1 << 0


class ISPIDMAChannels(NamedTuple):
    rx: DREQChannel
    tx: DREQChannel


class RPSPI(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str, irq: int, dreq: ISPIDMAChannels):
        super().__init__(rp2040, name)
        self.rx_fifo = FIFO(8)
        self.tx_fifo = FIFO(8)
        self.irq = irq
        self.dreq = dreq

        # User provided callback
        self.on_transmit: Callable[[int], None] = lambda value: self.complete_transmit(0)

        self._busy = False
        self._control0 = 0
        self._control1 = 0
        self._dma_control = 0
        self._clock_divisor = 0
        self._int_raw = 0
        self._int_enable = 0

        self._update_dma_tx()
        self._update_dma_rx()

    @property
    def int_status(self) -> int:
        return self._int_raw & self._int_enable

    @property
    def enabled(self) -> bool:
        return bool(self._control1 & SSE)

    @property
    def data_bits(self) -> int:
        """Data size in bits: 4 to 16 bits"""
        return ((self._control0 >> DSS_SHIFT) & DSS_MASK) + 1

    @property
    def master_mode(self) -> bool:
        return not (self._control0 & MS)

    @property
    def spi_mode(self) -> int:
        cpol = self._control0 & SPO
        cpha = self._control0 & SPH
        if cpol:
            return 2 if cpha else 3
        return 1 if cpha else 0

    @property
    def clock_frequency(self) -> float:
        if not self._clock_divisor:
            return 0

        scr = (self._control0 >> SCR_SHIFT) & SCR_MASK
        return self.rp2040.clk_peri / (self._clock_divisor * (1 + scr))

    def _update_dma_tx(self) -> None:
        if self.tx_fifo.full:
            self.rp2040.dma.clear_dreq(self.dreq.tx)
        else:
            self.rp2040.dma.set_dreq(self.dreq.tx)

    def _update_dma_rx(self) -> None:
        if self.rx_fifo.empty:
            self.rp2040.dma.clear_dreq(self.dreq.rx)
        else:
            self.rp2040.dma.set_dreq(self.dreq.rx)

    def _do_tx(self) -> None:
        if not self._busy and not self.tx_fifo.empty:
            value = self.tx_fifo.pull()
            self._busy = True
            self.on_transmit(value)
            self._fifos_updated()

    def complete_transmit(self, rx_value: int) -> None:
        self._busy = False
        if not self.rx_fifo.full:
            self.rx_fifo.push(rx_value)
        else:
            self._int_raw |= SSPRORINTR
        self._fifos_updated()
        self._do_tx()

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(self.irq, bool(self.int_status))

    def _fifos_updated(self) -> None:
        prev_status = self.int_status
        if self.tx_fifo.item_count <= self.tx_fifo.size / 2:
            self._int_raw |= SSPTXINTR
        else:
            self._int_raw &= ~SSPTXINTR
        if self.rx_fifo.item_count >= self.rx_fifo.size / 2:
            self._int_raw |= SSPRXINTR
        else:
            self._int_raw &= ~SSPRXINTR
        if self.int_status != prev_status:
            self.check_interrupts()

        self._update_dma_tx()
        self._update_dma_rx()

    def read_uint32(self, offset: int) -> int:
        if offset == SSPCR0:
            return self._control0
        if offset == SSPCR1:
            return self._control1
        if offset == SSPDR:
            if not self.rx_fifo.empty:
                value = self.rx_fifo.pull()
                self._fifos_updated()
                return value
            return 0
        if offset == SSPSR:
            return (
                (BSY if (self._busy or not self.tx_fifo.empty) else 0)
                | (RFF if self.rx_fifo.full else 0)
                | (RNE if not self.rx_fifo.empty else 0)
                | (TNF if not self.tx_fifo.full else 0)
                | (TFE if self.tx_fifo.empty else 0)
            )
        if offset == SSPCPSR:
            return self._clock_divisor
        if offset == SSPIMSC:
            return self._int_enable
        if offset == SSPRIS:
            return self._int_raw
        if offset == SSPMIS:
            return self.int_status
        if offset == SSPDMACR:
            return self._dma_control
        if offset == SSPPERIPHID0:
            return 0x22
        if offset == SSPPERIPHID1:
            return 0x10
        if offset == SSPPERIPHID2:
            return 0x34
        if offset == SSPPERIPHID3:
            return 0x00
        if offset == SSPPCELLID0:
            return 0x0D
        if offset == SSPPCELLID1:
            return 0xF0
        if offset == SSPPCELLID2:
            return 0x05
        if offset == SSPPCELLID3:
            return 0xB1
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == SSPCR0:
            self._control0 = value
        elif offset == SSPCR1:
            self._control1 = value
        elif offset == SSPDR:
            if not self.tx_fifo.full:
                # decoded with respect to SSPCR0.DSS
                self.tx_fifo.push(value & ((1 << self.data_bits) - 1))
                self._do_tx()
                self._fifos_updated()
        elif offset == SSPCPSR:
            self._clock_divisor = value & CPSDVSR_MASK
        elif offset == SSPIMSC:
            self._int_enable = value
            self.check_interrupts()
        elif offset == SSPDMACR:
            self._dma_control = value
        elif offset == SSPICR:
            self._int_raw &= ~(value & (SSPRTINTR | SSPRORINTR))
            self.check_interrupts()
        else:
            super().write_uint32(offset, value)
