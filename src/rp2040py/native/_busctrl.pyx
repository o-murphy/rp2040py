# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 BUSCTRL: a Python-facing shell over the C++ `BusctrlBlock` of `core/busctrl.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_busctrl.py` is the pure-Python reference, kept as the oracle (tests/test_small_blocks_diff.py); `peripherals/busctrl.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a
register access never enters Python. What stays Python is the edge: the logger.

The shell shows no registers as attributes: the state is read as `snapshot()` (one crossing, the same dict as the reference's), and the one input the bus cannot give, a counter that counted, is `set_perf_counter()`.
"""

from libc.stdint cimport uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops

__all__ = ("RPBUSCTRL",)


cdef class RPBUSCTRL(NativeBlock):
    def __init__(self, rp2040, name):
        self.rp2040 = rp2040
        self.name = name
        address = getattr(rp2040, "_native_busctrl_address", None)
        self._from_mcu = address is not None
        if address is None:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPBUSCTRL is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
        # the chip's C++ `Mcu` has built the block already - with its window on the bus - and this object is the Python view of it
        self._block = <BusctrlBlock*> <size_t> address()
        self._ops = register_ops(self._block)

    def snapshot(self):
        cdef uint32_t words[9]
        self._block.snapshot(words)
        return {"bus_priority": words[0], "perf_ctr": (words[1], words[2], words[3], words[4]), "perf_sel": (words[5], words[6], words[7], words[8])}

    def set_perf_counter(self, index, value):
        if not 0 <= index < 4:
            raise IndexError("list assignment index out of range")
        self._block.set_perf_counter(<uint32_t> index, <uint32_t> (value & 0xFFFFFFFF))
