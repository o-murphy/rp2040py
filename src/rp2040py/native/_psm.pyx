# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 PSM: a Python-facing shell over the C++ `PsmBlock` of `core/psm.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_psm.py` is the pure-Python reference, kept as the oracle (tests/test_psm_xosc_resets_diff.py); `peripherals/psm.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPPSM",)


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPPSM block = <RPPSM> ctx
    try:
        if kind == kPsmWarnRead:
            block.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kPsmWarnReadAtomicArea:
            block.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            block.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class RPPSM:
    def __init__(self, rp2040, name):
        cdef PsmHost host
        self.rp2040 = rp2040
        self.name = name
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(host)

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
    def wdsel(self):
        """Which power-manager domains a *watchdog* reset resets - read by `RP2040.reset()` on the watchdog path (0089's D5)."""
        return self._block.wdsel

    @wdsel.setter
    def wdsel(self, value):
        self._block.wdsel = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _frce_on(self):
        return self._block.frce_on

    @_frce_on.setter
    def _frce_on(self, value):
        self._block.frce_on = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _frce_off(self):
        return self._block.frce_off

    @_frce_off.setter
    def _frce_off(self, value):
        self._block.frce_off = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _wdsel(self):
        return self._block.wdsel

    @_wdsel.setter
    def _wdsel(self, value):
        self._block.wdsel = <uint32_t> (value & 0xFFFFFFFF)

    def reset(self):
        """The block is not on the RESETS list: nothing to put back (`BasePeripheral`'s no-op)."""

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
