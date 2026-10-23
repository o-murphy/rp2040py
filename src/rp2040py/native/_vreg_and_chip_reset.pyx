# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 VREG_AND_CHIP_RESET: a Python-facing shell over the C++ `VregBlock` of `core/vreg.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_vreg_and_chip_reset.py` is the pure-Python reference, kept as the oracle (tests/test_small_blocks_diff.py); `peripherals/vreg_and_chip_reset.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._vreg_and_chip_reset import HAD_POR, HAD_PSM_RESTART, HAD_RUN

__all__ = ("RPVREGAndChipReset",)


cdef class RPVREGAndChipReset(NativeBlock):
    def __init__(self, rp2040, name):
        self.rp2040 = rp2040
        self.name = name
        address = getattr(rp2040, "_native_vreg_address", None)
        self._from_mcu = address is not None
        if address is not None:
            # the chip's C++ `Mcu` has built the block already - with its window on the bus - and this object is the Python view of it
            self._block = <VregBlock*> <size_t> address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPVREGAndChipReset is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
    def snapshot(self):
        cdef uint32_t w[3]
        self._block.snapshot(w)
        return {"vreg": w[0], "bod": w[1], "chip_reset": w[2]}

    def set_psm_restart_flag(self):
        """What a debugger's psm_restart does (see the reference)."""
        self._block.set_psm_restart_flag()

    def record_reset_cause(self, flag):
        """Record which chip-level reset just happened (see the reference): `flag` is one of HAD_POR / HAD_RUN / HAD_PSM_RESTART."""
        assert flag in (HAD_POR, HAD_RUN, HAD_PSM_RESTART), f"not a CHIP_RESET cause flag: 0x{flag:x}"
        self._block.record_reset_cause(<uint32_t> flag)

    # no reset(): the block is not reset by RESETS, as the reference's is not (`BasePeripheral`'s no-op)
    def reset(self):
        pass

    # --- the native bus protocol -----------------------------------------------------------------
