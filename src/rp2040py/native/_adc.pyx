# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 ADC: a Python-facing shell over the C++ `AdcBlock` of `core/adc.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 4).
`peripherals/_adc.py` is the pure-Python reference, kept as the oracle (tests/test_adc_diff.py); `peripherals/adc.py` is the facade that picks between them, as for the other native
ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x4004C] = adc`) it registers the block's own C++ read/write functions in the bus's
window table (see `_native_window`), so a register access never enters Python. The block's two alarms are nodes of the chip's C++ clock (like the DMA's: it needs the native
`SimulationClock`), so a conversion's sample time and a free-running capture's gaps run without Python. What stays Python is the edge of the block, reached through trampolines
whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in: the interrupt line (`rp2040.set_interrupt`), the DREQ (`rp2040.dma`), the device
callback `on_adc_read`, the analog inputs (`channel_values`, a plain Python list the embedder mutates; read when the sample alarm fires) and the logger.

`on_adc_read` is a plain attribute that hands back the very object that was set; with none set (or the shell's own `_default_on_adc_read` set again) the reference's default applies -
the sample comes out of `channel_values` after `sample_time` microseconds - and the trampoline runs it in C++ without calling Python. A device that finishes later calls
`complete_adc_read()` from a clock alarm, and one that finishes at once calls it from inside `on_adc_read`: the block is re-entered from its own host callback, as the reference is.

The API is the reference's: the registers' attributes (readable and writable), `fifo` (a view with the reference FIFO's attribute names), `sample_alarm`/`multi_shot_alarm` (views of
the block's two alarms), `num_channels`, `sample_time`, `resolution`, `divider`, `int_raw`, `int_status`, `start_adc_read`, `complete_adc_read`, `check_interrupts`, `reset`, plus
`BasePeripheral`'s surface.
"""

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._clock cimport Alarm
from rp2040py.native._mcu_irq cimport Mcu
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals._dma import DREQChannel

__all__ = ("RPADC",)

cdef uint32_t CS_TS_EN = 1 << 1
cdef uint32_t CS_EN = 1 << 0


cdef class _Fifo:
    """The conversion FIFO as the reference's `utils.fifo.FIFO(8)` shows it."""

    cdef RPADC _owner

    @property
    def size(self):
        return 8

    @property
    def item_count(self):
        return self._owner._block.fifo.count()

    @property
    def empty(self):
        return self._owner._block.fifo.empty()

    @property
    def full(self):
        return self._owner._block.fifo.full()

    @property
    def items(self):
        cdef Fifo8* fifo = &self._owner._block.fifo
        return [fifo.at(i) for i in range(fifo.count())]

    def push(self, value):
        self._owner._block.fifo.push(<uint32_t> (<int64_t> value))

    def pull(self):
        return self._owner._block.fifo.pull()

    def peek(self):
        return self._owner._block.fifo.peek()

    def reset(self):
        self._owner._block.fifo.reset()


cdef class _AlarmView:
    """One of the block's two alarms as the reference's `ClockAlarm` shows it: the node itself lives in the C++ block."""

    cdef RPADC _owner
    cdef Alarm* _node

    def schedule(self, double delta_nanos):
        self._owner._block.clock().schedule(self._node, delta_nanos)

    def cancel(self):
        self._owner._block.clock().cancel(self._node)

    @property
    def scheduled(self):
        return bool(self._node.scheduled)

    @property
    def nanos(self):
        return self._node.ticks / 16.0


cdef class RPADC(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native ADC schedules its alarms on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_adc.RPADC with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        self.channel_values = [0, 0, 0, 0, 0]
        self.resolution = 12
        self.dreq = DREQChannel.DREQ_ADC
        self._on_adc_read = None
        self._sample_time = 2
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` has built this block already - with its window on the bus, its line and request on the chip - and this object is the Python view of it
            self._block = <AdcBlock*> <size_t> rp2040._native_adc_address()
            self._ops = register_ops(self._block)
            return
        raise TypeError(
            "the native block is a view of the chip's C++ `Mcu` block: RPADC is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
        )

    def _world_read(self, channel):
        """A conversion was requested: the attached device answers; with none, the reference's default device does."""
        callback = self._on_adc_read
        if callback is None:
            self._block.default_adc_read(channel)
            return
        callback(channel)

    def _world_channel_value(self, channel):
        return int(self.channel_values[channel])

    # --- the device on the pins ------------------------------------------------------------------

    def _default_on_adc_read(self, channel):
        self._block.default_adc_read(<uint32_t> channel)

    @property
    def on_adc_read(self):
        if self._on_adc_read is None:
            return self._default_on_adc_read  # the reference's default
        return self._on_adc_read

    @on_adc_read.setter
    def on_adc_read(self, callback):
        # The default put back is the default: the trampoline runs it in C++.
        self._on_adc_read = None if callback == self._default_on_adc_read else callback

    def start_adc_read(self):
        if not self._block.start_adc_read():
            raise_if_pending()

    def complete_adc_read(self, value, error):
        if not self._block.complete_adc_read(<int64_t> value, bool(error)):
            raise_if_pending()

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def fifo(self):
        cdef _Fifo view = _Fifo.__new__(_Fifo)
        view._owner = self
        return view

    @property
    def sample_alarm(self):
        cdef _AlarmView view = _AlarmView.__new__(_AlarmView)
        view._owner = self
        view._node = &self._block.sample_alarm
        return view

    @property
    def multi_shot_alarm(self):
        cdef _AlarmView view = _AlarmView.__new__(_AlarmView)
        view._owner = self
        view._node = &self._block.multi_shot_alarm
        return view

    @property
    def num_channels(self):
        return self._block.num_channels

    @num_channels.setter
    def num_channels(self, value):
        self._block.num_channels = <int64_t> value

    @property
    def sample_time(self):
        return self._sample_time

    @sample_time.setter
    def sample_time(self, value):
        self._sample_time = value
        self._block.sample_time = <double> value

    @property
    def cs(self):
        return self._block.cs

    @cs.setter
    def cs(self, value):
        self._block.cs = <uint32_t> (<int64_t> value)

    @property
    def fcs(self):
        return self._block.fcs

    @fcs.setter
    def fcs(self, value):
        self._block.fcs = <uint32_t> (<int64_t> value)

    @property
    def clock_div(self):
        return self._block.clock_div

    @clock_div.setter
    def clock_div(self, value):
        self._block.clock_div = <uint32_t> (<int64_t> value)

    @property
    def int_enable(self):
        return self._block.int_enable

    @int_enable.setter
    def int_enable(self, value):
        self._block.int_enable = <uint32_t> (<int64_t> value)

    @property
    def int_force(self):
        return self._block.int_force

    @int_force.setter
    def int_force(self, value):
        self._block.int_force = <uint32_t> (<int64_t> value)

    @property
    def result(self):
        return self._block.result

    @result.setter
    def result(self, value):
        self._block.result = <int64_t> value

    @property
    def busy(self):
        return bool(self._block.busy)

    @busy.setter
    def busy(self, value):
        self._block.busy = bool(value)

    @property
    def current_channel(self):
        return self._block.current_channel

    @current_channel.setter
    def current_channel(self, value):
        self._block.current_channel = <uint32_t> (<int64_t> value)

    @property
    def temperature_enable(self):
        return self._block.cs & CS_TS_EN

    @property
    def enabled(self):
        return self._block.cs & CS_EN

    @property
    def divider(self):
        return self._block.divider()

    @property
    def int_raw(self):
        return self._block.int_raw()

    @property
    def int_status(self):
        return self._block.int_status()

    @property
    def _active_channel(self):
        return self._block.active_channel()

    @_active_channel.setter
    def _active_channel(self, channel):
        self._block.set_active_channel(<int64_t> channel)

    # --- BasePeripheral's surface ----------------------------------------------------------------

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

    def reset(self):
        """Registers, the FIFO and both alarms, back to power-on (0089 Phase 5). `channel_values` is the analog input a caller wired to the pins and `on_adc_read` is
        wiring: neither is chip state, and both survive."""
        if not self._block.reset():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------
