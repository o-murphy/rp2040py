# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 PADS_BANK0 / PADS_QSPI: a Python-facing shell over the C++ `PadsBlock` of `core/pads.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_pads.py` is the pure-Python reference, kept as the oracle (tests/test_pin_diff.py); `peripherals/pads.py` is the facade that picks between them.

Like `native/_io.pyx`: the block works on the `PinBank` the chip's native pins share, and the chip registers its C++ read/write functions in the bus's window table. The pins it
reaches are `rp2040.gpio` (bank0) or `rp2040.qspi` (qspi) at construction, which must be native pins of one bank.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._gpio_pin cimport GPIOPin, GPIOPinBank
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPPADS",)


cdef class RPPADS(NativeBlock):
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
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPPADS is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def snapshot(self):
        cdef uint32_t w[3]
        self._block.snapshot(w)
        return {"voltage_select": w[0], "swclk": w[1], "swd": w[2]}

    def read_uint32(self, offset):
        cdef uint32_t value = 0
        if not self._block.read(<uint32_t> offset, &value):
            raise_if_pending()
        raise_if_pending()
        return value

    def reset(self):
        """`VOLTAGE_SELECT` and the debug pads, the only state this block owns itself - the per-pad registers live on the pins and are reset with them."""
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
