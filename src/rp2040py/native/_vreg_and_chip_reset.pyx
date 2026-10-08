# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 VREG_AND_CHIP_RESET: a Python-facing shell over the C++ `VregBlock` of `core/vreg.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_vreg_and_chip_reset.py` is the pure-Python reference, kept as the oracle (tests/test_small_blocks_diff.py); `peripherals/vreg_and_chip_reset.py` is the facade that picks
between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window
table (see `_native_window`), so a register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._vreg_and_chip_reset import HAD_POR, HAD_PSM_RESTART, HAD_RUN

__all__ = ("RPVREGAndChipReset",)


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPVREGAndChipReset block = <RPVREGAndChipReset> ctx
    try:
        if kind == kVregWarnRead:
            block.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kVregWarnReadAtomicArea:
            block.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            block.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class RPVREGAndChipReset:
    def __init__(self, rp2040, name):
        cdef VregHost host
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
    def vreg(self):
        return self._block.vreg

    @vreg.setter
    def vreg(self, value):
        self._block.vreg = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def bod(self):
        return self._block.bod

    @bod.setter
    def bod(self, value):
        self._block.bod = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def chip_reset(self):
        return self._block.chip_reset

    @chip_reset.setter
    def chip_reset(self, value):
        self._block.chip_reset = <uint32_t> (value & 0xFFFFFFFF)

    def record_reset_cause(self, flag):
        """Record which chip-level reset just happened (see the reference): `flag` is one of HAD_POR / HAD_RUN / HAD_PSM_RESTART."""
        assert flag in (HAD_POR, HAD_RUN, HAD_PSM_RESTART), f"not a CHIP_RESET cause flag: 0x{flag:x}"
        self._block.record_reset_cause(<uint32_t> flag)

    # no reset(): the block is not reset by RESETS, as the reference's is not (`BasePeripheral`'s no-op)
    def reset(self):
        pass

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
