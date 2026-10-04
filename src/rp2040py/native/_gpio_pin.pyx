# cython: language_level=3
"""Native `GPIOPin`: a Python-facing shell over the C++ pin of `core/pin.hpp`
(docs/records/0096-cpp-mcu-core.md, Phase 3). `_gpio_pin.py` is the pure-Python reference and the oracle; `gpio_pin.py` is the
facade that picks between them.

History: this module started as the Cython port of `_gpio_pin.py` that collapsed the ~9-call property cascade a PIO clock edge
used to re-evaluate (the single biggest pure-Python cost of a CYW43 boot at the time) into one inlined C path. Phase 3 moves that
path, with the pin's state, into C++: the ten words of state and the level/STATUS/input/IRQ logic live in a `PinBank` of one pin
that this object owns, and what is left here is the edge of the pin - the things only Python can answer:

* the *sources* of the level (`rp2040.sio`, `rp2040.pio[i]`, `rp2040.pwm` registers), read through one trampoline exactly as the
  Cython did, object attribute by object attribute;
* the *listeners* (a Python `set`, iterated in set order, so their order is unchanged by construction);
* `rp2040.update_io_interrupt()` and the PWM/PIO reactions to an input change.

Every failure of those callbacks (an exception from a listener, from a source attribute) is parked in the shared slot of
`_pending.pyx` and re-raised here, right after the C++ call returns - the same exception, at the same call, as before.

The full `@property` surface of `_gpio_pin.py` is preserved: `ctrl`, `pad_value`, the three IRQ words, `index`, `_raw_input_value`,
`_driven` and `_always_output_enabled` are properties over the C++ pin's fields, so external callers (`peripherals/ssi.py`,
`external/cyw43`, `tests/utils/pin_trace.py`, tests) see no difference. `rp2040` stays `object` (untyped), same reasoning as
`native/_state_machine.pyx`: its `pwm`/`sio`/`pio` aren't in a typed `.pxd` surface.
"""
import logging

from libc.stdint cimport uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._pin cimport (
    PinHost,
    apply_override,
    kSrcPio0Oe,
    kSrcPio0Value,
    kSrcPio1Oe,
    kSrcPio1Value,
    kSrcPwmDirection,
    kSrcPwmValue,
    kSrcSioOe,
    kSrcSioValue,
)

from rp2040py._gpio_pin import GPIOPinState

# WaitType from pio_registers (its real home), NOT the peripherals.pio facade: the facade pulls in
# native._pio, and native._pio cimports GPIOPin from this module - importing WaitType via the facade
# would make _gpio_pin depend on _pio and turn that one-way cimport into a fragile circular runtime
# import. Sourcing WaitType directly keeps _gpio_pin free of any _pio dependency.
from rp2040py.peripherals.pio_registers import WaitType

_logger = logging.getLogger(__name__)

# Indexed by GPIOPinState integer value (LOW=0, HIGH=1, INPUT=2, PULL_UP=3, PULL_DOWN=4,
# BUS_KEEPER=5) - turns the C++ state code back into the enum member for the listeners.
cdef tuple _STATES = (
    GPIOPinState.LOW,
    GPIOPinState.HIGH,
    GPIOPinState.INPUT,
    GPIOPinState.INPUT_PULL_UP,
    GPIOPinState.INPUT_PULL_DOWN,
    GPIOPinState.INPUT_BUS_KEEPER,
)


# --- the C++ pin's host: what only Python can answer ----------------------------------------------------------------------

