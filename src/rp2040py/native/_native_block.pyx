# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""What every shell over a C++ register block (`core/core_host.hpp`'s `RegisterBlock`) has in common (docs/records/0096-cpp-mcu-core.md, Phase 5b): the chip and the
name it logs under, the logger, and the register-access surface of `BasePeripheral` - `read_uint32`, `write_uint32`, `write_uint32_atomic`, `raw_write_value`, `reset` - plus the
bus protocol `_native_window`.

A shell derives from `NativeBlock`, points `self._block` at the chip's C++ block and says `self._ops = register_ops(self._block)`; it then defines only what is its own (the
reference's other attributes, its callbacks, a `reset` that does more than put the block back). A shell whose block does not fit the template (a `read` that can fail, say) never
fills `_ops` and defines these methods itself.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("NativeBlock",)


cdef class NativeBlock:
    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    @property
    def raw_write_value(self):
        if self._ops.raw_write_value == NULL:
            raise TypeError(f"{type(self).__name__} did not set up its register ops")
        return self._ops.raw_write_value(self._ops.block)

    def read_uint32(self, offset):
        cdef uint32_t value
        if self._ops.read == NULL:
            raise TypeError(f"{type(self).__name__} did not set up its register ops")
        value = self._ops.read(self._ops.block, <uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        if self._ops.write == NULL:
            raise TypeError(f"{type(self).__name__} did not set up its register ops")
        self._ops.write(self._ops.block, <uint32_t> offset, <int64_t> value)
        raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if self._ops.write_atomic == NULL:
            raise TypeError(f"{type(self).__name__} did not set up its register ops")
        self._ops.write_atomic(self._ops.block, <uint32_t> offset, <int64_t> value, <uint32_t> atomic_type)
        raise_if_pending()

    def reset(self):
        if self._ops.reset == NULL:
            raise TypeError(f"{type(self).__name__} did not set up its register ops")
        self._ops.reset(self._ops.block)
        raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the block's own
        read/write functions and its context. Looked up on the *type* by the bus, so a wrapper that merely forwards attributes (a recorder, a profiler)
        cannot lend its target's fast path and be bypassed."""
        cdef WindowHandler handler
        if self._ops.window == NULL:
            raise TypeError(f"{type(self).__name__} did not set up its register ops")
        handler = self._ops.window(self._ops.block)
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
