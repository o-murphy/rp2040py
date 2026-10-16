# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 PLL (PLL_SYS and PLL_USB are two instances): a Python-facing shell over the C++ `PllBlock` of `core/pll.hpp` (docs/records/0096-cpp-mcu-core.md, docs/tasks/clocks-port.md).
`peripherals/_pll.py` is the pure-Python reference, kept as the oracle; `peripherals/pll.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a register
access never enters Python. What stays Python is the edge: the logger, the crystal's output (`ref_hz`: the XOSC block's STABLE bit and the chip's `xosc_freq`) and the clock tree that is re-derived after
a register write (`changed`: the chip's `update_clocks()`). Their failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPPLL",)

XOSC_STATUS = 0x04
XOSC_STATUS_STABLE = 1 << 31


cdef double _ref_trampoline(void* ctx) noexcept:
    # `xosc_hz()` of the reference: the crystal's frequency while the XOSC block is stable, nothing otherwise.
    cdef RPPLL block = <RPPLL> ctx
    try:
        rp2040 = block.rp2040
        return <double> rp2040.xosc_freq if rp2040.xosc.read_uint32(XOSC_STATUS) & XOSC_STATUS_STABLE else 0.0
    except BaseException as error:
        park_error(error)
        return 0.0


cdef class RPPLL:
    def __init__(self, rp2040, name, mcu_index=-1):
        self.rp2040 = rp2040
        self.name = name
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus and its notices to the clock tree - and this object is the Python view of it
            self._block = <PllBlock*> <size_t> rp2040._native_pll_address(mcu_index)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPPLL is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def _rejoin_the_python_tree(self, tree):
        """`tree` is going away (the chip's clock tree was replaced): ask the chip through Python for the reference again, if the PLL was asking that tree."""
        if self._bound_tree is not tree:
            return
        self._bound_tree = None
        self._block.bind_reference(_ref_trampoline, <void*> self)

    def _warn_kind(self, kind, offset, value):
        """A register access the block does not implement, as the logger's message (the chip's host calls this for the block it built)."""
        if kind == kPllWarnRead:
            self.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kPllWarnReadAtomicArea:
            self.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            self.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        if not self._block.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

    def reset(self):
        self._block.reset()

    @property
    def refdiv(self):
        return self._block.refdiv()

    @property
    def fbdiv(self):
        return self._block.fbdiv()

    @property
    def postdiv1(self):
        return self._block.postdiv1()

    @property
    def postdiv2(self):
        return self._block.postdiv2()

    @property
    def reference(self):
        value = self._block.reference()
        raise_if_pending()
        return value

    @property
    def locked(self):
        value = bool(self._block.locked())
        raise_if_pending()
        return value

    @property
    def frequency(self):
        value = self._block.frequency()
        raise_if_pending()
        return value

    @property
    def cs(self):
        return self._block.cs

    @cs.setter
    def cs(self, value):
        self._block.cs = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def pwr(self):
        return self._block.pwr

    @pwr.setter
    def pwr(self, value):
        self._block.pwr = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def fbdiv_int(self):
        return self._block.fbdiv_int

    @fbdiv_int.setter
    def fbdiv_int(self, value):
        self._block.fbdiv_int = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def prim(self):
        return self._block.prim

    @prim.setter
    def prim(self, value):
        self._block.prim = <uint32_t> (value & 0xFFFFFFFF)

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    # --- the native bus protocol -----------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the block's
        own read/write functions and its context. Looked up on the *type* by the bus, so a wrapper that merely forwards attributes (a recorder,
        a profiler) cannot lend its target's fast path and be bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