cdef cppbool _source_trampoline(void* ctx, uint32_t source, uint32_t* out) noexcept:
    cdef GPIOPin pin = <GPIOPin> ctx
    cdef object rp = pin.rp2040
    try:
        if source == kSrcSioOe:
            out[0] = <unsigned int> rp.sio.gpio_output_enable
        elif source == kSrcSioValue:
            out[0] = <unsigned int> rp.sio.gpio_value
        elif source == kSrcPio0Oe:
            out[0] = <unsigned int> rp.pio[0].pin_directions
        elif source == kSrcPio0Value:
            out[0] = <unsigned int> rp.pio[0].pin_values
        elif source == kSrcPio1Oe:
            out[0] = <unsigned int> rp.pio[1].pin_directions
        elif source == kSrcPio1Value:
            out[0] = <unsigned int> rp.pio[1].pin_values
        elif source == kSrcPwmDirection:
            out[0] = <unsigned int> rp.pwm.gpio_direction
        else:  # kSrcPwmValue
            out[0] = <unsigned int> rp.pwm.gpio_value
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _change_trampoline(void* ctx, uint32_t pin_index, int new_state, int old_state) noexcept:
    cdef GPIOPin pin = <GPIOPin> ctx
    try:
        value = _STATES[new_state]
        last_value = _STATES[old_state]
        for listener in pin._listeners:
            listener(value, last_value)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _io_interrupt_trampoline(void* ctx) noexcept:
    cdef GPIOPin pin = <GPIOPin> ctx
    try:
        pin.rp2040.update_io_interrupt()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _input_trampoline(void* ctx, uint32_t pin_index, cppbool function_is_pwm) noexcept:
    cdef GPIOPin pin = <GPIOPin> ctx
    cdef object rp = pin.rp2040
    try:
        if function_is_pwm:
            rp.pwm.gpio_on_input(pin_index)
        for pio in rp.pio:
            for machine in pio.machines:
                if (
                    machine.enabled
                    and machine.waiting
                    and machine.wait_type == WaitType.PIN
                    and machine.wait_index == pin_index
                ):
                    machine.check_wait()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class GPIOPin:
    # Fields are declared in _gpio_pin.pxd (so native/_pio.pyx can cimport this class).

    def __cinit__(self, *args, **kwargs):
        self._pin = self._bank.pin_ptr(0)

    def __init__(self, rp2040, unsigned int index, name=None, bint always_output_enabled=False):
        cdef PinHost host
        cdef cppbool always = always_output_enabled
        self.rp2040 = rp2040
        self.name = name if name is not None else str(index)
        self._listeners = set()

        host.source = _source_trampoline
        host.on_change = _change_trampoline
        host.io_interrupt = _io_interrupt_trampoline
        host.input_changed = _input_trampoline
        host.ctx = <void*> self
        # Mirrors the Python constructor's field-initializer ordering: the pin's `_last_state` is captured with ctrl = 0 and
        # pad_value = 0 *before* the real defaults are written (core/pin.hpp's init does exactly that).
        if not self._bank.init(1, host, &always, index):
            raise_if_pending()

    def reset(self, io=True, pads=True):
        """Return this pin's *registers* to their power-on values - `IO_BANK0.GPIOn_CTRL`,
        `PADS_BANK0.GPIOn` and the three IRQ masks - leaving everything about what is wired to the
        pin alone (docs/records/0089-one-reset-for-every-trigger.md Phase 5).

        That split is the whole contract, and it is not an approximation: on silicon a chip reset
        releases the pad, but it does not unsolder the LED, and a button still held down is still
        held. So `_listeners` (every attached `ExternalDevice`), `_raw_input_value` and `_driven`
        (whatever is externally driving the pin) survive; only the chip's own side is restored.

        `_last_state` is recomputed *after* the registers are back, so the next
        `check_for_updates()` compares against the post-reset level rather than firing a spurious
        edge for a transition the pin never made.

        `io`/`pads` split the two halves because `RESETS` does: `IO_BANK0` and `PADS_BANK0` are
        separate bits a guest can select independently via `RESETS.WDSEL` (0089's D5). A `GPIOPin`
        merges both - `ctrl` is the IO half, `pad_value` the PADS half - so the flags are what keeps
        that merge from over-resetting.

        QSPI pads are the one exception this method cannot handle alone: `PADS_QSPI` has different
        per-pad reset values (record 0050), applied by `RP2040.__init__`/`RP2040.reset()` right
        after this call, the same way construction does it."""
        if not self._bank.reset(0, bool(io), bool(pads)):
            raise_if_pending()

    cpdef check_for_updates(self):
        # Announces a change of the pin's state to the listeners - once, recording it first (core/pin.hpp).
        if not self._bank.check_for_updates(0):
            raise_if_pending()

    # --- the pin's own fields, as properties over the C++ pin ----------------------------------------------------------------

    @property
    def index(self):
        return self._pin.index

    @index.setter
    def index(self, unsigned int value):
        self._pin.index = value

    @property
    def ctrl(self):
        return self._pin.ctrl

    @ctrl.setter
    def ctrl(self, unsigned int value):
        self._pin.ctrl = value

    @property
    def pad_value(self):
        return self._pin.pad_value

    @pad_value.setter
    def pad_value(self, unsigned int value):
        self._pin.pad_value = value

    @property
    def irq_enable_mask(self):
        return self._pin.irq_enable_mask

    @irq_enable_mask.setter
    def irq_enable_mask(self, unsigned int value):
        self._pin.irq_enable_mask = value

    @property
    def irq_force_mask(self):
        return self._pin.irq_force_mask

    @irq_force_mask.setter
    def irq_force_mask(self, unsigned int value):
        self._pin.irq_force_mask = value

    @property
    def irq_status(self):
        return self._pin.irq_status

    @irq_status.setter
    def irq_status(self, unsigned int value):
        self._pin.irq_status = value

    @property
    def _raw_input_value(self):
        return self._pin.raw_input_value

    @_raw_input_value.setter
    def _raw_input_value(self, bint value):
        self._pin.raw_input_value = value

    @property
    def _driven(self):
        return self._pin.driven

    @_driven.setter
    def _driven(self, bint value):
        self._pin.driven = value

    @property
    def _always_output_enabled(self):
        return self._pin.always_output_enabled

    @_always_output_enabled.setter
    def _always_output_enabled(self, bint value):
        self._pin.always_output_enabled = value

    # --- @property surface (behavioural parity with _gpio_pin.py) ---------------------------------------------------------

    @property
    def raw_interrupt(self):
        return bool(self._bank.raw_interrupt(0))

    @property
    def is_slew_fast(self):
        return bool(self._pin.pad_value & 1)

    @property
    def schmitt_enabled(self):
        return bool(self._pin.pad_value & 2)

    @property
    def pulldown_enabled(self):
        return bool(self._pin.pad_value & 4)

    @property
    def pullup_enabled(self):
        return bool(self._pin.pad_value & 8)

    @property
    def drive_strength(self):
        return (self._pin.pad_value >> 4) & 0x3

    @property
    def input_enable(self):
        return bool(self._pin.pad_value & 0x40)

    @property
    def output_disable(self):
        return bool(self._pin.pad_value & 0x80)

    @property
    def function_select(self):
        return self._pin.ctrl & 0x1F

    @property
    def output_override(self):
        return (self._pin.ctrl >> 8) & 0x3

    @property
    def output_enable_override(self):
        return (self._pin.ctrl >> 12) & 0x3

    @property
    def input_override(self):
        return (self._pin.ctrl >> 16) & 0x3

    @property
    def irq_override(self):
        return (self._pin.ctrl >> 28) & 0x3

    @property
    def raw_output_enable(self):
        cdef cppbool out = False
        if not self._bank.raw_output_enable(0, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(out)

    @property
    def raw_output_value(self):
        cdef cppbool out = False
        if not self._bank.raw_output_value(0, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(out)

    @property
    def _effective_raw_input_value(self):
        return bool(self._bank.eff_raw_input(0))

    @property
    def input_value(self):
        return bool(self._bank.input_value(0))

    @property
    def irq_value(self):
        return bool(self._bank.irq_value(0))

    @property
    def output_enable(self):
        cdef cppbool out = False
        if not self._bank.raw_output_enable(0, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(apply_override(out, (self._pin.ctrl >> 12) & 0x3))

    @property
    def output_value(self):
        cdef cppbool out = False
        if not self._bank.raw_output_value(0, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(apply_override(out, (self._pin.ctrl >> 8) & 0x3))

    @property
    def status(self):
        cdef uint32_t out = 0
        if not self._bank.status(0, &out):
            raise_if_pending()
        return out

    @property
    def value(self):
        cdef int code = 0
        if not self._bank.state_code(0, &code):
            raise_if_pending()
        return _STATES[code]

    # --- methods ----------------------------------------------------------------------------------------------------------

    def set_input_value(self, value):
        if not self._bank.set_input_value(0, bool(value)):
            raise_if_pending()

    def release_input(self):
        """Mirrors _gpio_pin.py's own release_input() - hand the pad back to its pull resistor."""
        if not self._bank.release_input(0):
            raise_if_pending()

    def _apply_input_value(self, value):
        if not self._bank.apply_input_value(0, bool(value)):
            raise_if_pending()

    def refresh_input(self):
        if not self._bank.refresh_input(0):
            raise_if_pending()

    def update_irq_value(self, value):
        if not self._bank.update_irq_value(0, <unsigned int> value):
            raise_if_pending()

    def add_listener(self, callback):
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)
