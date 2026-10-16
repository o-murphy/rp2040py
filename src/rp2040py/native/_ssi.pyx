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

from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler


__all__ = ("RPSSI",)


cdef class _RxView:
    """The RX FIFO as the reference's `deque` shows it: length, iteration, `append` (what a test pushes by hand)."""

    cdef RPSSI _owner

    def __len__(self):
        return self._owner._block.rx_count()

    def __bool__(self):
        return self._owner._block.rx_count() != 0

    def __iter__(self):
        return iter([self._owner._block.rx_at(i) for i in range(self._owner._block.rx_count())])

    def append(self, value):
        self._owner._block.rx_push(<uint8_t> (<int> value & 0xFF))

    def __repr__(self):
        return repr(list(self))


cdef class _TxView:
    """The command in flight as the reference's `bytearray` shows it: its length (a count, however long) and its first 260 bytes."""

    cdef RPSSI _owner

    def __len__(self):
        return self._owner._block.tx_length()

    def _kept(self):
        cdef uint32_t n = self._owner._block.tx_length()
        if n > 260:
            n = 260
        return bytes([self._owner._block.tx_at(i) for i in range(n)])

    def __getitem__(self, index):
        kept = self._kept()
        if isinstance(index, slice):
            return kept[index]
        if index >= len(kept) or index < -len(kept):
            raise IndexError("the native SSI keeps only the first 260 bytes of a command")
        return kept[index]

    def __iter__(self):
        return iter(self._kept())

    def append(self, value):
        self._owner._block.tx_append(<uint8_t> (<int> value & 0xFF))

    def __repr__(self):
        return repr(self._kept())


cdef class RPSSI:
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
        self.rp2040 = rp2040
        self.name = name

    def _warn_kind(self, kind, offset, value):
        """A register access the block does not implement, as the logger's message (the chip's host calls this for the block it built)."""
        if kind == kSsiWarnRead:
            self.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kSsiWarnReadAtomicArea:
            self.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            self.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    def _detach(self):
        """Takes the block off the chip-select pin (the oracle's rig replaces an SSI after construction; a second one listening would apply every
        command to the same flash twice)."""
        self._block.detach()

    # --- the reference's private state, as views --------------------------------------------------

    @property
    def _rx_queue(self):
        cdef _RxView view = _RxView.__new__(_RxView)
        view._owner = self
        return view

    @property
    def _tx_buffer(self):
        cdef _TxView view = _TxView.__new__(_TxView)
        view._owner = self
        return view

    @property
    def _ssienr(self):
        return self._block.ssienr()

    @_ssienr.setter
    def _ssienr(self, value):
        self._block.set_ssienr(<uint32_t> (<int64_t> value))

    @property
    def _txflr(self):
        return self._block.txflr()

    @property
    def _stored(self):
        # the not-acted-on registers by offset, as the reference's dict (MWCR, SER, TXFTLR, RXFTLR, IMR, DMACR, DMATDLR, DMARDLR)
        offsets = (0x0C, 0x10, 0x18, 0x1C, 0x2C, 0x4C, 0x50, 0x54)
        return {offset: self._block.stored(index) for index, offset in enumerate(offsets)}

    @property
    def _sticky(self):
        return self._block.sticky()

    @_sticky.setter
    def _sticky(self, value):
        self._block.set_sticky(<uint32_t> (value & 0xFFFFFFFF))

    @property
    def _risr(self):
        return self._block.risr()

    @property
    def _isr(self):
        return self._block.isr()

    @property
    def _irq_level(self):
        return bool(self._block.irq_level())

    @property
    def _dreq_tx(self):
        return bool(self._block.dreq_tx())

    @property
    def _dreq_rx(self):
        return bool(self._block.dreq_rx())

    @property
    def _write_enabled(self):
        return self._block.write_enabled()

    @_write_enabled.setter
    def _write_enabled(self, value):
        self._block.set_write_enabled(bool(value))

    @property
    def _cs_asserted(self):
        return self._block.cs_asserted()

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    # --- BasePeripheral's surface -----------------------------------------------------------------

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        self._block.write(<uint32_t> offset, <int64_t> value)
        raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type)
        raise_if_pending()

    def reset(self):
        if not self._block.reset():
            raise_if_pending()
        raise_if_pending()

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    # --- the native bus protocol ------------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the
        block's own read/write functions and its context. Looked up on the *type* by the bus."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
