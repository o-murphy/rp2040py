# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 SSI (the flash command path): a Python-facing shell over the C++ `SsiBlock` of `core/ssi.hpp`
(docs/records/0096-cpp-mcu-core.md, the flash-path design note). `peripherals/_ssi.py` is the pure-Python reference, kept as the
oracle (tests/test_ssi_diff.py); `peripherals/ssi.py` is the facade that picks between them, as for the other native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x18000] = ssi`) it registers the
block's own C++ read/write functions in the bus's window table (see `_native_window`), so a bootrom's DR0/SR polling never touches
Python. The chip's C++ `Mcu` builds the block (record 0096, step 3a): its window, its interrupt line (XIP_IRQ), its DMA requests and its chip-select listener (a *direct
listener* of the QSPI_SS pin, so an edge never enters Python) are wired there; this object is only the Python view. The flash is the chip's own buffer, by pointer.

The reference's private state is kept readable under its names (`_ssienr`, `_write_enabled`, `_cs_asserted`, `_rx_queue`,
`_tx_buffer`, ...) as views of the C++ state: the tests and the oracle look at them. `_tx_buffer` shows the first 260 bytes of the
command (all the block keeps) and the *count* as its length.
"""

from libc.stdint cimport int64_t, uint8_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPSSI",)


cdef class RPSSI(NativeBlock):
    def __init__(self, rp2040, name):
        native_ssi = getattr(rp2040, "_native_ssi_address", None)
        if native_ssi is None:
            raise TypeError(
                f"the native SSI is a view of a block of the native chip's C++ core; {type(rp2040).__name__} is not that "
                "(use the pure-Python peripherals/_ssi.RPSSI with it)"
            )
        # the chip's `Mcu` builds the block: its window, its interrupt line, its DMA requests and its chip-select listener are wired inside
        self._from_mcu = True
        self._block = <SsiBlock*> <size_t> native_ssi()
        self._ops = register_ops(self._block)
        self.rp2040 = rp2040
        self.name = name

    def _detach(self):
        """Takes the block off the chip-select pin (the oracle's rig replaces an SSI after construction; a second one listening would apply every
        command to the same flash twice)."""
        self._block.detach()

    # --- the state, in one call ------------------------------------------------------------------

    def snapshot(self):
        """The block's state in one call: the enable, the held and derived interrupts and requests, the stored registers, the received bytes, and the flash command in flight."""
        cdef SsiBlock* b = self._block
        cdef uint32_t i
        cdef uint32_t n = b.tx_length()
        if n > 260:
            n = 260
        offsets = (0x0C, 0x10, 0x18, 0x1C, 0x2C, 0x4C, 0x50, 0x54)  # the not-acted-on registers: MWCR, SER, TXFTLR, RXFTLR, IMR, DMACR, DMATDLR, DMARDLR
        return {
            "ssienr": b.ssienr(),
            "txflr": b.txflr(),
            "stored": tuple([(offset, b.stored(index)) for index, offset in enumerate(offsets)]),
            "sticky": b.sticky(),
            "risr": b.risr(),
            "isr": b.isr(),
            "irq_level": bool(b.irq_level()),
            "dreq_tx": bool(b.dreq_tx()),
            "dreq_rx": bool(b.dreq_rx()),
            "rx": tuple([b.rx_at(i) for i in range(b.rx_count())]),
            "write_enabled": bool(b.write_enabled()),
            "cs_asserted": bool(b.cs_asserted()),
            "command_length": b.tx_length(),
            "command": bytes([b.tx_at(i) for i in range(n)]),
        }

    # --- BasePeripheral's surface -----------------------------------------------------------------

    def reset(self):
        if not self._block.reset():
            raise_if_pending()
        raise_if_pending()

    # --- the native bus protocol ------------------------------------------------------------------
