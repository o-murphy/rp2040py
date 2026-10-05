# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 SSI (the flash command path): a Python-facing shell over the C++ `SsiBlock` of `core/ssi.hpp`
(docs/records/0096-cpp-mcu-core.md, the flash-path design note). `peripherals/_ssi.py` is the pure-Python reference, kept as the
oracle (tests/test_ssi_diff.py); `peripherals/ssi.py` is the facade that picks between them, as for the other native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x18000] = ssi`) it registers the
block's own C++ read/write functions in the bus's window table (see `_native_window`), so a bootrom's DR0/SR polling never touches
Python. Chip-select is the QSPI_SS pin: when that is a native `GPIOPin` the block is a *direct listener* of it (an edge never
enters Python); anything else gets a Python listener that forwards the level. The flash is the chip's own buffer, by pointer.

The reference's private state is kept readable under its names (`_ssienr`, `_write_enabled`, `_cs_asserted`, `_rx_queue`,
`_tx_buffer`, ...) as views of the C++ state: the tests and the oracle look at them. `_tx_buffer` shows the first 260 bytes of the
command (all the block keeps) and the *count* as its length.
"""

from cpython.ref cimport Py_DECREF, Py_INCREF
from libc.stdint cimport int64_t, uint8_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._gpio_pin cimport GPIOPin
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.gpio_pin import GPIOPinState

__all__ = ("RPSSI",)


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPSSI ssi = <RPSSI> ctx
    try:
        if kind == kSsiWarnRead:
            ssi.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kSsiWarnReadAtomicArea:
            ssi.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            ssi.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef cppbool _cs_low_trampoline(void* ctx, cppbool* low) noexcept:
    cdef RPSSI ssi = <RPSSI> ctx
    try:
        low[0] = ssi.rp2040.qspi[1].value == GPIOPinState.LOW
    except BaseException as error:
        park_error(error)
        return False
    return True


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
    def __cinit__(self, *args, **kwargs):
        self._pin_keepalive = NULL

    def __init__(self, rp2040, name):
        cdef SsiHost host
        cdef GPIOPin pin
        native_flash = getattr(rp2040, "_native_flash_address", None)
        if native_flash is None:
            raise TypeError(
                f"the native SSI works on the native chip's flash buffer; {type(rp2040).__name__} is not that "
                "(use the pure-Python peripherals/_ssi.RPSSI with it)"
            )
        flash_address, flash_size = native_flash()
        self.rp2040 = rp2040
        self.name = name
        self._cs_pin = rp2040.qspi[1]
        self._cs_native = False
        self._cs_listening = False
        host.warn = _warn_trampoline
        host.cs_low = _cs_low_trampoline
        host.ctx = <void*> self
        self._block.init(<uint8_t*> <size_t> flash_address, <uint32_t> flash_size, host)
        if isinstance(self._cs_pin, GPIOPin):
            pin = <GPIOPin> self._cs_pin
            # The block keeps a raw pointer into the pin's bank and takes itself off it in __dealloc__. A reference the garbage collector's tp_clear cannot
            # drop keeps the pin alive until then (it would otherwise be freed first, with this block still on its bank). No cycle: the pin never refers back.
            Py_INCREF(pin)
            self._pin_keepalive = <void*> pin
            if not self._block.bind_cs(pin.bank_ptr()):
                raise_if_pending()
            self._cs_native = True
            self._cs_listening = True
        else:
            if not self._block.reset():
                raise_if_pending()
            self._cs_pin.add_listener(self._on_cs_pin_changed)
            self._cs_listening = True

    def _on_cs_pin_changed(self, value, _last_value):
        self._block.on_cs_change(value == GPIOPinState.LOW)

    def _detach(self):
        """Takes the block off the chip-select pin (the oracle's rig replaces an SSI after construction; a second one listening would apply every
        command to the same flash twice)."""
        cdef GPIOPin pin
        if not self._cs_listening:
            return
        if self._cs_native:
            self._block.detach()
        else:
            self._cs_pin._listeners.discard(self._on_cs_pin_changed)
        self._cs_listening = False

    def __dealloc__(self):
        if self._cs_native and self._cs_listening:
            self._block.detach()
        if self._pin_keepalive != NULL:
            Py_DECREF(<object> self._pin_keepalive)
            self._pin_keepalive = NULL

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
