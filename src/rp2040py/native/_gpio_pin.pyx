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

The full `@property` surface of `_gpio_pin.py` is preserved: `ctrl`, `pad_value`, the three IRQ words, `index`, the state a block
keeps on the outside of the pin is `snapshot()`; `driven` is the one device-facing flag, so external callers (`peripherals/ssi.py`,
`external/cyw43`, `tests/utils/pin_trace.py`, tests) see no difference. `rp2040` stays `object` (untyped), same reasoning as
`native/_pio.pyx`: its `pwm`/`sio`/`pio` aren't in a typed `.pxd` surface.
"""
import logging

from libc.stdint cimport uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._mcu_irq cimport mcu_io_interrupt, native_mcu
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._pin cimport (
    PinBank,
    PinChangeFn,
    PinHost,
    apply_override,
    kSrcPio0Oe,
    kSrcPio0Value,
    kSrcPio1Oe,
    kSrcPio1Value,
    kSrcPwmDirection,
    kSrcPwmValue,
    kSrcSioOe,
    kSrcSioQspiOe,
    kSrcSioQspiValue,
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
    cdef GPIOPinBank owner = <GPIOPinBank> ctx
    try:
        out[0] = <uint32_t> owner._world_source(source)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _change_trampoline(void* ctx, uint32_t pin_index, int new_state, int old_state) noexcept:
    cdef GPIOPinBank owner = <GPIOPinBank> ctx
    try:
        owner._world_changed(pin_index, new_state, old_state)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _io_interrupt_trampoline(void* ctx) noexcept:
    cdef GPIOPinBank owner = <GPIOPinBank> ctx
    try:
        owner.rp2040.update_io_interrupt()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _input_trampoline(void* ctx, uint32_t pin_index, cppbool function_is_pwm) noexcept:
    cdef GPIOPinBank owner = <GPIOPinBank> ctx
    try:
        owner._world_input(pin_index, bool(function_is_pwm), 3)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class GPIOPinBank:
    """The C++ `PinBank` that a chip's pins share (30 GPIO pins, or the 6 QSPI pins): the pin state of all of them lives in one array, which the C++ IO_BANK0 / IO_QSPI / PADS
    windows (`native/_io.pyx`, `native/_pads.pyx`) work on directly. Each `GPIOPin` is a view onto one slot of it. A `GPIOPin` built without a bank gets a private bank of one pin, so
    a pin on its own (tests, a stand-in chip) behaves exactly as before.

    The bank is the host of its pins: it answers for the sources of the level (`rp2040.sio`, `rp2040.pio[i]`, `rp2040.pwm` registers), the IO interrupt line and the PWM/PIO reactions
    to an input change, and hands a pin's listeners their calls. It keeps the pin objects (`shells`) and whatever objects a C++ consumer's raw pointers point into (`_direct_refs`)
    alive, as one reference cycle with them."""

    def __cinit__(self, *args, **kwargs):
        self.shells = []
        self._direct_refs = []

    def __init__(self, rp2040, unsigned int count, always_output_enabled=False, unsigned int first=0, int mcu_bank=-1):
        cdef PinHost host
        cdef cppbool always[32]
        cdef unsigned int i
        if count < 1 or count > 32:
            raise ValueError(f"a pin bank holds 1 to 32 pins, not {count}")
        self.rp2040 = rp2040
        self.first = first
        self.shells = [None] * count
        for i in range(count):
            always[i] = bool(always_output_enabled)
        self._from_mcu = mcu_bank >= 0
        if mcu_bank >= 0:
            # the chip's C++ `Mcu` holds this bank (and the IO and PADS blocks on it): this object is the Python view of it
            self._bank = <PinBank*> <size_t> rp2040._native_pins_address(mcu_bank)
            return
        self._bank = &self._own
        host.source = _source_trampoline
        host.on_change = _change_trampoline
        mcu = native_mcu(rp2040)
        if mcu != NULL and first == 0 and count == 30:
            # the chip's GPIO bank: its IO_BANK0 line goes from the C++ pins straight to the chip's CPU. (The QSPI bank, and a pin on its own, ask `update_io_interrupt()`.)
            self._io_irq.mcu = mcu
            self._io_irq.bank = self._bank
            self._io_irq.line = 13  # IRQ.IO_BANK0
            host.io_interrupt = mcu_io_interrupt
            host.io_interrupt_ctx = &self._io_irq
        else:
            host.io_interrupt = _io_interrupt_trampoline
            host.io_interrupt_ctx = NULL
        host.input_changed = _input_trampoline
        host.ctx = <void*> self
        # Mirrors the Python constructor's field-initializer ordering: each pin's `_last_state` is captured with ctrl = 0 and
        # pad_value = 0 *before* the real defaults are written (core/pin.hpp's init does exactly that).
        if not self._own.init(count, host, always, first):
            raise_if_pending()

    # What the C++ bank asks of the world, as methods: the trampolines above call them for a bank made directly, and the chip's host calls them for the bank the `Mcu` holds.
    def _world_source(self, source):
        rp = self.rp2040
        if source == kSrcSioOe:
            return <unsigned int> rp.sio.gpio_output_enable
        if source == kSrcSioValue:
            return <unsigned int> rp.sio.gpio_value
        if source == kSrcPio0Oe:
            return <unsigned int> rp.pio[0].pin_directions
        if source == kSrcPio0Value:
            return <unsigned int> rp.pio[0].pin_values
        if source == kSrcPio1Oe:
            return <unsigned int> rp.pio[1].pin_directions
        if source == kSrcPio1Value:
            return <unsigned int> rp.pio[1].pin_values
        if source == kSrcSioQspiOe:
            return <unsigned int> rp.sio.qspi_gpio_output_enable
        if source == kSrcSioQspiValue:
            return <unsigned int> rp.sio.qspi_gpio_value
        if source == kSrcPwmDirection:
            return <unsigned int> rp.pwm.gpio_direction
        return <unsigned int> rp.pwm.gpio_value  # kSrcPwmValue

    def _world_changed(self, pin_index, new_state, old_state):
        cdef GPIOPin pin = <GPIOPin> self.shells[pin_index - self.first]
        value = _STATES[new_state]
        last_value = _STATES[old_state]
        for listener in pin._listeners:
            listener(value, last_value)

    def _world_input(self, pin_index, function_is_pwm, pio_mask):
        rp = self.rp2040
        if function_is_pwm:
            rp.pwm.gpio_on_input(pin_index)
        for pio_index, pio in enumerate(rp.pio):
            if not (pio_mask >> pio_index) & 1:
                continue
            for machine in pio.machines:
                if (
                    machine.enabled
                    and machine.waiting
                    and machine.wait_type == WaitType.PIN
                    and machine.wait_index == pin_index
                ):
                    machine.check_wait()

    def __len__(self):
        return self._bank.count()

    def input_values(self):
        """One bit per pin: what SIO's GPIO_IN returns."""
        return self._bank.input_values()

    def any_irq_value(self):
        """Is any pin's IRQ value set: the level of the IO interrupt line."""
        return bool(self._bank.any_irq_value())


