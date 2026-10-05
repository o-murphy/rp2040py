# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 PWM: a Python-facing shell over the C++ `PwmBlock` of `core/pwm.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 4).
`peripherals/_pwm.py` is the pure-Python reference, kept as the oracle (tests/test_pwm_diff.py); `peripherals/pwm.py` is the facade that picks between them, as for the other native
ports.

What a firmware sees is the C++ block: when the chip adopts this object (`RP2040.peripherals[0x40050] = pwm`) it registers the block's own C++ read/write functions in the bus's
window table (see `_native_window`), so a register access never enters Python. The block's 24 alarms (three per channel, on the eight channels' `Timer32`s) are nodes of the chip's C++
clock (it needs the native `SimulationClock`), so a running PWM - the counters, the compare matches, the wraps - costs no Python. What stays Python is the edge of the block, reached
through trampolines whose failures are parked in the shared slot of `_pending.pyx` and re-raised by whoever called in: the interrupt line (`rp2040.set_interrupt`), the DMA request of a
wrapping channel (`rp2040.dma`), the pin a change was made to (`rp2040.gpio[i].check_for_updates()`), the level of a B input (`rp2040.gpio[i].input_value`) and the logger.

The pins read the block's two words, `gpio_value` and `gpio_direction`, as attributes of this object (the pin's trampoline looks `rp2040.pwm` up when it evaluates a level) and tell it
about a changed input through `gpio_on_input`; both are plain properties here.

The API is the reference's: `channels` (eight views with the reference channel's attributes: the registers, the double-buffer flags, the B-input bookkeeping, `timer` and the three
alarms), `gpio_value`, `gpio_direction`, the private interrupt words `_int_raw`/`_int_enable`/`_int_force`, `int_status`, `clock_freq`, `channel_interrupt`, `check_interrupts`,
`gpio_set`, `gpio_set_dir`, `gpio_read`, `gpio_on_input`, `reset`, plus `BasePeripheral`'s surface.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.irq import IRQ
from rp2040py.peripherals._pwm import PWMDivMode
from rp2040py.peripherals.dma import DREQChannel
from rp2040py.utils.timer32 import TimerMode

__all__ = ("RPPWM",)


