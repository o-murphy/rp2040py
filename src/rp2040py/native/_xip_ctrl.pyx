# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 XIP_CTRL and XIP subsystem: a Python-facing shell over the C++ `XipBlock` of `core/xip_ctrl.hpp` (docs/records/0096-cpp-mcu-core.md,
docs/records/0098-datasheet-conformance-audit.md "XIP_CTRL and the XIP subsystem"). `peripherals/_xip_ctrl.py` is the pure-Python reference, kept as the oracle (tests/test_xip_diff.py);
`peripherals/xip_ctrl.py` is the facade that picks between them.

What a firmware sees is the C++ block, in three places the chip wires when it adopts this object: its register window (`_native_window`, as every native block), the flash window and the
`0x15...` cache-as-SRAM segment (the memory map's `XipHooks`, set by the `Mcu`) and the auxiliary slave `XIP_AUX` (`RPXIPAux`, a second window onto the same stream FIFO).
What stays Python is the edge: the logger (a bus fault the model cannot raise is reported as a warning), the DMA request line, and the two chip numbers the timing derives from (`clk_sys` and
SSI.BAUDR). Their failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in. The stream's alarm lives on the chip's C++ `Clock`, so the block needs the native
`SimulationClock`, and it reads the chip's own flash buffer by pointer.

The reference's state is kept readable under its names (`ctrl`, `ctr_hit`, `ctr_acc`, `stream_addr`, `stream_ctr`, `stream_fifo`, `tags`, `recent`, `sram`, ...) as views of the C++ state.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler
from rp2040py.native._register_warning import register_warning


__all__ = ("RPXIPAux", "RPXIPCtrl")


cdef class RPXIPCtrl:
    def __init__(self, rp2040, name):
        native_xip = getattr(rp2040, "_native_xip_address", None)
        if native_xip is None:
            raise TypeError(
                f"the native XIP_CTRL is a view of a block of the native chip's C++ core; {type(rp2040).__name__} is not that "
                "(use the pure-Python peripherals/_xip_ctrl.RPXIPCtrl with it)"
            )
        # the chip's `Mcu` builds the block: its window, the flash window's hooks, the XIP_AUX window, its DMA request and its pace (clk_sys, SSI.BAUDR) are wired inside
        self._from_mcu = True
        self._block = <XipBlock*> <size_t> native_xip()
        self.rp2040 = rp2040
        self.name = name
        self.clock = rp2040.clock

    def _warn_kind(self, kind, address, value):
        """A bus fault the model cannot raise, or a register access it does not implement, as the logger's message (the chip's host calls this for the block it built)."""
        if kind == kXipWarnBadWrite:
            self.warn(f"Write to the XIP alias at 0x{address:08x} (a bus fault with ERR_BADWRITE set)")
        elif kind == kXipWarnSramRead:
            self.warn(f"Read of the cache-as-SRAM at 0x{address:08x} while the cache is enabled or powered down (a bus error)")
        elif kind == kXipWarnSramWrite:
            self.warn(f"Write to the cache-as-SRAM at 0x{address:08x} while the cache is enabled or powered down (a bus error)")
        else:
            self.warn(register_warning(kind, address, value))

    def reset(self):
        """The XIP domain (`PSM.WDSEL`'s XIP bit, see `RP2040.reset()`): registers to power-on, the stream stopped, the cache flushed; the data RAM keeps its contents."""
        if not self._block.reset():
            raise_if_pending()

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
        raise_if_pending()

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()
        raise_if_pending()

    # --- the reference's state, as views -------------------------------------------------------------

    @property
    def ctrl(self):
        return self._block.ctrl

    @ctrl.setter
    def ctrl(self, value):
        self._block.ctrl = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def ctr_hit(self):
        return self._block.ctr_hit

    @ctr_hit.setter
    def ctr_hit(self, value):
        self._block.ctr_hit = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def ctr_acc(self):
        return self._block.ctr_acc

    @ctr_acc.setter
    def ctr_acc(self, value):
        self._block.ctr_acc = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def stream_addr(self):
        return self._block.stream_addr

    @stream_addr.setter
    def stream_addr(self, value):
        self._block.stream_addr = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def stream_ctr(self):
        return self._block.stream_ctr

    @stream_ctr.setter
    def stream_ctr(self, value):
        self._block.stream_ctr = <uint32_t> (value & 0xFFFFFFFF)

    @property
    def stream_fifo(self):
        return [self._block.fifo[i] for i in range(self._block.fifo_len)]

    @property
    def stream_in_flight(self):
        return bool(self._block.stream_in_flight)

    @property
    def flush_done_ticks(self):
        return self._block.flush_done_ticks

    @flush_done_ticks.setter
    def flush_done_ticks(self, value):
        self._block.flush_done_ticks = <int64_t> value

    @property
    def flush_ready(self):
        return bool(self._block.flush_ready())

    @property
    def stat(self):
        return self._block.stat()

    @property
    def tags(self):
        return [[self._block.tags[i][0], self._block.tags[i][1]] for i in range(1024)]

    @property
    def recent(self):
        return [self._block.recent[i] for i in range(1024)]

    @property
    def sram(self):
        return bytes(self._block.sram[:16384])

    @property
    def sram_available(self):
        return not (self._block.ctrl & 0x9)

    # --- the bus protocol ----------------------------------------------------------------------------

    def access(self, address):
        self._block.access(<uint32_t> address)
        raise_if_pending()

    def write_access(self, address):
        self._block.write_access(<uint32_t> address)
        raise_if_pending()

    def pop_stream_fifo(self):
        cdef uint32_t word = self._block.pop_stream_fifo()
        raise_if_pending()
        return word

    def debug(self, msg):
        self.rp2040.logger.debug(self.name, msg)

    def info(self, msg):
        self.rp2040.logger.info(self.name, msg)

    def warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def error(self, msg):
        self.rp2040.logger.error(self.name, msg)

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block instead of a Python trampoline (see `_rp2040.pyx`)."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)


cdef class RPXIPAux:
    """`XIP_AUX` (`0x50400000`): "a simple bus interface providing access only to the streaming FIFO" - a window onto the XIP block's FIFO."""

    cdef public object rp2040
    cdef public object name
    cdef RPXIPCtrl _xip

    def __init__(self, rp2040, name):
        xip = rp2040.xip_ctrl
        if not isinstance(xip, RPXIPCtrl):
            raise TypeError("the native XIP_AUX is a window onto the native XIP_CTRL's stream FIFO")
        self.rp2040 = rp2040
        self.name = name
        self._xip = xip

    def read_uint32(self, offset):
        return self._xip.pop_stream_fifo()

    def write_uint32(self, offset, value):
        pass  # a read-only slave

    def write_uint32_atomic(self, offset, value, atomic_type):
        pass

    def _native_window(self):
        cdef WindowHandler handler = self._xip._block.aux_window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
