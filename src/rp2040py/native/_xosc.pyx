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

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

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


cdef class RPXOSC:
    def __init__(self, rp2040, name, mcu_index=-1):
        self.rp2040 = rp2040
        self.name = name
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus and its notices to the clock tree - and this object is the Python view of it
            self._block = <XoscBlock*> <size_t> rp2040._native_xosc_address()
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
        if kind == kXoscWarnRead:
            self.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kXoscWarnReadAtomicArea:
            self.warn("Unimplemented read from peripheral in the atomic operation region")
        elif kind == kXoscWarnInvalidFreqRange:
            self.warn(f"Invalid FREQ_RANGE value written: 0x{value:x}")
        elif kind == kXoscWarnInvalidEnable:
            self.warn(f"Invalid ENABLE value written: 0x{value:x}")
        elif kind == kXoscWarnInvalidDormant:
            self.warn(f"Invalid DORMANT value written: 0x{value:x}")
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

    @property
    def _ctrl(self):
        return self._block.ctrl

    @_ctrl.setter
    def _ctrl(self, value):
        self._block.ctrl = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _status(self):
        return self._block.status

    @_status.setter
    def _status(self, value):
        self._block.status = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _dormant(self):
        return self._block.dormant

    @_dormant.setter
    def _dormant(self, value):
        self._block.dormant = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _startup(self):
        return self._block.startup

    @_startup.setter
    def _startup(self, value):
        self._block.startup = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _count(self):
        return self._block.count

    @_count.setter
    def _count(self, value):
        self._block.count = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _enabled(self):
        return bool(self._block.enabled)

    @_enabled.setter
    def _enabled(self, value):
        self._block.enabled = bool(value)

    @property
    def _stable(self):
        return bool(self._block.stable)

    @_stable.setter
    def _stable(self, value):
        self._block.stable = bool(value)

    @property
    def _is_dormant(self):
        return bool(self._block.is_dormant)

    @_is_dormant.setter
    def _is_dormant(self, value):
        self._block.is_dormant = bool(value)

    def reset(self):
        """Back to power-on: disabled, not stable, not dormant. Gated on `PSM.WDSEL`'s XOSC bit by `RP2040.reset()` (see the reference)."""
        self._block.reset()

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
