# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 RESETS: a Python-facing shell over the C++ `ResetsBlock` of `core/resets.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_reset.py` is the pure-Python reference, kept as the oracle (tests/test_psm_xosc_resets_diff.py); `peripherals/reset.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPReset",)


cdef class RPReset(NativeBlock):
    def __init__(self, rp2040, name):
        self.rp2040 = rp2040
        self.name = name
        address = getattr(rp2040, "_native_resets_address", None)
        self._from_mcu = address is not None
        if address is not None:
            # the chip's C++ `Mcu` has built the block already - with its window on the bus - and this object is the Python view of it
            self._block = <ResetsBlock*> <size_t> address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPReset is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
    @property
    def wdsel(self):
        """Which peripheral blocks a *watchdog* reset resets directly (see the reference)."""
        return self._block.wdsel

    def snapshot(self):
        cdef uint32_t w[2]
        self._block.snapshot(w)
        return {"reset": w[0], "wdsel": w[1]}

    def reset(self):
        """The reset controller has no reset of its own here (`BasePeripheral`'s no-op)."""

    # --- the native bus protocol -----------------------------------------------------------------
