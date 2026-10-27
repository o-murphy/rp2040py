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

from rp2040py.native._mcu_irq cimport Mcu
from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._simulation_clock cimport SimulationClock
from rp2040py.native._views cimport Timer32View
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPPWM",)


cdef class _Channel:
    """One of the eight channels as far as the rest of the chip looks at it: its `timer` (the clock tree retunes it) and its place. The channel's state is in `RPPWM.snapshot()`."""

    cdef RPPWM _owner
    cdef PwmChannel* _channel
    cdef public object timer

    @property
    def pwm(self):
        return self._owner

    @property
    def clock(self):
        return self._owner.clock

    @property
    def index(self):
        return self._channel.index


cdef class RPPWM(NativeBlock):
    def __init__(self, rp2040, name, mcu_index=-1):
        cdef _Channel channel
        cdef Timer32View timer
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
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` holds this block (its line, request and pins are wired inside): this object is the Python view of it
            self._block = <PwmBlock*> <size_t> rp2040._native_pwm_address()
            self._ops = register_ops(self._block)
        else:
            raise TypeError(
                "the native block is a view of the chip's C++ `Mcu` block: RPPWM is built by a native chip (the pure-Python chip has the pure-Python peripherals)"
            )
        views = []
        for i in range(8):
            channel = _Channel.__new__(_Channel)
            channel._owner = self
            channel._channel = &self._block.channels[i]
            timer = Timer32View.__new__(Timer32View)
            timer._owner = self
            timer._timer = &self._block.channels[i].timer
            channel.timer = timer
            views.append(channel)
        self.channels = views

    # --- the reference's attributes --------------------------------------------------------------

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

    def snapshot(self):
        """The block's state in one call: every channel's registers, flags, counter bookkeeping, timer and alarms, and the block's pin words, interrupt words and clock."""
        cdef PwmChannel* c
        cdef uint32_t i
        channels = []
        for i in range(8):
            c = &self._block.channels[i]
            channels.append({
                "csr": c.csr,
                "div": c.div,
                "cc": c.cc,
                "top": c.top,
                "last_b_value": bool(c.last_b_value),
                "counting_up": bool(c.counting_up),
                "cc_updated": bool(c.cc_updated),
                "top_updated": bool(c.top_updated),
                "tick_counter": c.tick_counter,
                "div_mode": c.div_mode_raw(),
                "timer_enable": bool(c.timer.enable()),
                "timer_mode": c.timer.mode_raw(),
                "timer_prescaler": c.timer.prescaler(),
                "timer_top": c.timer.top(),
                "timer_raw_counter": c.timer.raw_counter(),
                "timer_counter": c.timer.counter(),
                "alarm_a_target": c.alarm_a.target(),
                "alarm_b_target": c.alarm_b.target(),
                "alarm_a_rise_target": c.alarm_a_rise.target(),
                "alarm_b_rise_target": c.alarm_b_rise.target(),
                "alarm_a_enable": bool(c.alarm_a.enable()),
                "alarm_b_enable": bool(c.alarm_b.enable()),
                "alarm_a_rise_enable": bool(c.alarm_a_rise.enable()),
                "alarm_b_rise_enable": bool(c.alarm_b_rise.enable()),
                "alarm_bottom_enable": bool(c.alarm_bottom.enable()),
                "latched_top": c.latched_top,
                "latched_cc_a": c.latched_cc_a,
                "latched_cc_b": c.latched_cc_b,
                "counter": c.counter(),
            })
        return {
            "channels": tuple(channels),
            "gpio_value": self._block.gpio_value,
            "gpio_direction": self._block.gpio_direction,
            "int_raw": self._block.int_raw,
            "int_enable": self._block.int_enable,
            "int_force": self._block.int_force,
            "int_status": self._block.int_status(),
            "clock_freq": self.rp2040.clk_sys,
        }

    def gpio_on_input(self, index):
        if not self._block.gpio_on_input(<uint32_t> index):
            raise_if_pending()

    # --- BasePeripheral's surface ----------------------------------------------------------------

    def reset(self):
        """The channels and the direction word back to power-on (0089 Phase 5); the interrupt registers and `gpio_value` are left, as in the reference."""
        if not self._block.reset():
            raise_if_pending()

    # --- the native bus protocol -----------------------------------------------------------------