cdef cppbool _irq_trampoline(void* ctx, cppbool level) noexcept:
    cdef RPPWM pwm = <RPPWM> ctx
    try:
        pwm.rp2040.set_interrupt(IRQ.PWM_WRAP, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _dreq_trampoline(void* ctx, uint32_t channel) noexcept:
    cdef RPPWM pwm = <RPPWM> ctx
    try:
        pwm.rp2040.dma.set_dreq(DREQChannel.DREQ_PWM_WRAP0 + channel)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _pin_changed_trampoline(void* ctx, uint32_t pin) noexcept:
    cdef RPPWM pwm = <RPPWM> ctx
    try:
        pwm.rp2040.gpio[<object> pin].check_for_updates()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _pin_read_trampoline(void* ctx, uint32_t pin, cppbool* level) noexcept:
    cdef RPPWM pwm = <RPPWM> ctx
    try:
        level[0] = bool(pwm.rp2040.gpio[<object> pin].input_value)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPPWM pwm = <RPPWM> ctx
    try:
        if kind == kPwmWarnRead:
            pwm.warn(f"Unimplemented peripheral read from 0x{offset:x}")
        elif kind == kPwmWarnReadAtomicArea:
            pwm.warn("Unimplemented read from peripheral in the atomic operation region")
        else:
            pwm.warn(f"Unimplemented peripheral write to 0x{offset:x}: 0x{value:x}")
    except BaseException as error:
        park_error(error)


cdef class _AlarmView:
    """One of a channel's three compare alarms as the reference's `Timer32PeriodicAlarm` shows it (`target`, `enable`): the state lives in the C++ block."""

    cdef RPPWM _owner
    cdef Timer32PeriodicAlarm* _alarm

    @property
    def target(self):
        return self._alarm.target()

    @target.setter
    def target(self, value):
        self._alarm.set_target(<int64_t> value)

    @property
    def enable(self):
        return bool(self._alarm.enable())

    @enable.setter
    def enable(self, value):
        self._alarm.set_enable(bool(value))


cdef class _TimerView:
    """A channel's `Timer32` as the reference shows it."""

    cdef RPPWM _owner
    cdef Timer32* _timer

    @property
    def counter(self):
        return self._timer.counter()

    @property
    def raw_counter(self):
        return self._timer.raw_counter()

    @property
    def top(self):
        return self._timer.top()

    @top.setter
    def top(self, value):
        self._timer.set_top(<int64_t> value)

    @property
    def frequency(self):
        return self._timer.frequency()

    @frequency.setter
    def frequency(self, value):
        self._timer.set_frequency(<double> value)

    @property
    def prescaler(self):
        return self._timer.prescaler()

    @prescaler.setter
    def prescaler(self, value):
        self._timer.set_prescaler(<double> value)

    @property
    def enable(self):
        return bool(self._timer.enable())

    @enable.setter
    def enable(self, value):
        self._timer.set_enable(bool(value))

    @property
    def mode(self):
        return TimerMode(self._timer.mode_raw())

    @mode.setter
    def mode(self, value):
        self._timer.set_mode_raw(<uint32_t> int(value))

    def set(self, value, zig_zag_down=False):
        self._timer.set(<int64_t> value, bool(zig_zag_down))

    def advance(self, delta):
        self._timer.advance(<int64_t> delta)

    def reset(self):
        self._timer.reset()

    def to_nanos(self, cycles):
        return self._timer.to_nanos(<int64_t> cycles)


cdef class _Channel:
    """One of the eight channels as the reference's `PWMChannel` shows it."""

    cdef RPPWM _owner
    cdef PwmChannel* _channel
    cdef public object timer
    cdef public object alarm_a
    cdef public object alarm_b
    cdef public object alarm_bottom

    @property
    def pwm(self):
        return self._owner

    @property
    def clock(self):
        return self._owner.clock

    @property
    def index(self):
        return self._channel.index

    @property
    def pin_a1(self):
        return self._channel.pin_a1

    @property
    def pin_b1(self):
        return self._channel.pin_b1

    @property
    def pin_a2(self):
        return self._channel.pin_a2

    @property
    def pin_b2(self):
        return self._channel.pin_b2

    @property
    def csr(self):
        return self._channel.csr

    @csr.setter
    def csr(self, value):
        self._channel.csr = <uint32_t> (<int64_t> value)

    @property
    def div(self):
        return self._channel.div

    @div.setter
    def div(self, value):
        self._channel.div = <uint32_t> (<int64_t> value)

    @property
    def cc(self):
        return self._channel.cc

    @cc.setter
    def cc(self, value):
        self._channel.cc = <uint32_t> (<int64_t> value)

    @property
    def top(self):
        return self._channel.top

    @top.setter
    def top(self, value):
        self._channel.top = <uint32_t> (<int64_t> value)

    @property
    def last_b_value(self):
        return bool(self._channel.last_b_value)

    @last_b_value.setter
    def last_b_value(self, value):
        self._channel.last_b_value = bool(value)

    @property
    def counting_up(self):
        return bool(self._channel.counting_up)

    @counting_up.setter
    def counting_up(self, value):
        self._channel.counting_up = bool(value)

    @property
    def cc_updated(self):
        return bool(self._channel.cc_updated)

    @cc_updated.setter
    def cc_updated(self, value):
        self._channel.cc_updated = bool(value)

    @property
    def top_updated(self):
        return bool(self._channel.top_updated)

    @top_updated.setter
    def top_updated(self, value):
        self._channel.top_updated = bool(value)

    @property
    def tick_counter(self):
        return self._channel.tick_counter

    @tick_counter.setter
    def tick_counter(self, value):
        self._channel.tick_counter = <double> value

    @property
    def div_mode(self):
        return PWMDivMode(self._channel.div_mode_raw())

    @div_mode.setter
    def div_mode(self, value):
        self._channel.set_div_mode_raw(<uint32_t> int(value))

    @property
    def gpio_b_value(self):
        cdef cppbool level = False
        if not self._channel.gpio_b_value(&level):
            raise_if_pending()
        return bool(level)

    @property
    def en(self):
        return self._channel.en()

    @en.setter
    def en(self, value):
        if not self._channel.set_en(bool(value)):
            raise_if_pending()

    def read_register(self, offset):
        return self._channel.read_register(<uint32_t> offset)

    def write_register(self, offset, value):
        if not self._channel.write_register(<uint32_t> offset, <uint32_t> (<int64_t> value)):
            raise_if_pending()

    def reset(self):
        if not self._channel.reset():
            raise_if_pending()

    def set_a(self, value):
        if not self._channel.set_a(bool(value)):
            raise_if_pending()

    def set_b(self, value):
        if not self._channel.set_b(bool(value)):
            raise_if_pending()

    def gpio_b_changed(self):
        if not self._channel.gpio_b_changed():
            raise_if_pending()

    def update_enable(self):
        if not self._channel.update_enable():
            raise_if_pending()

    def _update_double_buffered(self):
        self._channel.update_double_buffered()


cdef class RPPWM:
    def __init__(self, rp2040, name):
        cdef PwmHost host
        cdef _Channel channel
        cdef _TimerView timer
        cdef _AlarmView alarm
        cdef uint32_t i
        clock = rp2040.clock
        if not isinstance(clock, SimulationClock):
            raise TypeError(
                "the native PWM schedules its alarms on the native SimulationClock; "
                f"{type(rp2040).__name__} with {type(clock).__name__} is not that "
                "(use the pure-Python peripherals/_pwm.RPPWM with it)"
            )
        self.rp2040 = rp2040
        self.name = name
        self.clock = clock
        host.irq = _irq_trampoline
        host.dreq = _dreq_trampoline
        host.pin_changed = _pin_changed_trampoline
        host.pin_read = _pin_read_trampoline
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        host.failed = pending_flag()
        self._block.init(&(<SimulationClock> clock)._clock, host, <double> rp2040.clk_sys)
        views = []
        for i in range(8):
            channel = _Channel.__new__(_Channel)
            channel._owner = self
            channel._channel = &self._block.channels[i]
            timer = _TimerView.__new__(_TimerView)
            timer._owner = self
            timer._timer = &self._block.channels[i].timer
            channel.timer = timer
            alarm = _AlarmView.__new__(_AlarmView)
            alarm._owner = self
            alarm._alarm = &self._block.channels[i].alarm_a
            channel.alarm_a = alarm
            alarm = _AlarmView.__new__(_AlarmView)
            alarm._owner = self
            alarm._alarm = &self._block.channels[i].alarm_b
            channel.alarm_b = alarm
            alarm = _AlarmView.__new__(_AlarmView)
            alarm._owner = self
            alarm._alarm = &self._block.channels[i].alarm_bottom
            channel.alarm_bottom = alarm
            views.append(channel)
        self.channels = views

    def __dealloc__(self):
        # Safe whichever of this block and its clock the collector frees first: the C++ clock unlinks its alarms when it dies.
        self._block.detach()

    # --- the reference's attributes --------------------------------------------------------------

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @property
    def gpio_value(self):
        return self._block.gpio_value

    @gpio_value.setter
    def gpio_value(self, value):
        self._block.gpio_value = <uint32_t> (<int64_t> value)

    @property
    def gpio_direction(self):
        return self._block.gpio_direction

    @gpio_direction.setter
    def gpio_direction(self, value):
        self._block.gpio_direction = <uint32_t> (<int64_t> value)

    @property
    def _int_raw(self):
        return self._block.int_raw

    @_int_raw.setter
    def _int_raw(self, value):
        self._block.int_raw = <uint32_t> (<int64_t> value)

    @property
    def _int_enable(self):
        return self._block.int_enable

    @_int_enable.setter
    def _int_enable(self, value):
        self._block.int_enable = <uint32_t> (<int64_t> value)

    @property
    def _int_force(self):
        return self._block.int_force

    @_int_force.setter
    def _int_force(self, value):
        self._block.int_force = <uint32_t> (<int64_t> value)

    @property
    def int_status(self):
        return self._block.int_status()

    @property
    def clock_freq(self):
        # The reference reads the live system clock; the counters were built on the value at construction.
        return self.rp2040.clk_sys

    def set_clock_freq(self, freq):
        """A new system clock: every counter keeps its value and continues at the new rate."""
        self._block.set_clock_freq(<double> freq)

    def channel_interrupt(self, index):
        if not self._block.channel_interrupt(<uint32_t> index):
            raise_if_pending()

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

    def gpio_set(self, index, value):
        if not self._block.gpio_set(<int64_t> index, bool(value)):
            raise_if_pending()

    def gpio_set_dir(self, index, output):
        if not self._block.gpio_set_dir(<int64_t> index, bool(output)):
            raise_if_pending()

    def gpio_read(self, index):
        cdef cppbool level = False
        if not self._block.gpio_read(<int64_t> index, &level):
            raise_if_pending()
        return bool(level)

    def gpio_on_input(self, index):
        if not self._block.gpio_on_input(<uint32_t> index):
            raise_if_pending()

    # --- BasePeripheral's surface ----------------------------------------------------------------

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
        """The channels and the direction word back to power-on (0089 Phase 5); the interrupt registers and `gpio_value` are left, as in the reference."""
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

    # --- the native bus protocol -----------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers in its C++ window table when it adopts this block, instead of a Python trampoline: the addresses of the block's own
        read/write functions and its context. Looked up on the *type* by the bus, so a wrapper that merely forwards attributes (a recorder, a profiler)
        cannot lend its target's fast path and be bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
