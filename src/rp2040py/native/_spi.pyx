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
names), `int_status`, `enabled`, `data_bits`, `master_mode`, `spi_mode`, `clock_frequency`, `complete_transmit`, `check_interrupts`, `reset`, plus
`BasePeripheral`'s surface. Unlike the DMA it needs neither the chip's bus nor a native clock.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._spi import _clock_frequency

__all__ = ("RPSPI",)

cdef uint32_t SPH = 1 << 7
cdef uint32_t SPO = 1 << 6
cdef uint32_t MS = 1 << 2
cdef uint32_t SCR_MASK = 0xFF
cdef uint32_t SCR_SHIFT = 8


cdef cppbool _irq_trampoline(void* ctx, cppbool level) noexcept:
    cdef RPSPI spi = <RPSPI> ctx
    try:
        spi.rp2040.set_interrupt(spi.irq, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _dreq_trampoline(void* ctx, cppbool tx, cppbool asserted) noexcept:
    cdef RPSPI spi = <RPSPI> ctx
    try:
        channel = spi.dreq.tx if tx else spi.dreq.rx
        if asserted:
            spi.rp2040.dma.set_dreq(channel)
        else:
            spi.rp2040.dma.clear_dreq(channel)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _transmit_trampoline(void* ctx, uint32_t value) noexcept:
    cdef RPSPI spi = <RPSPI> ctx
    try:
        callback = spi._on_transmit
        if callback is None:
            # The reference's default device: the byte comes straight back as 0, from inside the callback. Never leaves C++.
            return spi._block.complete_transmit(0)
        callback(value)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPSPI spi = <RPSPI> ctx
    try:
        if kind == kSpiWarnRead:
            spi.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kSpiWarnReadAtomicArea:
            spi.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            spi.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class _Fifo:
    """One of the two FIFOs as the reference's `utils.fifo.FIFO(8)` shows it."""

    cdef RPSPI _owner
    cdef bint _is_tx

    cdef Fifo8* _fifo(self):
        return &self._owner._block.tx if self._is_tx else &self._owner._block.rx

    @property
    def size(self):
        return 8

    @property
    def item_count(self):
        return self._fifo().count()

    @property
    def empty(self):
        return self._fifo().empty()

    @property
    def full(self):
        return self._fifo().full()

    @property
    def items(self):
        cdef Fifo8* fifo = self._fifo()
        return [fifo.at(i) for i in range(fifo.count())]

    def push(self, value):
        self._fifo().push(<uint32_t> (<int64_t> value))

    def pull(self):
        return self._fifo().pull()

    def peek(self):
        return self._fifo().peek()

    def reset(self):
        self._fifo().reset()


cdef class RPSPI:
    def __init__(self, rp2040, name, irq, dreq):
        cdef SpiHost host
        self.rp2040 = rp2040
        self.name = name
        self.irq = irq
        self.dreq = dreq
        self._on_transmit = None
        host.irq = _irq_trampoline
        host.dreq = _dreq_trampoline
        host.transmit = _transmit_trampoline
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(host)
        # The reference's constructor publishes both DREQs.
        if not self._block.power_on():
            raise_if_pending()

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

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def rx_fifo(self):
        cdef _Fifo view = _Fifo.__new__(_Fifo)
        view._owner = self
        view._is_tx = False
        return view

    @property
    def tx_fifo(self):
        cdef _Fifo view = _Fifo.__new__(_Fifo)
        view._owner = self
        view._is_tx = True
        return view

    @property
    def _busy(self):
        return bool(self._block.busy)

    @_busy.setter
    def _busy(self, value):
        self._block.busy = bool(value)

    @property
    def _control0(self):
        return self._block.control0

    @_control0.setter
    def _control0(self, value):
        self._block.control0 = <uint32_t> (<int64_t> value)

    @property
    def _control1(self):
        return self._block.control1

    @_control1.setter
    def _control1(self, value):
        self._block.control1 = <uint32_t> (<int64_t> value)

    @property
    def _dma_control(self):
        return self._block.dma_control

    @_dma_control.setter
    def _dma_control(self, value):
        self._block.dma_control = <uint32_t> (<int64_t> value)

    @property
    def _clock_divisor(self):
        return self._block.clock_divisor

    @_clock_divisor.setter
    def _clock_divisor(self, value):
        self._block.clock_divisor = <uint32_t> (<int64_t> value)

    @property
    def _int_raw(self):
        return self._block.int_raw

    @_int_raw.setter
    def _int_raw(self, value):
        self._block.int_raw = <uint32_t> (<int64_t> value)

    @property
    def _int_enable(self):
        return self._block.int_enable

    @_int_enable.setter
    def _int_enable(self, value):
        self._block.int_enable = <uint32_t> (<int64_t> value)

    @property
    def int_status(self):
        return self._block.int_status()

    @property
    def enabled(self):
        return bool(self._block.enabled())

    @property
    def data_bits(self):
        """Data size in bits: 4 to 16 bits"""
        return self._block.data_bits()

    @property
    def master_mode(self):
        return not (self._block.control1 & MS)  # SSPCR1.MS, bit 2

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

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        # A direct write keeps whatever raw_write_value the last atomic write left, as the pure-Python block does.
        if not self._block.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

    def reset(self):
        """Registers and both FIFOs, back to power-on (0089 Phase 5). `on_transmit` is wiring - a device on the bus - and survives, as does `irq`/`dreq` identity."""
        if not self._block.reset():
            raise_if_pending()

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    # --- the native bus protocol -----------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the block's own
        read/write functions and its context. Looked up on the *type* by the bus, so a wrapper that merely forwards attributes (a recorder, a profiler)
        cannot lend its target's fast path and be bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
