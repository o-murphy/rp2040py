# cython: language_level=3
"""Native Cython port of `_gpio_pin.py`'s `GPIOPin` - the pin-state hot path, measured as the
single biggest pure-Python cost during a CYW43 (PIO-driven SPI) boot (~46% of profiled self-time
after the `check_changed_pins` fix). Every PIO clock edge re-evaluates a pin's `value` through a
cascade of ~9 Python `@property`/helper calls (`function_select`, `raw_output_*`, the override
getters, `_apply_override`); this collapses that cascade into inline C in a single `cdef _value()`
path, keeping only the handful of genuinely object-typed reads (`rp2040.pio[i].pin_values`/
`.pin_directions`, `rp2040.pwm`/`.sio`) that touch still-pure peripherals.

Behaviourally identical to `_gpio_pin.py` (the pure reference the facade falls back to) - the full
`@property` surface is preserved for external callers (`peripherals/ssi.py`, `external/cyw43`,
tests); only the internals are inlined. `rp2040` stays `object` (untyped), same reasoning as
`native/_state_machine.pyx`: `RP2040.pwm`/`.sio`/`.pio` aren't in a typed `.pxd` surface and RPPIO
isn't natively typed, so cimporting buys nothing here.
"""
import logging

from rp2040py._gpio_pin import (
    FUNCTION_PIO0,
    FUNCTION_PIO1,
    FUNCTION_PWM,
    FUNCTION_SIO,
    GPIOPinState,
)

# WaitType from pio_registers (its real home), NOT the peripherals.pio facade: the facade pulls in
# native._pio, and native._pio cimports GPIOPin from this module - importing WaitType via the facade
# would make _gpio_pin depend on _pio and turn that one-way cimport into a fragile circular runtime
# import. Sourcing WaitType directly keeps _gpio_pin free of any _pio dependency.
from rp2040py.peripherals.pio_registers import WaitType

_logger = logging.getLogger(__name__)

# Cache the enum members as module-level Python objects so `_value()` returns them without a dict
# lookup per call.
cdef object _S_LOW = GPIOPinState.LOW
cdef object _S_HIGH = GPIOPinState.HIGH
cdef object _S_INPUT = GPIOPinState.INPUT
cdef object _S_PULLUP = GPIOPinState.INPUT_PULL_UP
cdef object _S_PULLDOWN = GPIOPinState.INPUT_PULL_DOWN
cdef object _S_BUSKEEPER = GPIOPinState.INPUT_BUS_KEEPER

# Indexed by GPIOPinState integer value (LOW=0, HIGH=1, INPUT=2, PULL_UP=3, PULL_DOWN=4,
# BUS_KEEPER=5) - lets _value()/check_for_updates() turn a C state code back into the enum member.
cdef tuple _STATES = (_S_LOW, _S_HIGH, _S_INPUT, _S_PULLUP, _S_PULLDOWN, _S_BUSKEEPER)

cdef unsigned int _FUNCTION_PWM = FUNCTION_PWM
cdef unsigned int _FUNCTION_SIO = FUNCTION_SIO
cdef unsigned int _FUNCTION_PIO0 = FUNCTION_PIO0
cdef unsigned int _FUNCTION_PIO1 = FUNCTION_PIO1

cdef unsigned int IRQ_EDGE_HIGH = 1 << 3
cdef unsigned int IRQ_EDGE_LOW = 1 << 2
cdef unsigned int IRQ_LEVEL_HIGH = 1 << 1
cdef unsigned int IRQ_LEVEL_LOW = 1 << 0


cdef inline bint _apply_override_c(bint value, unsigned int override_type):
    if override_type == 0:
        return value
    if override_type == 1:
        return not value
    if override_type == 2:
        return False
    if override_type == 3:
        return True
    _logger.error("applyOverride received invalid override type %s", override_type)
    return value


