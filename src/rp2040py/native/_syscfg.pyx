# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 SYSCFG: a Python-facing shell over the C++ `SyscfgBlock` of `core/syscfg.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_syscfg.py` is the pure-Python reference, kept as the oracle (tests/test_small_blocks_diff.py); `peripherals/syscfg.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RP2040SysCfg",)


cdef class RP2040SysCfg(NativeBlock):
    def __init__(self, rp2040, name):
        address = getattr(rp2040, "_native_syscfg_address", None)
        self._from_mcu = address is not None
        if address is not None:
            # the chip's C++ `Mcu` has built the block already - on its own CPU, with its window on the bus - and this object is the Python view of it
            self.rp2040 = rp2040
            self.name = name
            self._block = <SyscfgBlock*> <size_t> address()
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RP2040SysCfg is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def snapshot(self):
        cdef uint32_t w[6]
        self._block.snapshot(w)
        return {
            "proc1_nmi_mask": w[0],
            "proc_config": w[1],
            "proc_in_sync_bypass": w[2],
            "proc_in_sync_bypass_hi": w[3],
            "dbgforce": w[4],
            "mempowerdown": w[5],
        }

    def reset(self):
        """`RESETS_RESET_SYSCFG`: every register back to its datasheet reset value, the core's NMI mask included."""
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
