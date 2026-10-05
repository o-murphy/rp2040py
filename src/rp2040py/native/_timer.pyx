# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 TIMER: a Python-facing shell over the C++ `TimerBlock` of `core/timer.hpp`
(docs/records/0096-cpp-mcu-core.md, Phase 2). `peripherals/_timer.py` is the pure-Python reference, kept as the
oracle; `peripherals/timer.py` is the facade that picks between them, as for the other native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x40054] = timer`) it
registers the block's own C++ read/write functions in the bus's window table (see `_native_window`), so a TIMELR
poll never touches Python at all. What stays Python is the edge of the block: the interrupt line (a call to
`rp2040.set_interrupt`) and the logger (`warn()` for what the block does not implement), reached through two
trampolines whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in.

The API is `BasePeripheral`'s (name, rp2040, clock, read_uint32, write_uint32, write_uint32_atomic, raw_write_value,
reset, warn/info/debug/error) plus `int_status`, so blocks, boards and tests treat it as before.

A TIMER needs the native `SimulationClock` (its alarms are nodes of the C++ clock); `RP2040` already insists on that
for its own `clock` field, so a native chip always has one.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.irq import IRQ

__all__ = ("RPTimer",)

_TIMER_IRQS = (int(IRQ.TIMER_0), int(IRQ.TIMER_1), int(IRQ.TIMER_2), int(IRQ.TIMER_3))


cdef bool _irq_trampoline(void* ctx, uint32_t line, bool level) noexcept:
    cdef RPTimer timer = <RPTimer> ctx
    try:
        timer.rp2040.set_interrupt(line, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPTimer timer = <RPTimer> ctx
    try:
        if kind == kTimerWarnRead:
            timer.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kTimerWarnReadAtomicArea:
            timer.warn("Unimplemented read from peripheral in the atomic operation region")
        elif kind == kTimerWarnWrite:
            timer.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
        else:
            timer.warn("Unimplemented Timer Pause")
    except BaseException as error:
        park_error(error)


cdef class RPTimer:
    def __init__(self, rp2040, name):
        cdef TimerHost host
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native TIMER schedules its alarms on the native SimulationClock; "
                f"{type(clock).__name__} is not one (use the pure-Python peripherals/_timer.RPTimer with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        host.irq = _irq_trampoline
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        for i in range(4):
            host.lines[i] = _TIMER_IRQS[i]
        self._block.init(&(<SimulationClock> clock)._clock, host)

    def __dealloc__(self):
        # Safe whichever of this block and its clock the collector frees first: the C++ clock unlinks its alarms when it dies.
        self._block.detach()

    # --- BasePeripheral's surface -----------------------------------------------------------------

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def int_status(self):
        return self._block.int_status()

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        # A direct write keeps whatever raw_write_value the last atomic write left, as the pure-Python block does.
        cdef int result = self._block.write32(<uint32_t> offset, <int64_t> value, self._block.raw_write_value())
        if result == 2:  # kWriteFailed: an interrupt-line call raised
            raise_if_pending()
        elif result == 1:  # kWriteUnhandled
            self.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")

    def write_uint32_atomic(self, offset, value, atomic_type):
        self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type)
        raise_if_pending()

    def reset(self):
        if not self._block.reset():
            raise_if_pending()

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    # --- the native bus protocol ------------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python
        trampoline: the addresses of the block's own read/write functions and its context. Looked up on the *type*
        by the bus, so a wrapper that merely forwards attributes (a recorder, a profiler) cannot lend its target's
        fast path and be bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
