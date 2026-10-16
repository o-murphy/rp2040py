# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 PADS_BANK0 / PADS_QSPI: a Python-facing shell over the C++ `PadsBlock` of `core/pads.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_pads.py` is the pure-Python reference, kept as the oracle (tests/test_pin_diff.py); `peripherals/pads.py` is the facade that picks between them.

Like `native/_io.pyx`: the block works on the `PinBank` the chip's native pins share, and the chip registers its C++ read/write functions in the bus's window table. The pins it
reaches are `rp2040.gpio` (bank0) or `rp2040.qspi` (qspi) at construction, which must be native pins of one bank.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._gpio_pin cimport GPIOPin, GPIOPinBank
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPPADS",)

cdef uint32_t _WARN_READ = 0, _WARN_READ_ATOMIC = 1


cdef class RPPADS:
    def __init__(self, rp2040, name, bank, mcu_index=-1):
        cdef GPIOPin pin
        cdef GPIOPinBank owner = None
        cdef Py_ssize_t i
        pins = list(rp2040.qspi if bank == "qspi" else rp2040.gpio)
        for i in range(len(pins)):
            if not isinstance(pins[i], GPIOPin):
                raise TypeError(
                    f"the native pads block works on the native pin bank; {type(pins[i]).__name__} is not a native GPIOPin "
                    "(use the pure-Python peripherals/_pads.RPPADS with it)"
                )
            pin = <GPIOPin> pins[i]
            if owner is None:
                owner = pin._owner
            if pin._owner is not owner or pin._slot != i or len(pins) != len(owner.shells):
                raise TypeError("the native pads block needs every pin of one bank, in order (use the pure-Python peripherals/_pads.RPPADS with other pins)")
        self.rp2040 = rp2040
        self.name = name
        self.bank = bank
        self._owner = owner
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` holds this bank (and the IO and PADS blocks on it): this object is the Python view of it
            self._block = <PadsBlock*> <size_t> rp2040._native_pads_address(mcu_index)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPPADS is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def _warn_kind(self, kind, offset, value):
        """A register access the block does not implement, as the logger's message (the chip's host calls this for the block it built)."""
        if kind == _WARN_READ:
            self.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == _WARN_READ_ATOMIC:
            self.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            self.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def voltage_select(self):
        return self._block.voltage_select

    @voltage_select.setter
    def voltage_select(self, value):
        self._block.voltage_select = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def swclk(self):
        return self._block.swclk

    @swclk.setter
    def swclk(self, value):
        self._block.swclk = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def swd(self):
        return self._block.swd

    @swd.setter
    def swd(self, value):
        self._block.swd = <uint32_t> (value & 0xFFFFFFFF)

    def read_uint32(self, offset):
        cdef uint32_t value = 0
        if not self._block.read(<uint32_t> offset, &value):
            raise_if_pending()
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        if not self._block.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

    def reset(self):
        """`VOLTAGE_SELECT` and the debug pads, the only state this block owns itself - the per-pad registers live on the pins and are reset with them."""
        self._block.reset()

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
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline (see `native/_rosc.pyx`)."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
