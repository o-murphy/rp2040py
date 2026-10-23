# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 XOSC: a Python-facing shell over the C++ `XoscBlock` of `core/xosc.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_xosc.py` is the pure-Python reference, kept as the oracle (tests/test_psm_xosc_resets_diff.py); `peripherals/xosc.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler
from rp2040py.native._register_warning import register_warning

__all__ = ("RPXOSC",)


cdef cppbool _stable_trampoline(void* ctx) noexcept:
    # The crystal started or stopped: the clock tree is re-derived (nothing is clocked from XOSC while it is stopped)
    cdef RPXOSC block = <RPXOSC> ctx
    try:
        block.rp2040.update_clocks()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class RPXOSC(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
        self.rp2040 = rp2040
        self.name = name
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus and its notices to the clock tree - and this object is the Python view of it
            self._block = <XoscBlock*> <size_t> rp2040._native_xosc_address()
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPXOSC is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def _rejoin_the_python_tree(self, tree):
        """`tree` is going away (the chip's clock tree was replaced): tell the chip through Python again, if this block was telling that tree."""
        if self._bound_tree is not tree:
            return
        self._bound_tree = None
        self._block.bind_stable_listener(_stable_trampoline, <void*> self)

    def _warn_kind(self, kind, offset, value):
        """A register access the block does not implement, as the logger's message (the chip's host calls this for the block it built)."""
        if kind == kXoscWarnInvalidFreqRange:
            self.warn(f"Invalid FREQ_RANGE value written: 0x{value:x}")
        elif kind == kXoscWarnInvalidEnable:
            self.warn(f"Invalid ENABLE value written: 0x{value:x}")
        elif kind == kXoscWarnInvalidDormant:
            self.warn(f"Invalid DORMANT value written: 0x{value:x}")
        else:
            self.warn(register_warning(kind, offset, value))

    def snapshot(self):
        cdef uint32_t w[8]
        self._block.snapshot(w)
        return {
            "ctrl": w[0],
            "status": w[1],
            "dormant": w[2],
            "startup": w[3],
            "count": w[4],
            "enabled": bool(w[5]),
            "stable": bool(w[6]),
            "is_dormant": bool(w[7]),
        }

    def reset(self):
        """Back to power-on: disabled, not stable, not dormant. Gated on `PSM.WDSEL`'s XOSC bit by `RP2040.reset()` (see the reference)."""
        self._block.reset()

    # --- the native bus protocol -----------------------------------------------------------------