cdef class GPIOPin:
    # Fields are declared in _gpio_pin.pxd (so native/_pio.pyx can cimport this class).

    def __init__(self, rp2040, unsigned int index, name=None, bint always_output_enabled=False):
        self.rp2040 = rp2040
        self.index = index
        self.name = name if name is not None else str(index)
        self._always_output_enabled = always_output_enabled

        self._raw_input_value = False
        self._driven = False

        # Matches upstream field-initializer ordering: `_last_value` reads ctrl=0/pad_value=0 here,
        # deterministically yielding GPIOPinState.INPUT, before the real defaults below.
        self.ctrl = 0
        self.pad_value = 0
        self._last_state = self._state_code()

        self.ctrl = 0x1F
        self.pad_value = 0b0110110
        self.irq_enable_mask = 0
        self.irq_force_mask = 0
        self.irq_status = 0

        self._listeners = set()

    # --- inlined hot path -------------------------------------------------------------------

    cdef bint _raw_output_enable(self, unsigned int fsel):
        if self._always_output_enabled:
            return True
        cdef unsigned int bitmask = (<unsigned int>1) << self.index
        cdef object rp = self.rp2040
        if fsel == _FUNCTION_PWM:
            return (<unsigned int>rp.pwm.gpio_direction) & bitmask
        if fsel == _FUNCTION_SIO:
            return (<unsigned int>rp.sio.gpio_output_enable) & bitmask
        if fsel == _FUNCTION_PIO0:
            return (<unsigned int>rp.pio[0].pin_directions) & bitmask
        if fsel == _FUNCTION_PIO1:
            return (<unsigned int>rp.pio[1].pin_directions) & bitmask
        return False

    cdef bint _raw_output_value(self, unsigned int fsel):
        cdef unsigned int bitmask = (<unsigned int>1) << self.index
        cdef object rp = self.rp2040
        if fsel == _FUNCTION_PWM:
            return (<unsigned int>rp.pwm.gpio_value) & bitmask
        if fsel == _FUNCTION_SIO:
            return (<unsigned int>rp.sio.gpio_value) & bitmask
        if fsel == _FUNCTION_PIO0:
            return (<unsigned int>rp.pio[0].pin_values) & bitmask
        if fsel == _FUNCTION_PIO1:
            return (<unsigned int>rp.pio[1].pin_values) & bitmask
        return False

    cdef bint _eff_raw_input(self):
        cdef unsigned int pad = self.pad_value
        if self._driven:
            return self._raw_input_value
        if (pad & 8) and not (pad & 4):
            return True
        if (pad & 4) and not (pad & 8):
            return False
        return self._raw_input_value

    cdef int _state_code(self):
        cdef unsigned int ctrl = self.ctrl
        cdef unsigned int fsel = ctrl & 0x1F
        cdef unsigned int pad
        cdef bint oe = _apply_override_c(self._raw_output_enable(fsel), (ctrl >> 12) & 0x3)
        cdef bint ov
        cdef bint pd
        cdef bint pu
        if oe:
            ov = _apply_override_c(self._raw_output_value(fsel), (ctrl >> 8) & 0x3)
            return 1 if ov else 0  # HIGH / LOW
        pad = self.pad_value
        pd = (pad & 4) != 0
        pu = (pad & 8) != 0
        if pd and pu:
            return 5  # BUS_KEEPER
        if pd:
            return 4  # PULL_DOWN
        if pu:
            return 3  # PULL_UP
        return 2  # INPUT

    cdef object _value(self):
        return _STATES[self._state_code()]

    cpdef check_for_updates(self):
        # Compare the state as a plain C int; only materialise the GPIOPinState objects (for the
        # listeners) on an actual change, avoiding a per-call enum box + object richcompare.
        cdef int s = self._state_code()
        cdef int last = self._last_state
        if s != last:
            self._last_state = s
            value = _STATES[s]
            last_value = _STATES[last]
            for listener in self._listeners:
                listener(value, last_value)

    # --- @property surface (behavioural parity with _gpio_pin.py) ---------------------------

    @property
    def raw_interrupt(self):
        return bool((self.irq_status & self.irq_enable_mask) | self.irq_force_mask)

    @property
    def is_slew_fast(self):
        return bool(self.pad_value & 1)

    @property
    def schmitt_enabled(self):
        return bool(self.pad_value & 2)

    @property
    def pulldown_enabled(self):
        return bool(self.pad_value & 4)

    @property
    def pullup_enabled(self):
        return bool(self.pad_value & 8)

    @property
    def drive_strength(self):
        return (self.pad_value >> 4) & 0x3

    @property
    def input_enable(self):
        return bool(self.pad_value & 0x40)

    @property
    def output_disable(self):
        return bool(self.pad_value & 0x80)

    @property
    def function_select(self):
        return self.ctrl & 0x1F

    @property
    def output_override(self):
        return (self.ctrl >> 8) & 0x3

    @property
    def output_enable_override(self):
        return (self.ctrl >> 12) & 0x3

    @property
    def input_override(self):
        return (self.ctrl >> 16) & 0x3

    @property
    def irq_override(self):
        return (self.ctrl >> 28) & 0x3

    @property
    def raw_output_enable(self):
        return bool(self._raw_output_enable(self.ctrl & 0x1F))

    @property
    def raw_output_value(self):
        return bool(self._raw_output_value(self.ctrl & 0x1F))

    @property
    def _effective_raw_input_value(self):
        return bool(self._eff_raw_input())

    @property
    def input_value(self):
        return _apply_override_c(
            self._eff_raw_input() and bool(self.pad_value & 0x40), (self.ctrl >> 16) & 0x3
        )

    @property
    def irq_value(self):
        return _apply_override_c(
            bool((self.irq_status & self.irq_enable_mask) | self.irq_force_mask), (self.ctrl >> 28) & 0x3
        )

    @property
    def output_enable(self):
        return _apply_override_c(self._raw_output_enable(self.ctrl & 0x1F), (self.ctrl >> 12) & 0x3)

    @property
    def output_value(self):
        return _apply_override_c(self._raw_output_value(self.ctrl & 0x1F), (self.ctrl >> 8) & 0x3)

    @property
    def status(self):
        cdef unsigned int fsel = self.ctrl & 0x1F
        cdef bint raw_int = bool((self.irq_status & self.irq_enable_mask) | self.irq_force_mask)
        cdef bint irq_v = _apply_override_c(raw_int, (self.ctrl >> 28) & 0x3)
        cdef bint eff_in = self._eff_raw_input()
        cdef bint in_v = _apply_override_c(eff_in and bool(self.pad_value & 0x40), (self.ctrl >> 16) & 0x3)
        cdef bint roe = self._raw_output_enable(fsel)
        cdef bint oe = _apply_override_c(roe, (self.ctrl >> 12) & 0x3)
        cdef bint rov = self._raw_output_value(fsel)
        cdef bint ov = _apply_override_c(rov, (self.ctrl >> 8) & 0x3)
        return (
            (1 << 26 if irq_v else 0)
            | (1 << 24 if raw_int else 0)
            | (1 << 19 if in_v else 0)
            | (1 << 17 if eff_in else 0)
            | (1 << 13 if oe else 0)
            | (1 << 12 if roe else 0)
            | (1 << 9 if ov else 0)
            | (1 << 8 if rov else 0)
        )

    @property
    def value(self):
        return self._value()

    # --- methods ----------------------------------------------------------------------------

    def set_input_value(self, value):
        self._driven = True
        self._apply_input_value(value)

    def _apply_input_value(self, value):
        self._raw_input_value = bool(value)
        prev_irq_value = self.irq_value
        if value and (self.pad_value & 0x40):
            self.irq_status |= IRQ_EDGE_HIGH | IRQ_LEVEL_HIGH
            self.irq_status &= ~IRQ_LEVEL_LOW
        else:
            self.irq_status |= IRQ_EDGE_LOW | IRQ_LEVEL_LOW
            self.irq_status &= ~IRQ_LEVEL_HIGH
        if self.irq_value != prev_irq_value:
            self.rp2040.update_io_interrupt()
        if (self.ctrl & 0x1F) == _FUNCTION_PWM:
            self.rp2040.pwm.gpio_on_input(self.index)
        for pio in self.rp2040.pio:
            for machine in pio.machines:
                if (
                    machine.enabled
                    and machine.waiting
                    and machine.wait_type == WaitType.PIN
                    and machine.wait_index == self.index
                ):
                    machine.check_wait()

    def refresh_input(self):
        self._apply_input_value(self._raw_input_value)

    def update_irq_value(self, value):
        if value & IRQ_EDGE_LOW and self.irq_status & IRQ_EDGE_LOW:
            self.irq_status &= ~IRQ_EDGE_LOW
            self.rp2040.update_io_interrupt()
        if value & IRQ_EDGE_HIGH and self.irq_status & IRQ_EDGE_HIGH:
            self.irq_status &= ~IRQ_EDGE_HIGH
            self.rp2040.update_io_interrupt()

    def add_listener(self, callback):
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)
