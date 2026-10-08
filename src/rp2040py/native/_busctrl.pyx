# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 BUSCTRL: a Python-facing shell over the C++ `BusctrlBlock` of `core/busctrl.hpp` (docs/records/0096-cpp-mcu-core.md).
`peripherals/_busctrl.py` is the pure-Python reference, kept as the oracle (tests/test_small_blocks_diff.py); `peripherals/busctrl.py` is the facade that picks between them.

What a firmware sees is the C++ block: when the chip adopts this object it registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a
register access never enters Python. What stays Python is the edge: the logger. Its failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPBUSCTRL",)

cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPBUSCTRL block = <RPBUSCTRL> ctx
    try:
        if kind == kBusctrlWarnRead:
            block.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kBusctrlWarnReadAtomicArea:
            block.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            block.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class _FourRegisters:
    """The reference's `perf_ctr` / `perf_sel` lists as a view of the C++ arrays: indexable, assignable (tests poke `busctrl.perf_ctr[1] = 99`) and equal to a list of the same values."""

    cdef uint32_t* _words

    def __len__(self):
        return 4

    def __getitem__(self, index):
        return list(self)[index]

    def __setitem__(self, index, value):
        cdef int i = range(4)[index]
        self._words[i] = <uint32_t> (value & 0xFFFFFFFF)

    def __iter__(self):
        for i in range(4):
            yield self._words[i]

    def __eq__(self, other):
        try:
            return list(self) == list(other)
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return repr(list(self))


cdef class RPBUSCTRL:
    def __init__(self, rp2040, name):
        cdef BusctrlHost host
        cdef _FourRegisters view
        self.rp2040 = rp2040
        self.name = name
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(host)
        self.voltage_select = 0  # kept for the reference's attribute set; nothing reads it
        view = _FourRegisters.__new__(_FourRegisters)
        view._words = self._block.perf_ctr
        self.perf_ctr = view
        view = _FourRegisters.__new__(_FourRegisters)
        view._words = self._block.perf_sel
        self.perf_sel = view

    @property
    def bus_priority(self):
        return self._block.bus_priority

    @bus_priority.setter
    def bus_priority(self, value):
        self._block.bus_priority = <uint32_t> (value & 0xFFFFFFFF)

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
    def raw_write_value(self):
        return self._block.raw_write_value()

    def reset(self):
        """`RESETS_RESET_BUSCTRL` (0089 Phase 5): the priority register, the four counters and their selectors back to reset."""
        self.voltage_select = 0
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
