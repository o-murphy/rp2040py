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

    @property
    def proc1_nmi_mask(self):
        return self._block.proc1_nmi_mask

    @proc1_nmi_mask.setter
    def proc1_nmi_mask(self, value):
        self._block.proc1_nmi_mask = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def proc_config(self):
        return self._block.proc_config

    @proc_config.setter
    def proc_config(self, value):
        self._block.proc_config = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def proc_in_sync_bypass(self):
        return self._block.proc_in_sync_bypass

    @proc_in_sync_bypass.setter
    def proc_in_sync_bypass(self, value):
        self._block.proc_in_sync_bypass = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def proc_in_sync_bypass_hi(self):
        return self._block.proc_in_sync_bypass_hi

    @proc_in_sync_bypass_hi.setter
    def proc_in_sync_bypass_hi(self, value):
        self._block.proc_in_sync_bypass_hi = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def dbgforce(self):
        return self._block.dbgforce

    @dbgforce.setter
    def dbgforce(self, value):
        self._block.dbgforce = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def mempowerdown(self):
        return self._block.mempowerdown

    @mempowerdown.setter
    def mempowerdown(self, value):
        self._block.mempowerdown = <uint32_t> (value & 0xFFFFFFFF)

    def reset(self):
        """`RESETS_RESET_SYSCFG`: every register back to its datasheet reset value, the core's NMI mask included."""
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
