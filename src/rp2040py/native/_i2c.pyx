# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 I2C (DW_apb_i2c, master side): a Python-facing shell over the C++ `I2cBlock` of `core/i2c.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 4).
`peripherals/_i2c.py` is the pure-Python reference, kept as the oracle (tests/test_i2c_diff.py); `peripherals/i2c.py` is the facade that picks between them, as for the other
native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x40044] = i2c`) it registers the block's own C++ read/write functions in the bus's
window table (see `_native_window`), so a register access never enters Python. What stays Python is the edge of the block, reached through trampolines whose failures are parked
in the shared slot of `_pending.pyx` and re-raised by whoever called in: the interrupt line (`rp2040.set_interrupt`), the logger and the five callbacks of the device on the bus -
`on_start`, `on_connect`, `on_write_byte`, `on_read_byte`, `on_stop`.

Each `on_*` is a plain attribute of the shell that hands back the very object that was set (`tests/test_chip_reset.py` compares with `is`). With none set the reference's default
applies - start and stop complete at once, **every connect is NACKed**, a write is NACKed, a read returns 0xFF - and the trampoline completes it in C++ without calling Python.
A device that answers later calls `complete_*()` from a clock alarm, and one that answers at once calls it from inside the callback: the block is re-entered from its own host
callback, as the reference is, down the whole queue of commands in one call stack.

The API is the reference's: the registers' attributes (readable and writable), `_rx_fifo`/`_tx_fifo` (views with the reference FIFO's attribute names), `_state` (an `I2CState`),
`speed`, `scl_low_period`, `scl_high_period`, `master_bits`, `int_status`, `complete_*`, `arbitration_lost`, `check_interrupts`, `reset`, plus `BasePeripheral`'s surface.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._i2c import I2CMode, I2CSpeed, I2CState

__all__ = ("RPI2C",)

cdef uint32_t SPEED_SHIFT = 1
cdef uint32_t SPEED_MASK = 0x3
cdef uint32_t SPEED_STANDARD = 1


cdef cppbool _irq_trampoline(void* ctx, cppbool level) noexcept:
    cdef RPI2C i2c = <RPI2C> ctx
    try:
        i2c.rp2040.set_interrupt(i2c.irq, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _start_trampoline(void* ctx, cppbool repeated) noexcept:
    cdef RPI2C i2c = <RPI2C> ctx
    try:
        callback = i2c._on_start
        if callback is None:
            return i2c._block.complete_start()  # the reference's default device: never leaves C++
        callback(bool(repeated))
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _connect_trampoline(void* ctx, uint32_t address, uint32_t mode) noexcept:
    cdef RPI2C i2c = <RPI2C> ctx
    try:
        callback = i2c._on_connect
        if callback is None:
            return i2c._block.complete_connect(False, 0)  # the default device NACKs every address
        callback(address, I2CMode(mode))
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _write_trampoline(void* ctx, uint32_t value) noexcept:
    cdef RPI2C i2c = <RPI2C> ctx
    try:
        callback = i2c._on_write_byte
        if callback is None:
            return i2c._block.complete_write(False)
        callback(value)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _read_trampoline(void* ctx, cppbool ack) noexcept:
    cdef RPI2C i2c = <RPI2C> ctx
    try:
        callback = i2c._on_read_byte
        if callback is None:
            return i2c._block.complete_read(0xFF)
        callback(bool(ack))
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _stop_trampoline(void* ctx) noexcept:
    cdef RPI2C i2c = <RPI2C> ctx
    try:
        callback = i2c._on_stop
        if callback is None:
            return i2c._block.complete_stop()
        callback()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPI2C i2c = <RPI2C> ctx
    try:
        if kind == kI2cWarnRead:
            i2c.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kI2cWarnReadAtomicArea:
            i2c.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            i2c.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class _Fifo:
    """One of the two FIFOs as the reference's `utils.fifo.FIFO(16)` shows it."""

    cdef RPI2C _owner
    cdef bint _is_tx

    cdef Fifo16* _fifo(self):
        return &self._owner._block.tx if self._is_tx else &self._owner._block.rx

    @property
    def size(self):
        return 16

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
        cdef Fifo16* fifo = self._fifo()
        return [fifo.at(i) for i in range(fifo.count())]

    def push(self, value):
        self._fifo().push(<uint32_t> (<int64_t> value))

    def pull(self):
        return self._fifo().pull()

    def peek(self):
        return self._fifo().peek()

    def reset(self):
        self._fifo().reset()


cdef class RPI2C:
    def __init__(self, rp2040, name, irq):
        cdef I2cHost host
        self.rp2040 = rp2040
        self.name = name
        self.irq = irq
        self._on_start = None
        self._on_connect = None
        self._on_write_byte = None
        self._on_read_byte = None
        self._on_stop = None
        host.irq = _irq_trampoline
        host.start = _start_trampoline
        host.connect = _connect_trampoline
        host.write_byte = _write_trampoline
        host.read_byte = _read_trampoline
        host.stop = _stop_trampoline
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(host)

    # --- the device on the bus -------------------------------------------------------------------

    @property
    def on_start(self):
        if self._on_start is None:
            return lambda repeated_start: self.complete_start()  # the reference's default
        return self._on_start

    @on_start.setter
    def on_start(self, callback):
        self._on_start = callback

    @property
    def on_connect(self):
        if self._on_connect is None:
            return lambda address, mode: self.complete_connect(False)
        return self._on_connect

    @on_connect.setter
    def on_connect(self, callback):
        self._on_connect = callback

    @property
    def on_write_byte(self):
        if self._on_write_byte is None:
            return lambda value: self.complete_write(False)
        return self._on_write_byte

    @on_write_byte.setter
    def on_write_byte(self, callback):
        self._on_write_byte = callback

    @property
    def on_read_byte(self):
        if self._on_read_byte is None:
            return lambda ack: self.complete_read(0xFF)
        return self._on_read_byte

    @on_read_byte.setter
    def on_read_byte(self, callback):
        self._on_read_byte = callback

    @property
    def on_stop(self):
        if self._on_stop is None:
            return self.complete_stop
        return self._on_stop

    @on_stop.setter
    def on_stop(self, callback):
        self._on_stop = callback

    def complete_start(self):
        if not self._block.complete_start():
            raise_if_pending()

    def complete_connect(self, ack, nack_byte=0):
        if not self._block.complete_connect(bool(ack), <uint32_t> (<int64_t> nack_byte)):
            raise_if_pending()

    def complete_write(self, ack):
        if not self._block.complete_write(bool(ack)):
            raise_if_pending()

    def complete_read(self, value):
        if not self._block.complete_read(<uint32_t> (<int64_t> value)):
            raise_if_pending()

    def complete_stop(self):
        if not self._block.complete_stop():
            raise_if_pending()

    def arbitration_lost(self):
        if not self._block.arbitration_lost():
            raise_if_pending()

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def _rx_fifo(self):
        cdef _Fifo view = _Fifo.__new__(_Fifo)
        view._owner = self
        view._is_tx = False
        return view

    @property
    def _tx_fifo(self):
        cdef _Fifo view = _Fifo.__new__(_Fifo)
        view._owner = self
        view._is_tx = True
        return view

    @property
    def _state(self):
        return I2CState(self._block.state)

    @_state.setter
    def _state(self, value):
        self._block.state = <uint32_t> (<int64_t> value)

    @property
    def _busy(self):
        return bool(self._block.busy)

    @_busy.setter
    def _busy(self, value):
        self._block.busy = bool(value)

    @property
    def _stop(self):
        return bool(self._block.stop)

    @_stop.setter
    def _stop(self, value):
        self._block.stop = bool(value)

    @property
    def _pending_restart(self):
        return bool(self._block.pending_restart)

    @_pending_restart.setter
    def _pending_restart(self, value):
        self._block.pending_restart = bool(value)

    @property
    def _first_byte(self):
        return bool(self._block.first_byte)

    @_first_byte.setter
    def _first_byte(self, value):
        self._block.first_byte = bool(value)

    @property
    def enable(self):
        return self._block.enable

    @enable.setter
    def enable(self, value):
        self._block.enable = <uint32_t> (<int64_t> value)

    @property
    def rx_threshold(self):
        return self._block.rx_threshold

    @rx_threshold.setter
    def rx_threshold(self, value):
        self._block.rx_threshold = <uint32_t> (<int64_t> value)

    @property
    def tx_threshold(self):
        return self._block.tx_threshold

    @tx_threshold.setter
    def tx_threshold(self, value):
        self._block.tx_threshold = <uint32_t> (<int64_t> value)

    @property
    def control(self):
        return self._block.control

    @control.setter
    def control(self, value):
        self._block.control = <uint32_t> (<int64_t> value)

    @property
    def ss_clock_high_period(self):
        return self._block.ss_clock_high

    @ss_clock_high_period.setter
    def ss_clock_high_period(self, value):
        self._block.ss_clock_high = <uint32_t> (<int64_t> value)

    @property
    def ss_clock_low_period(self):
        return self._block.ss_clock_low

    @ss_clock_low_period.setter
    def ss_clock_low_period(self, value):
        self._block.ss_clock_low = <uint32_t> (<int64_t> value)

    @property
    def fs_clock_high_period(self):
        return self._block.fs_clock_high

    @fs_clock_high_period.setter
    def fs_clock_high_period(self, value):
        self._block.fs_clock_high = <uint32_t> (<int64_t> value)

    @property
    def fs_clock_low_period(self):
        return self._block.fs_clock_low

    @fs_clock_low_period.setter
    def fs_clock_low_period(self, value):
        self._block.fs_clock_low = <uint32_t> (<int64_t> value)

    @property
    def target_address(self):
        return self._block.target_address

    @target_address.setter
    def target_address(self, value):
        self._block.target_address = <uint32_t> (<int64_t> value)

    @property
    def slave_address(self):
        return self._block.slave_address

    @slave_address.setter
    def slave_address(self, value):
        self._block.slave_address = <uint32_t> (<int64_t> value)

    @property
    def abort_source(self):
        return self._block.abort_source

    @abort_source.setter
    def abort_source(self, value):
        self._block.abort_source = <uint32_t> (<int64_t> value)

    @property
    def int_raw(self):
        return self._block.int_raw

    @int_raw.setter
    def int_raw(self, value):
        self._block.int_raw = <uint32_t> (<int64_t> value)

    @property
    def int_enable(self):
        return self._block.int_enable

    @int_enable.setter
    def int_enable(self, value):
        self._block.int_enable = <uint32_t> (<int64_t> value)

    @property
    def _spikelen(self):
        return self._block.spikelen

    @_spikelen.setter
    def _spikelen(self, value):
        self._block.spikelen = <uint32_t> (<int64_t> value)

    @property
    def sda_hold(self):
        return self._block.sda_hold

    @sda_hold.setter
    def sda_hold(self, value):
        self._block.sda_hold = <uint32_t> (<int64_t> value)

    @property
    def sda_setup(self):
        return self._block.sda_setup

    @sda_setup.setter
    def sda_setup(self, value):
        self._block.sda_setup = <uint32_t> (<int64_t> value)

    @property
    def ack_general_call(self):
        return self._block.ack_general_call

    @ack_general_call.setter
    def ack_general_call(self, value):
        self._block.ack_general_call = <uint32_t> (<int64_t> value)

    @property
    def slv_data_nack_only(self):
        return self._block.slv_data_nack_only

    @slv_data_nack_only.setter
    def slv_data_nack_only(self, value):
        self._block.slv_data_nack_only = <uint32_t> (<int64_t> value)

    @property
    def dma_control(self):
        return self._block.dma_control

    @dma_control.setter
    def dma_control(self, value):
        self._block.dma_control = <uint32_t> (<int64_t> value)

    @property
    def dma_tdlr(self):
        return self._block.dma_tdlr

    @dma_tdlr.setter
    def dma_tdlr(self, value):
        self._block.dma_tdlr = <uint32_t> (<int64_t> value)

    @property
    def dma_rdlr(self):
        return self._block.dma_rdlr

    @dma_rdlr.setter
    def dma_rdlr(self, value):
        self._block.dma_rdlr = <uint32_t> (<int64_t> value)

    @property
    def int_status(self):
        return self._block.int_status()

    @property
    def speed(self):
        return I2CSpeed((self._block.control >> SPEED_SHIFT) & SPEED_MASK)

    @property
    def scl_low_period(self):
        return self._block.ss_clock_low if self.speed == SPEED_STANDARD else self._block.fs_clock_low

    @property
    def scl_high_period(self):
        return self._block.ss_clock_high if self.speed == SPEED_STANDARD else self._block.fs_clock_high

    @property
    def master_bits(self):
        return self._block.master_bits()

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
        """Registers, both FIFOs and the bus state machine, back to power-on (0089 Phase 5). The five `on_*` callbacks are wiring - whatever is on the bus - and survive."""
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
