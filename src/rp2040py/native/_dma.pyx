# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 DMA: a Python-facing shell over the C++ `DmaBlock` of `core/dma.hpp`
(docs/records/0096-cpp-mcu-core.md, Phase 4). `peripherals/_dma.py` is the pure-Python reference, kept as the oracle
(tests/test_dma_diff.py); `peripherals/dma.py` is the facade that picks between them, as for the other native ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x50000] = dma`) it registers
the block's own C++ read/write functions in the bus's window table (see `_native_window`). A transfer is a read and a write
on the chip's C++ bus and the channel's alarm is a node of the chip's C++ clock, so a DMA running between RAM and a
peripheral that is native too never touches Python. What stays Python is the edge of the block: the two interrupt lines
(`rp2040.set_interrupt`), `rp2040.clk_sys` (asked only when a pacing timer is actually running) and the logger, reached through
trampolines whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in.

The API is the reference's: `channels` (views of the C++ channels with the reference's attribute names), `dreq` (a read-only
view of the asserted DREQs), `int_raw`, `int_status0/1`, `set_dreq/clear_dreq`, `get_timer`, `check_interrupts`, `reset`, plus
`BasePeripheral`'s surface (name, rp2040, clock, read_uint32, write_uint32, write_uint32_atomic, raw_write_value, warn/...).

A DMA needs the native chip (its bus) and the native `SimulationClock` (its alarms are nodes of the C++ clock); a native
`RP2040` always has both.
"""

from cpython.ref cimport Py_DECREF, Py_INCREF
from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._bus cimport Bus
from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.irq import IRQ

__all__ = ("RPDMA",)


cdef cppbool _irq_trampoline(void* ctx, uint32_t line, cppbool level) noexcept:
    cdef RPDMA dma = <RPDMA> ctx
    try:
        dma.rp2040.set_interrupt(line, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef double _clk_sys_trampoline(void* ctx) noexcept:
    cdef RPDMA dma = <RPDMA> ctx
    try:
        return <double> dma.rp2040.clk_sys
    except BaseException as error:
        park_error(error)
        return 0.0


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPDMA dma = <RPDMA> ctx
    try:
        if kind == kDmaWarnRead:
            dma.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kDmaWarnReadAtomicArea:
            dma.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            dma.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class _Channel:
    """A view of one C++ channel with the reference channel's attribute names (`_ctrl`, `_read_addr`, ...) and methods."""

    cdef RPDMA _owner
    cdef DmaChannel* _channel

    @property
    def dma(self):
        return self._owner

    @property
    def rp2040(self):
        return self._owner.rp2040

    @property
    def index(self):
        return self._channel.index

    @property
    def _ctrl(self):
        return self._channel.ctrl

    @property
    def _read_addr(self):
        return self._channel.read_addr

    @property
    def _write_addr(self):
        return self._channel.write_addr

    @property
    def _trans_count(self):
        return self._channel.trans_count

    @property
    def _trans_count_reload(self):
        return self._channel.trans_count_reload

    @property
    def _dreq_counter(self):
        return self._channel.dreq_counter

    @property
    def _treq_value(self):
        return self._channel.treq

    @property
    def _data_size(self):
        return self._channel.data_size

    @property
    def _chain_to(self):
        return self._channel.chain_to

    @property
    def _ring_mask(self):
        return self._channel.ring_mask

    @property
    def treq(self):
        return self._channel.treq

    @property
    def active(self):
        return self._channel.active()

    def start(self):
        if not self._channel.start():
            raise_if_pending()

    def schedule_transfer(self):
        if not self._channel.schedule_transfer():
            raise_if_pending()

    def abort(self):
        self._channel.abort()

    def read_uint32(self, offset):
        return self._channel.read(<uint32_t> offset)

    def write_uint32(self, offset, value):
        if not self._channel.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def reset(self):
        if not self._channel.reset():
            raise_if_pending()


cdef class _DreqView:
    """A read-only view of the asserted DREQs, shaped like the reference's `dict[int, bool]` for what is looked at: a DREQ
    number is a key exactly while it is asserted (the reference also keeps a `False` entry for one that was cleared)."""

    cdef RPDMA _owner

    def get(self, key, default=None):
        cdef int64_t number = key
        if 0 <= number < 64 and self._owner._block.dreq(<uint32_t> number):
            return True
        return default

    def __getitem__(self, key):
        cdef int64_t number = key
        if 0 <= number < 64 and self._owner._block.dreq(<uint32_t> number):
            return True
        raise KeyError(key)

    def __contains__(self, key):
        cdef int64_t number
        try:
            number = key
        except (TypeError, OverflowError):
            return False
        return 0 <= number < 64 and self._owner._block.dreq(<uint32_t> number)

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self.keys())

    def keys(self):
        return [i for i in range(64) if self._owner._block.dreq(<uint32_t> i)]

    def items(self):
        return [(i, True) for i in self.keys()]

    def __repr__(self):
        return repr(dict(self.items()))


cdef class RPDMA:
    def __cinit__(self, *args, **kwargs):
        self._clock_keepalive = NULL

    def __init__(self, rp2040, name):
        cdef DmaHost host
        cdef Bus* bus
        clock = rp2040.clock
        bus_address = getattr(rp2040, "_native_bus_address", None)
        if not isinstance(clock, SimulationClock) or bus_address is None:
            raise TypeError(
                "the native DMA works on the native chip's bus and schedules its alarms on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_dma.RPDMA with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        # A reference the garbage collector's tp_clear cannot drop: __dealloc__ must still reach the clock to unlink
        # this block's alarms from it, and a cycle collection clears the object fields above first.
        Py_INCREF(clock)
        self._clock_keepalive = <void*> clock
        bus = <Bus*> <size_t> bus_address()
        host.irq = _irq_trampoline
        host.clk_sys = _clk_sys_trampoline
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        host.lines[0] = <uint32_t> int(IRQ.DMA_IRQ0)
        host.lines[1] = <uint32_t> int(IRQ.DMA_IRQ1)
        self._block.init(bus, &(<SimulationClock> clock)._clock, host)
        self._channels = []
        cdef _Channel view
        for i in range(12):
            view = _Channel.__new__(_Channel)
            view._owner = self
            view._channel = &self._block.channels[i]
            self._channels.append(view)
        cdef _DreqView dreq_view = _DreqView.__new__(_DreqView)
        dreq_view._owner = self
        self._dreq_view = dreq_view

    def __dealloc__(self):
        if self._clock_keepalive != NULL:
            self._block.detach()
            Py_DECREF(<object> self._clock_keepalive)
            self._clock_keepalive = NULL

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def channels(self):
        return self._channels

    @property
    def dreq(self):
        return self._dreq_view

    @property
    def int_raw(self):
        return self._block.int_raw

    @int_raw.setter
    def int_raw(self, value):
        self._block.int_raw = <uint32_t> (<int64_t> value)

    @property
    def int_status0(self):
        return self._block.int_status0()

    @property
    def int_status1(self):
        return self._block.int_status1()

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    # --- BasePeripheral's surface -----------------------------------------------------------------

    def read_uint32(self, offset):
        cdef uint32_t value = self._block.read(<uint32_t> offset)
        raise_if_pending()
        return value

    def write_uint32(self, offset, value):
        # A direct write keeps whatever raw_write_value the last atomic write left, as the pure-Python block does.
        if not self._block.write(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

    def reset(self):
        if not self._block.reset():
            raise_if_pending()

    def set_dreq(self, dreq_channel):
        cdef int64_t number = dreq_channel
        if number < 0:
            return
        if not self._block.set_dreq(<uint32_t> number):
            raise_if_pending()

    def clear_dreq(self, dreq_channel):
        cdef int64_t number = dreq_channel
        if number >= 0:
            self._block.clear_dreq(<uint32_t> number)

    def get_timer(self, treq):
        """The number of microseconds for a cycle of the given DMA timer, or 0 if the timer is disabled."""
        cdef cppbool ok
        cdef double result = self._block.get_timer(<uint32_t> treq, &ok)
        if not ok:
            raise_if_pending()
        return result

    def check_interrupts(self):
        if not self._block.check_interrupts():
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