cdef class GPIOPin:
    # Fields are declared in _gpio_pin.pxd (so native/_pio.pyx can cimport this class).

    def __cinit__(self, *args, **kwargs):
        self._direct_refs = []

    def __init__(self, rp2040, unsigned int index, name=None, bint always_output_enabled=False, bank=None):
        """`bank` is the shared `GPIOPinBank` this pin is a slot of (slot = `index` less the bank's first pin; the bank decides `always_output_enabled`); without one the pin gets a
        private bank of its own."""
        cdef GPIOPinBank owner
        cdef unsigned int slot
        if bank is None:
            owner = GPIOPinBank(rp2040, 1, always_output_enabled, index)
            slot = 0
        else:
            owner = <GPIOPinBank?> bank
            if index < owner.first or index - owner.first >= len(owner.shells):
                raise ValueError(f"pin {index} is not in the bank (pins {owner.first}..{owner.first + len(owner.shells) - 1})")
            slot = index - owner.first
            if owner.shells[slot] is not None:
                raise ValueError(f"pin {index} of the bank already has an object")
        self.rp2040 = rp2040
        self.name = name if name is not None else str(index)
        self._listeners = set()
        self._owner = owner
        self._bankp = owner._bank
        self._slot = slot
        self._pin = owner._bank.pin_ptr(slot)
        self._direct_refs = owner._direct_refs
        owner.shells[slot] = self

    def reset(self, io=True, pads=True, pad_reset=0x56):
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
        if not self._bankp.reset(self._slot, bool(io), bool(pads), <unsigned int> pad_reset):
            raise_if_pending()

    cdef bint add_direct_listener(self, PinChangeFn fn, void* ctx):
        return self._bankp.add_direct_listener(self._slot, fn, ctx)

    cdef bint remove_direct_listener(self, PinChangeFn fn, void* ctx):
        return self._bankp.remove_direct_listener(self._slot, fn, ctx)

    cdef PinBank* bank_ptr(self):
        return self._bankp

    cpdef check_for_updates(self):
        # Announces a change of the pin's state to the listeners - once, recording it first (core/pin.hpp).
        if not self._bankp.check_for_updates(self._slot):
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
    def driven(self):
        """True while a device holds the pin with `set_input_value()` (until `release_input()`): a wire between two pins asks this to let go of the far end."""
        return self._pin.driven

    def snapshot(self):
        """The pin's state as plain values: its CTRL and pad words, the three interrupt words, what the outside holds on it (seen, raw, driven) and what is derived from them."""
        return {
            "ctrl": self.ctrl,
            "pad_value": self.pad_value,
            "irq_enable_mask": self.irq_enable_mask,
            "irq_force_mask": self.irq_force_mask,
            "irq_status": self.irq_status,
            "seen": bool(self._pin.seen),
            "raw_input_value": bool(self._pin.raw_input_value),
            "driven": bool(self._pin.driven),
            "always_output_enabled": bool(self._pin.always_output_enabled),
            "status": int(self.status),
            "value": int(self.value),
            "input_value": bool(self.input_value),
            "output_enable": bool(self.output_enable),
            "output_value": bool(self.output_value),
            "irq_value": bool(self.irq_value),
            "raw_interrupt": bool(self.raw_interrupt),
            "raw_output_enable": bool(self.raw_output_enable),
            "raw_output_value": bool(self.raw_output_value),
        }

    # --- @property surface (behavioural parity with _gpio_pin.py) ---------------------------------------------------------

    @property
    def raw_interrupt(self):
        return bool(self._bankp.raw_interrupt(self._slot))

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
        if not self._bankp.raw_output_enable(self._slot, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(out)

    @property
    def raw_output_value(self):
        cdef cppbool out = False
        if not self._bankp.raw_output_value(self._slot, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(out)

    @property
    def _effective_raw_input_value(self):
        return bool(self._bankp.eff_raw_input(self._slot))

    @property
    def input_value(self):
        return bool(self._bankp.input_value(self._slot))

    @property
    def irq_value(self):
        return bool(self._bankp.irq_value(self._slot))

    @property
    def output_enable(self):
        cdef cppbool out = False
        if not self._bankp.raw_output_enable(self._slot, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(apply_override(out, (self._pin.ctrl >> 12) & 0x3))

    @property
    def output_value(self):
        cdef cppbool out = False
        if not self._bankp.raw_output_value(self._slot, self._pin.ctrl & 0x1F, &out):
            raise_if_pending()
        return bool(apply_override(out, (self._pin.ctrl >> 8) & 0x3))

    @property
    def status(self):
        cdef uint32_t out = 0
        if not self._bankp.status(self._slot, &out):
            raise_if_pending()
        return out

    @property
    def value(self):
        cdef int code = 0
        if not self._bankp.state_code(self._slot, &code):
            raise_if_pending()
        return _STATES[code]

    # --- methods ----------------------------------------------------------------------------------------------------------

    def set_input_value(self, value):
        if not self._bankp.set_input_value(self._slot, bool(value)):
            raise_if_pending()

    def release_input(self):
        """Mirrors _gpio_pin.py's own release_input() - hand the pad back to its pull resistor."""
        if not self._bankp.release_input(self._slot):
            raise_if_pending()

    def _apply_input_value(self, value):
        if not self._bankp.apply_input_value(self._slot, bool(value)):
            raise_if_pending()

    def refresh_input(self):
        if not self._bankp.refresh_input(self._slot):
            raise_if_pending()

    def pad_changed(self):
        """The pad register was written: if that moved the level the interrupt logic sees (IE, or a pull on a floating pin), it is an input change."""
        if not self._bankp.pad_changed(self._slot):
            raise_if_pending()

    def update_irq_value(self, value):
        if not self._bankp.update_irq_value(self._slot, <unsigned int> value):
            raise_if_pending()

    def add_listener(self, callback):
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)
