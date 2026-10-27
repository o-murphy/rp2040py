# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 UART: a Python-facing shell over the C++ `UartBlock` of `core/uart.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 4).
`peripherals/_uart.py` is the pure-Python reference, kept as the oracle (tests/test_uart_diff.py); `peripherals/uart.py` is the facade that
picks between them, as for the other native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x40034] = uart`) it registers the block's own
C++ read/write functions in the bus's window table (see `_native_window`), so a register access never enters Python. What stays Python is
the edge of the block, reached through trampolines whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever
called in: the interrupt line (`rp2040.set_interrupt`), the TX DREQ (`rp2040.dma`), the transmitted-byte callback `on_byte`, the baud-rate
announcement `on_baud_rate_change` (the division by `clk_peri` and the callback are Python's: a zero divider is a `ZeroDivisionError` that
must reach the writer), the bit time (`bit_ns`, the reference's own helper, so both sides compute the same double) and the logger. The transmitter's
shift register is an alarm of the chip's C++ clock, so a character's time on the wire runs without Python (it needs the native `SimulationClock`, like the ADC's).

The API is the reference's: the registers' private attributes (`_ctrl_register`, `_line_ctrl_register`, `_int_divisor`, `_frac_divisor`,
`_interrupt_mask`, `_interrupt_status`, readable and writable - `tests/test_chip_reset.py` pokes them), `rx_fifo` (a view with the reference
FIFO's attribute names), `flags`, `baud_divider`, `baud_rate`,
`feed_byte`, `check_interrupts`, `reset`, `on_byte`/`on_baud_rate_change`, `tx_fifo` (a view), `tx_full`/`tx_capacity`/`tx_trigger`, plus `BasePeripheral`'s surface (name,
rp2040, raw_write_value, read_uint32, write_uint32, write_uint32_atomic, warn/...). Unlike the DMA it does not need the chip's bus.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.peripherals._uart import _baud_rate, _bit_ns
from rp2040py.native._register_warning import warn_register

__all__ = ("RPUART",)


cdef cppbool _ports_irq(void* chip, uint32_t line, cppbool level) noexcept:
    cdef RPUART uart = <RPUART> chip
    try:
        uart.rp2040.set_interrupt(uart.irq, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _ports_dreq(void* chip, uint32_t number, cppbool asserted) noexcept:
    cdef RPUART uart = <RPUART> chip
    try:
        channel = uart.dreq.tx if number == uart._dreq_base else uart.dreq.rx
        if asserted:
            uart.rp2040.dma.set_dreq(channel)
        else:
            uart.rp2040.dma.clear_dreq(channel)
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


cdef cppbool _bit_trampoline(void* ctx, double* nanos) noexcept:
    cdef RPUART uart = <RPUART> ctx
    try:
        nanos[0] = _bit_ns(uart.rp2040.clk_peri, uart.baud_divider)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _ports_warn(void* chip, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPUART uart = <RPUART> chip
    try:
        warn_register(uart, kind, offset, value)
    except BaseException as error:
        park_error(error)


cdef class RPUART(NativeBlock):
    def __init__(self, rp2040, name, irq, dreq, mcu_index=-1):
        cdef UartHost host
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native UART schedules its transmitter's alarm on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_uart.RPUART with it)"
            )
        self.clock = clock
        self.rp2040 = rp2040
        self.name = name
        self.irq = irq
        self.dreq = dreq
        self.on_byte = None
        self.on_baud_rate_change = None
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus, its line and request on the chip - and this object is the Python view of it
            self._block = <UartBlock*> <size_t> rp2040._native_uart_address(mcu_index)
            self._ops = register_ops(self._block)
            return
        self._block = &self._own
        self._ops = register_ops(self._block)
        self._ports.chip = <void*> self
        self._ports.warn = _ports_warn
        self._ports.irq = _ports_irq
        self._ports.dreq = _ports_dreq
        self._ports.failed = pending_flag()
        self._dreq_base = <uint32_t> int(self.dreq.tx)
        host.on_byte = _byte_trampoline
        host.baud_changed = _baud_trampoline
        host.bit_ns = _bit_trampoline
        host.ctx = <void*> self
        self._own.init(&(<SimulationClock> clock)._clock, &self._ports, 0, <uint32_t> int(irq), self._dreq_base, host)

    def _world_byte(self, byte):
        """A transmitted byte, for the device the host attached (the chip's host calls this for the block it built)."""
        callback = self.on_byte
        if callback:
            callback(byte)

    def _world_baud(self):
        callback = self.on_baud_rate_change
        if callback:
            callback(self.baud_rate)

    def __dealloc__(self):
        # Safe whichever of this block and its clock the collector frees first: the C++ clock unlinks its alarms when it dies.
        if self._block == &self._own:
            self._own.detach()

    # --- the state, in one call ------------------------------------------------------------------

    def snapshot(self):
        """The block's state in one call: the registers (public fields of the C++ block), both FIFOs' contents, the shifter and what is derived from them."""
        cdef UartBlock* b = self._block
        cdef uint32_t i
        return {
            "ctrl": b.ctrl,
            "line_ctrl": b.line_ctrl,
            "int_divisor": b.int_divisor,
            "frac_divisor": b.frac_divisor,
            "interrupt_mask": b.interrupt_mask,
            "interrupt_status": b.interrupt_status,
            "ifls": b.ifls,
            "ilpr": b.ilpr,
            "dmacr": b.dmacr,
            "rsr": b.rsr,
            "rx": tuple([b.rx_at(i) for i in range(b.rx_count())]),
            "tx": tuple([b.tx_at(i) for i in range(b.tx_count())]),
            "tx_shifting": bool(b.tx_shifting()),
            "tx_capacity": b.tx_capacity(),
            "tx_trigger": b.tx_trigger(),
            "tx_full": bool(b.tx_full()),
            "flags": b.flags(),
        }

    def _update_dreq(self):
        if not self._block.update_dreq():
            raise_if_pending()

    @property
    def baud_divider(self):
        return self._block.int_divisor + self._block.frac_divisor / 64.0  # a double: with cdivision the integer operands would divide as integers

    @property
    def baud_rate(self):
        return _baud_rate(self.rp2040.clk_peri, self.baud_divider)

    # --- BasePeripheral's surface ----------------------------------------------------------------

    def feed_byte(self, value):
        if not self._block.feed_byte(<uint32_t> (<int64_t> value)):
            raise_if_pending()

    def reset(self):
        """Registers and the RX FIFO, back to power-on (0089 Phase 5). `on_byte`/`on_baud_rate_change` are not touched: they are wiring."""
        if not self._block.reset():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------
