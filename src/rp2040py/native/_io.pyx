# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 IO_BANK0 / IO_QSPI: a Python-facing shell over the C++ `IoBlock` of `core/io.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_io.py` is the pure-Python reference, kept as the oracle (tests/test_pin_diff.py); `peripherals/io.py` is the facade that picks between them.

What a firmware sees is the C++ block, working on the `PinBank` the native pins share (`GPIOPinBank`): when the chip adopts this object it registers the block's own C++ read/write
functions in the bus's window table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger, and what a pin tells the host
(listeners, the IO interrupt line) - through the bank's own host. The `pins` must be the native `GPIOPin`s of one bank, in order; any other pin needs the reference.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._gpio_pin cimport GPIOPin, GPIOPinBank
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPIO",)



cdef class RPIO:
    def __init__(self, rp2040, name, pins=None, mcu_index=-1):
        cdef IoPadsHost host
        cdef GPIOPin pin
        cdef GPIOPinBank owner = None
        cdef Py_ssize_t i
        qspi_layout = pins is not None
        pins = list(pins) if pins is not None else list(rp2040.gpio)
        if not pins:
            raise TypeError("the native IO block needs the pins of a native pin bank")
        for i in range(len(pins)):
            if not isinstance(pins[i], GPIOPin):
                raise TypeError(
                    f"the native IO block works on the native pin bank; {type(pins[i]).__name__} is not a native GPIOPin "
                    "(use the pure-Python peripherals/_io.RPIO with it)"
                )
            pin = <GPIOPin> pins[i]
            if owner is None:
                owner = pin._owner
            if pin._owner is not owner or pin._slot != i or len(pins) != len(owner.shells):
                raise TypeError("the native IO block needs every pin of one bank, in order (use the pure-Python peripherals/_io.RPIO with other pins)")
        self.rp2040 = rp2040
        self.name = name
        self.pins = pins
        self._owner = owner
        self._count = len(pins)
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` holds this bank (and the IO and PADS blocks on it): this object is the Python view of it
            self._block = <IoBlock*> <size_t> rp2040._native_io_address(mcu_index)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPIO is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def _extra(self):
        """The proc 1 / dormant wake masks, as the reference keeps them: {dest: ([enable per pin], [force per pin])}."""
        cdef uint32_t i
        return {
            dest: (
                [self._block.extra(dest, False, i) for i in range(self._count)],
                [self._block.extra(dest, True, i) for i in range(self._count)],
            )
            for dest in (1, 2)
        }

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
        """The stored proc 1 / dormant wake masks back to zero (proc 0's are on the pins, reset with them)."""
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
