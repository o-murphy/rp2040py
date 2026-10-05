# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 UART: a Python-facing shell over the C++ `UartBlock` of `core/uart.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 4).
`peripherals/_uart.py` is the pure-Python reference, kept as the oracle (tests/test_uart_diff.py); `peripherals/uart.py` is the facade that
picks between them, as for the other native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x40034] = uart`) it registers the block's own
C++ read/write functions in the bus's window table (see `_native_window`), so a register access never enters Python. What stays Python is
the edge of the block, reached through trampolines whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever
called in: the interrupt line (`rp2040.set_interrupt`), the TX DREQ (`rp2040.dma`), the transmitted-byte callback `on_byte`, the baud-rate
announcement `on_baud_rate_change` (the division by `clk_peri` and the callback are Python's: a zero divider is a `ZeroDivisionError` that
must reach the writer) and the logger.

The API is the reference's: the registers' private attributes (`_ctrl_register`, `_line_ctrl_register`, `_int_divisor`, `_frac_divisor`,
`_interrupt_mask`, `_interrupt_status`, readable and writable - `tests/test_chip_reset.py` pokes them), `rx_fifo` (a view with the reference
FIFO's attribute names), `flags`, `enabled`/`tx_enabled`/`rx_enabled`/`fifos_enabled`, `word_length`, `baud_divider`, `baud_rate`,
`feed_byte`, `check_interrupts`, `reset`, `on_byte`/`on_baud_rate_change`, plus `BasePeripheral`'s surface (name, rp2040, raw_write_value,
read_uint32, write_uint32, write_uint32_atomic, warn/...). Unlike the DMA it needs neither the chip's bus nor a native clock.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._uart import _WORD_LENGTH_BY_LCR_WLEN, _baud_rate

__all__ = ("RPUART",)

cdef uint32_t FEN = 1 << 4
cdef uint32_t UARTEN = 1 << 0
cdef uint32_t TXE = 1 << 8
cdef uint32_t RXE = 1 << 9


cdef cppbool _irq_trampoline(void* ctx, cppbool level) noexcept:
    cdef RPUART uart = <RPUART> ctx
    try:
        uart.rp2040.set_interrupt(uart.irq, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _dreq_trampoline(void* ctx, cppbool asserted) noexcept:
    cdef RPUART uart = <RPUART> ctx
    try:
        if asserted:
            uart.rp2040.dma.set_dreq(uart.dreq.tx)
        else:
            uart.rp2040.dma.clear_dreq(uart.dreq.tx)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _byte_trampoline(void* ctx, uint32_t byte) noexcept:
    cdef RPUART uart = <RPUART> ctx
    try:
        callback = uart.on_byte
        if callback:
            callback(byte)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _baud_trampoline(void* ctx) noexcept:
    cdef RPUART uart = <RPUART> ctx
    try:
        callback = uart.on_baud_rate_change
        if callback:
            callback(uart.baud_rate)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPUART uart = <RPUART> ctx
    try:
        if kind == kUartWarnRead:
            uart.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kUartWarnReadAtomicArea:
            uart.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            uart.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class _RxFifo:
    """The RX FIFO as the reference's `utils.fifo.FIFO(32)` shows it."""

    cdef RPUART _owner

    @property
    def size(self):
        return 32

    @property
    def item_count(self):
        return self._owner._block.rx_count()

    @property
    def empty(self):
        return self._owner._block.rx_empty()

    @property
    def full(self):
        return self._owner._block.rx_full()

    @property
    def items(self):
        return [self._owner._block.rx_at(i) for i in range(self._owner._block.rx_count())]

    def push(self, value):
        self._owner._block.rx_push(<uint32_t> (<int64_t> value))

    def pull(self):
        return self._owner._block.rx_pull()

    def peek(self):
        return self._owner._block.rx_peek()

    def reset(self):
        self._owner._block.rx_reset()


cdef class RPUART:
    def __init__(self, rp2040, name, irq, dreq):
        cdef UartHost host
        self.rp2040 = rp2040
        self.name = name
        self.irq = irq
        self.dreq = dreq
        self.on_byte = None
        self.on_baud_rate_change = None
        host.irq = _irq_trampoline
        host.dreq = _dreq_trampoline
        host.on_byte = _byte_trampoline
        host.baud_changed = _baud_trampoline
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(host)

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def rx_fifo(self):
        cdef _RxFifo view = _RxFifo.__new__(_RxFifo)
        view._owner = self
        return view

    @property
    def _ctrl_register(self):
        return self._block.ctrl

    @_ctrl_register.setter
    def _ctrl_register(self, value):
        self._block.ctrl = <uint32_t> (<int64_t> value)

    @property
    def _line_ctrl_register(self):
        return self._block.line_ctrl

    @_line_ctrl_register.setter
    def _line_ctrl_register(self, value):
        self._block.line_ctrl = <uint32_t> (<int64_t> value)

    @property
    def _int_divisor(self):
        return self._block.int_divisor

    @_int_divisor.setter
    def _int_divisor(self, value):
        self._block.int_divisor = <uint32_t> (<int64_t> value)

    @property
    def _frac_divisor(self):
        return self._block.frac_divisor

    @_frac_divisor.setter
    def _frac_divisor(self, value):
        self._block.frac_divisor = <uint32_t> (<int64_t> value)

    @property
    def _interrupt_mask(self):
        return self._block.interrupt_mask

    @_interrupt_mask.setter
    def _interrupt_mask(self, value):
        self._block.interrupt_mask = <uint32_t> (<int64_t> value)

    @property
    def _interrupt_status(self):
        return self._block.interrupt_status

    @_interrupt_status.setter
    def _interrupt_status(self, value):
        self._block.interrupt_status = <uint32_t> (<int64_t> value)

    @property
    def enabled(self):
        return bool(self._block.ctrl & UARTEN)

    @property
    def tx_enabled(self):
        return bool(self._block.ctrl & TXE)

    @property
    def rx_enabled(self):
        return bool(self._block.ctrl & RXE)

    @property
    def fifos_enabled(self):
        return bool(self._block.line_ctrl & FEN)

    @property
    def word_length(self):
        """Number of bits per UART character"""
        return _WORD_LENGTH_BY_LCR_WLEN[(self._block.line_ctrl >> 5) & 0x3]

    @property
    def baud_divider(self):
        return self._block.int_divisor + self._block.frac_divisor / 64.0  # a double: with cdivision the integer operands would divide as integers

    @property
    def baud_rate(self):
        return _baud_rate(self.rp2040.clk_peri, self.baud_divider)

    def clk_peri_changed(self):
        """The peripheral clock changed: re-announce the baud rate, unless the firmware has not set the divider yet (rp2040js 1.4.0)."""
        if self.baud_divider and self.on_baud_rate_change:
            self.on_baud_rate_change(self.baud_rate)

    @property
    def flags(self):
        return self._block.flags()

    # --- BasePeripheral's surface ----------------------------------------------------------------

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

    def feed_byte(self, value):
        if not self._block.feed_byte(<uint32_t> (<int64_t> value)):
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
        """Registers and the RX FIFO, back to power-on (0089 Phase 5). `on_byte`/`on_baud_rate_change` are not touched: they are wiring."""
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
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the block's
        own read/write functions and its context. Looked up on the *type* by the bus, so a wrapper that merely forwards attributes (a recorder,
        a profiler) cannot lend its target's fast path and be bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
