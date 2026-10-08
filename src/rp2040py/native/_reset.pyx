# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 RESETS: a Python-facing shell over the C++ `ResetsBlock` of `core/resets.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_reset.py` is the pure-Python reference, kept as the oracle (tests/test_psm_xosc_resets_diff.py); `peripherals/reset.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPReset",)


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPReset block = <RPReset> ctx
    try:
        if kind == kResetsWarnRead:
            block.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kResetsWarnReadAtomicArea:
            block.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            block.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class RPReset:
    def __init__(self, rp2040, name):
        cdef ResetsHost host
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
        """Which peripheral blocks a *watchdog* reset resets directly (see the reference)."""
        return self._block.wdsel

    @wdsel.setter
    def wdsel(self, value):
        self._block.wdsel = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _reset(self):
        return self._block.reset_bits

    @_reset.setter
    def _reset(self, value):
        self._block.reset_bits = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def _wdsel(self):
        return self._block.wdsel

    @_wdsel.setter
    def _wdsel(self, value):
        self._block.wdsel = <uint32_t> (value & 0xFFFFFFFF)

    def reset(self):
        """The reset controller has no reset of its own here (`BasePeripheral`'s no-op)."""

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
