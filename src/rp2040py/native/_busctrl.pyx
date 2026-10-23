# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 BUSCTRL: a Python-facing shell over the C++ `BusctrlBlock` of `core/busctrl.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_busctrl.py` is the pure-Python reference, kept as the oracle (tests/test_small_blocks_diff.py); `peripherals/busctrl.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a
register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._views cimport WordsView
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPBUSCTRL",)


cdef class RPBUSCTRL(NativeBlock):
    def __init__(self, rp2040, name):
        cdef WordsView view
        self.rp2040 = rp2040
        self.name = name
        address = getattr(rp2040, "_native_busctrl_address", None)
        self._from_mcu = address is not None
        if address is not None:
            # the chip's C++ `Mcu` has built the block already - with its window on the bus - and this object is the Python view of it
            self._block = <BusctrlBlock*> <size_t> address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPBUSCTRL is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
        self.voltage_select = 0  # kept for the reference's attribute set; nothing reads it
        view = WordsView.__new__(WordsView)
        view._words = self._block.perf_ctr
        view._n = 4
        self.perf_ctr = view
        view = WordsView.__new__(WordsView)
        view._words = self._block.perf_sel
        view._n = 4
        self.perf_sel = view

    @property
    def bus_priority(self):
        return self._block.bus_priority

    @bus_priority.setter
    def bus_priority(self, value):
        self._block.bus_priority = <uint32_t> (value & 0xFFFFFFFF)

    def reset(self):
        """`RESETS_RESET_BUSCTRL` (0089 Phase 5): the priority register, the four counters and their selectors back to reset."""
        self.voltage_select = 0
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
