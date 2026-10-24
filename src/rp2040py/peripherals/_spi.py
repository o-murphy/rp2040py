from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from rp2040py.peripherals._dma import DREQChannel
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

# Reserved bits (RP2040 datasheet 4.4.4: SSPCR0 31:16, SSPCR1 31:4, SSPIMSC 31:4, SSPDMACR 31:2) read as zero and are not stored.
CR0_MASK = 0xFFFF
CR1_MASK = 0xF
IMSC_MASK = 0xF
DMACR_MASK = 0x3

# SSPDMACR bits:
TXDMAE = 1 << 1
RXDMAE = 1 << 0

# Interrupts:
SSPTXINTR = 1 << 3
SSPRXINTR = 1 << 2
SSPRTINTR = 1 << 1
SSPRORINTR = 1 << 0


def _clock_frequency(clk_peri: float, clock_divisor: int, scr: int) -> float:
    """The bit clock for a peripheral clock, the prescale divisor and SSPCR0.SCR; also what the native SPI's shell calls, so the division (a true division of
    possibly-integer operands) is the same on both sides by construction."""
    if not clock_divisor:
        return 0
    return clk_peri / (clock_divisor * (1 + scr))


def _bit_ns(clk_peri: float, clock_divisor: int, scr: int) -> float:
    """The time of one SSPCLKOUT period in nanoseconds (0 while the prescale divisor is 0: no bit clock is configured). Shared with the native SPI's shell, as `_clock_frequency` is."""
    frequency = _clock_frequency(clk_peri, clock_divisor, scr)
    return 1e9 / frequency if frequency else 0.0


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
        # A word's time on the wire: data_bits SSPCLKOUT periods from the moment it leaves the TX FIFO. The device's reply (`complete_transmit`) is held until then; a device that is slower
        # than the frame completes it later, on its own.
        self._frame_active = False
        self._reply: int | None = None
        self._frame_alarm = rp2040.clock.create_alarm(self._on_frame_done)
        self._control0 = 0
        self._control1 = 0
        self._dma_control = 0
        self._clock_divisor = 0
        self._int_raw = SSPTXINTR  # SSPRIS resets to 0x8: the TX FIFO is empty, so "half empty or less" holds
        self._int_enable = 0

        self._update_dma_tx()
        self._update_dma_rx()

    def reset(self) -> None:
        """Registers and both FIFOs, back to power-on (0089 Phase 5). `on_transmit` is wiring - a
        device on the bus - and survives, as does `irq`/`dreq` identity.

        The two `_update_dma_*()` calls are not bookkeeping: DREQ state is published *into* the DMA
        block, so clearing the control registers without re-publishing would leave DMA believing
        this peripheral still wants service."""
        self.rx_fifo.reset()
        self.tx_fifo.reset()
        self._busy = False
        self._frame_active = False
        self._reply = None
        self._frame_alarm.cancel()
        self._control0 = 0
        self._control1 = 0
        self._dma_control = 0
        self._clock_divisor = 0
        self._int_raw = SSPTXINTR
        self._int_enable = 0
        self._update_dma_tx()
        self._update_dma_rx()
        self.rp2040.set_interrupt(self.irq, False)

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
        return not (self._control1 & MS)  # MS is SSPCR1 bit 2 (see 'SSPCR1 bits' above); this used to test SSPCR0

    @property
    def spi_mode(self) -> int:
        cpol = self._control0 & SPO
        cpha = self._control0 & SPH
        if cpol:
            return 2 if cpha else 3
        return 1 if cpha else 0

    @property
    def clock_frequency(self) -> float:
        return _clock_frequency(self.rp2040.clk_peri, self._clock_divisor, (self._control0 >> SCR_SHIFT) & SCR_MASK)

    def _frame_ns(self) -> float:
        """How long the word that just left the TX FIFO is on the wire: `data_bits` bit-clock periods. A slave (SSPCR1.MS) is clocked from outside, and an unconfigured bit clock has no time: both are instant."""
        if not self.master_mode:
            return 0.0
        return (
            _bit_ns(self.rp2040.clk_peri, self._clock_divisor, (self._control0 >> SCR_SHIFT) & SCR_MASK)
            * self.data_bits
        )

    def _begin_frame(self) -> None:
        ns = self._frame_ns()
        if ns > 0:
            self._frame_active = True
            self._reply = None
            self._frame_alarm.schedule(ns)

    def _update_dma_tx(self) -> None:
        # "All request signals are deasserted if the PrimeCell SSP is disabled, or the DMA enable signal is cleared" (datasheet 4.4.3.16): SSE and TXDMAE gate the request, which is
        # otherwise up while the TX FIFO has an empty place.
        if self.tx_fifo.full or not (self._control1 & SSE and self._dma_control & TXDMAE):
            self.rp2040.dma.clear_dreq(self.dreq.tx)
        else:
            self.rp2040.dma.set_dreq(self.dreq.tx)

    def _update_dma_rx(self) -> None:
        if self.rx_fifo.empty or not (self._control1 & SSE and self._dma_control & RXDMAE):
            self.rp2040.dma.clear_dreq(self.dreq.rx)
        else:
            self.rp2040.dma.set_dreq(self.dreq.rx)

    def _do_tx(self) -> None:
        # "You can prime the transmit FIFO ... when the PrimeCell SSP is disabled ... Once enabled, transmission ... begins" (4.4.3.3): nothing leaves a disabled SSP.
        if not self._busy and not self.tx_fifo.empty and self._control1 & SSE:
            value = self.tx_fifo.pull()
            self._busy = True
            self._begin_frame()
            if self._control1 & LBM:  # loop back: the output of the transmit shifter feeds the receive shifter
                self.complete_transmit(value)
            else:
                self.on_transmit(value)
            self._fifos_updated()

    def complete_transmit(self, rx_value: int) -> None:
        if self._frame_active:
            self._reply = rx_value  # the word is still being shifted out: it completes when its last bit has gone
            return
        self._deliver(rx_value)

    def _on_frame_done(self) -> None:
        self._frame_active = False
        reply, self._reply = self._reply, None
        if reply is not None:
            self._deliver(reply)  # (no reply yet: BSY stays up until the device completes)

    def _deliver(self, rx_value: int) -> None:
        self._busy = False
        if not self.rx_fifo.full:
            self.rx_fifo.push(rx_value)
        else:
            self._int_raw |= SSPRORINTR
            # The interrupt line is the OR of the enabled raw interrupts, so the overrun reaches it at once. (`_fifos_updated()` below compares the status with the one it
            # sees on entry - after ROR was set - and so never noticed it: the line stayed low until something else changed.)
            self.check_interrupts()
        self._fifos_updated()
        self._do_tx()

    def snapshot(self) -> dict[str, Any]:
        """The block's state as plain values: the registers, the frame in flight, both FIFOs' contents and what is derived from them (the mode, the data size, the clock, the masked interrupts)."""
        return {
            "control0": self._control0,
            "control1": self._control1,
            "dma_control": self._dma_control,
            "clock_divisor": self._clock_divisor,
            "int_raw": self._int_raw,
            "int_enable": self._int_enable,
            "busy": bool(self._busy),
            "frame_active": bool(self._frame_active),
            "reply": None if self._reply is None else int(self._reply),
            "rx": tuple(self.rx_fifo.items),
            "tx": tuple(self.tx_fifo.items),
            "enabled": bool(self.enabled),
            "data_bits": self.data_bits,
            "master_mode": bool(self.master_mode),
            "spi_mode": int(self.spi_mode),
            "clock_frequency": float(self.clock_frequency),
            "int_status": int(self.int_status),
        }

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
            self._control0 = value & CR0_MASK
        elif offset == SSPCR1:
            self._control1 = value & CR1_MASK
            # enabling starts a primed FIFO and publishes the DMA requests, which SSE gates
            self._do_tx()
            self._fifos_updated()
        elif offset == SSPDR:
            if not self.tx_fifo.full:
                # decoded with respect to SSPCR0.DSS
                self.tx_fifo.push(value & ((1 << self.data_bits) - 1))
                self._do_tx()
                self._fifos_updated()
        elif offset == SSPCPSR:
            self._clock_divisor = value & CPSDVSR_MASK
        elif offset == SSPIMSC:
            self._int_enable = value & IMSC_MASK
            self.check_interrupts()
        elif offset == SSPDMACR:
            self._dma_control = value & DMACR_MASK
            self._update_dma_tx()
            self._update_dma_rx()
        elif offset == SSPICR:
            # By the bits the bus passed, not the alias-decoded value - the rule of the UART's and the DMA's write-1-to-clear registers (an alias decodes against a read
            # of ICR, which is write-only and reads as all ones here, so a CLR/XOR/SET write would otherwise clear the wrong bits).
            self._int_raw &= ~(self.raw_write_value & (SSPRTINTR | SSPRORINTR))
            self.check_interrupts()
        else:
            super().write_uint32(offset, value)
