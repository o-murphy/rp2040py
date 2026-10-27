# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 TBMAN: a Python-facing shell over the C++ `TbmanBlock` of `core/tbman.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_tbman.py` is the pure-Python reference, kept as the oracle (tests/test_ident_diff.py); `peripherals/tbman.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPTBMAN",)


cdef class RPTBMAN(NativeBlock):
    def __init__(self, rp2040, name):
        self.rp2040 = rp2040
        self.name = name
        address = getattr(rp2040, "_native_tbman_address", None)
        if address is not None:
            # the chip's C++ `Mcu` has built the block already - with its window on the bus - and this object is the Python view of it
            self._block = <TbmanBlock*> <size_t> address()
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPTBMAN is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def reset(self):
        """Nothing to put back: the registers are read-only chip identity (0089 Phase 5)."""
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
