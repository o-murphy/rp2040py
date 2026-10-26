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

The API is the reference's: `channels` (views of the C++ channels: `index`, `active`, `start`, `abort`, `schedule_transfer` and the channel's registers), `snapshot()` (the whole state in one call),
`set_dreq/clear_dreq`, `get_timer`, `check_interrupts`, `reset`, plus `BasePeripheral`'s surface (name, rp2040, clock, read_uint32, write_uint32, write_uint32_atomic, raw_write_value, warn/...).

A DMA needs the native chip (its bus) and the native `SimulationClock` (its alarms are nodes of the C++ clock); a native
`RP2040` always has both.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._bus cimport Bus
from rp2040py.native._mcu_irq cimport Mcu
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPDMA",)


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


cdef class RPDMA(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
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
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already and bound it to the tree and the bus - this object is the Python view of it
            self._block = <DmaBlock*> <size_t> rp2040._native_dma_address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPDMA is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
        self._channels = []
        cdef _Channel view
        for i in range(12):
            view = _Channel.__new__(_Channel)
            view._owner = self
            view._channel = &self._block.channels[i]
            self._channels.append(view)

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def channels(self):
        return self._channels

    def snapshot(self):
        """The controller's state in one call: every channel's registers and internals, the asserted DREQs, the raw interrupt word and the two statuses."""
        cdef DmaChannel* c
        cdef uint32_t i
        channels = []
        for i in range(12):
            c = &self._block.channels[i]
            channels.append((
                c.ctrl,
                c.read_addr,
                c.write_addr,
                c.trans_count,
                c.trans_count_reload,
                c.dreq_counter,
                c.treq,
                c.data_size,
                c.chain_to,
                c.ring_mask,
                bool(c.active()),
            ))
        return {
            "channels": tuple(channels),
            "dreq": tuple([i for i in range(64) if self._block.dreq(i)]),
            "int_raw": self._block.int_raw,
            "int_status0": self._block.int_status0(),
            "int_status1": self._block.int_status1(),
        }

    # --- BasePeripheral's surface -----------------------------------------------------------------

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

    # --- the native bus protocol ------------------------------------------------------------------
