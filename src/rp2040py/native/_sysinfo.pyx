# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 SYSINFO: a Python-facing shell over the C++ `SysInfoBlock` of `core/sysinfo.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_sysinfo.py` is the pure-Python reference, kept as the oracle (tests/test_ident_diff.py); `peripherals/sysinfo.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the bootrom's version byte (the CHIP_ID revision
follows the ROM that is loaded, and the ROM is loaded after the chip is built) and the logger. Failures of either are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RP2040SysInfo",)


cdef class RP2040SysInfo(NativeBlock):
    def __init__(self, rp2040, name):
        self.rp2040 = rp2040
        self.name = name
        address = getattr(rp2040, "_native_sysinfo_address", None)
        if address is not None:
            # the chip's C++ `Mcu` has built the block already - with its window on the bus - and this object is the Python view of it
            self._block = <SysInfoBlock*> <size_t> address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RP2040SysInfo is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
    def reset(self):
        """Nothing to put back: the registers are read-only chip identity (0089 Phase 5)."""
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
