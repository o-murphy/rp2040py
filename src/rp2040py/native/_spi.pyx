# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 SPI (PL022): a Python-facing shell over the C++ `SpiBlock` of `core/spi.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 4).
`peripherals/_spi.py` is the pure-Python reference, kept as the oracle (tests/test_spi_diff.py); `peripherals/spi.py` is the facade that picks between
them, as for the other native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x4003C] = spi`) it registers the block's own C++
read/write functions in the bus's window table (see `_native_window`), so a register access never enters Python. What stays Python is the edge of the
block, reached through trampolines whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in: the interrupt line
(`rp2040.set_interrupt`), the two DREQs (`rp2040.dma`), the transmit callback `on_transmit` and the logger.

`on_transmit` is the device on the bus: a plain attribute of the shell that hands back the very object that was set (`tests/test_chip_reset.py` compares
with `is`); with none set the reference's default applies - every byte comes straight back as 0 - and the trampoline completes it in C++ without calling
Python. A device that finishes later (the ST7735S, the e-paper) calls `complete_transmit()` from a clock alarm, and one that finishes at once calls it from
inside `on_transmit`: the block is re-entered from its own host callback, as the reference is.

The API is the reference's: the registers' private attributes (readable and writable), `rx_fifo`/`tx_fifo` (views with the reference FIFO's attribute
names), `int_status`, `data_bits`, `spi_mode`, `clock_frequency`, `complete_transmit`, `check_interrupts`, `reset`, plus
`BasePeripheral`'s surface. Unlike the DMA it does not need the chip's bus, but a word's time on the wire is an alarm of the chip's C++ clock, so it needs the native `SimulationClock` (as the UART and the ADC do).
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._mcu_irq cimport Mcu
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._spi import _clock_frequency

__all__ = ("RPSPI",)

cdef uint32_t SPH = 1 << 7
cdef uint32_t SPO = 1 << 6
cdef uint32_t MS = 1 << 2
cdef uint32_t SCR_MASK = 0xFF
cdef uint32_t SCR_SHIFT = 8


cdef class RPSPI(NativeBlock):
    def __init__(self, rp2040, name, irq, dreq, mcu_index=-1):
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native SPI schedules a word's time on the wire as an alarm of the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_spi.RPSPI with it)"
            )
        self.clock = clock
        self.rp2040 = rp2040
        self.name = name
        self.irq = irq
        self.dreq = dreq
        self._on_transmit = None
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus, its line and request on the chip - and this object is the Python view of it
            self._block = <SpiBlock*> <size_t> rp2040._native_spi_address(mcu_index)
            self._ops = register_ops(self._block)
            if not self._block.power_on():
                raise_if_pending()
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPSPI is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def _world_transmit(self, value):
        """A word is on the wire: the attached device answers; with none, the byte comes straight back as 0 (the reference's default device)."""
        callback = self._on_transmit
        if callback is None:
            return bool(self._block.complete_transmit(0))
        callback(value)
        return True

    # --- the device on the bus -------------------------------------------------------------------

    @property
    def on_transmit(self):
        if self._on_transmit is None:
            return lambda value: self.complete_transmit(0)  # the reference's default
        return self._on_transmit

    @on_transmit.setter
    def on_transmit(self, callback):
        self._on_transmit = callback

    def complete_transmit(self, rx_value):
        if not self._block.complete_transmit(<uint32_t> (<int64_t> rx_value)):
            raise_if_pending()

    # --- the state, in one call ------------------------------------------------------------------

    def snapshot(self):
        """The block's state in one call: the registers, the frame in flight, both FIFOs' contents and what is derived from them."""
        cdef SpiBlock* b = self._block
        cdef uint32_t i
        return {
            "control0": b.control0,
            "control1": b.control1,
            "dma_control": b.dma_control,
            "clock_divisor": b.clock_divisor,
            "int_raw": b.int_raw,
            "int_enable": b.int_enable,
            "busy": bool(b.busy),
            "frame_active": bool(b.frame_active),
            "reply": b.reply if b.has_reply else None,
            "rx": tuple([b.rx.at(i) for i in range(b.rx.count())]),
            "tx": tuple([b.tx.at(i) for i in range(b.tx.count())]),
            "enabled": bool(b.enabled()),
            "data_bits": b.data_bits(),
            "master_mode": not (b.control1 & MS),
            "spi_mode": self.spi_mode,
            "clock_frequency": self.clock_frequency,
            "int_status": b.int_status(),
        }

    # --- what a device on the bus reads of the configuration ---------------------------------------

    @property
    def data_bits(self):
        """Data size in bits: 4 to 16 bits"""
        return self._block.data_bits()

    @property
    def spi_mode(self):
        cdef uint32_t cpol = self._block.control0 & SPO
        cdef uint32_t cpha = self._block.control0 & SPH
        if cpol:
            return 2 if cpha else 3
        return 1 if cpha else 0

    @property
    def clock_frequency(self):
        return _clock_frequency(self.rp2040.clk_peri, self._block.clock_divisor, (self._block.control0 >> SCR_SHIFT) & SCR_MASK)

    # --- BasePeripheral's surface ----------------------------------------------------------------

    def reset(self):
        """Registers and both FIFOs, back to power-on (0089 Phase 5). `on_transmit` is wiring - a device on the bus - and survives, as does `irq`/`dreq` identity."""
        if not self._block.reset():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------
