import logging
from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING

from rp2040py._pin_bank import GPIOPinBank
from rp2040py.peripherals.pio_registers import WaitType

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

# Module-level: _apply_override() is a bare utility function with no GPIOPin/RP2040 instance in
# scope to hang a component-named logger call off of (see peripherals/peripheral.py's identical
# rationale for atomic_update()).
_logger = logging.getLogger(__name__)


__all__ = (
    "FUNCTION_PIO0",
    "FUNCTION_PIO1",
    "FUNCTION_PWM",
    "FUNCTION_SIO",
    "GPIOPin",
    "GPIOPinListener",
    "GPIOPinState",
)


class GPIOPinState(IntEnum):
    LOW = 0
    HIGH = 1
    INPUT = 2
    INPUT_PULL_UP = 3
    INPUT_PULL_DOWN = 4
    INPUT_BUS_KEEPER = 5


FUNCTION_PWM = 4
FUNCTION_SIO = 5
FUNCTION_PIO0 = 6
FUNCTION_PIO1 = 7

GPIOPinListener = Callable[[GPIOPinState, GPIOPinState], None]


def _apply_override(value: bool, override_type: int) -> bool:
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


IRQ_EDGE_HIGH = 1 << 3
IRQ_EDGE_LOW = 1 << 2
IRQ_LEVEL_HIGH = 1 << 1
IRQ_LEVEL_LOW = 1 << 0

# PADS_BANK0.GPIOn resets to 0x56 (RP2040 datasheet table 341, pico-sdk PADS_BANK0_GPIO0_RESET): OD 0, IE 1, DRIVE 1 (4 mA), PUE 0, PDE 1, SCHMITT 1, SLEWFAST 0.
# (This model started with 0b0110110 = 0x36, the value rp2040js carries: input disabled, 12 mA.)
PAD_RESET = 0x56
PAD_MASK = 0xFF  # 31:8 are reserved
CTRL_MASK = 0x3003331F  # IRQOVER 29:28, INOVER 17:16, OEOVER 13:12, OUTOVER 9:8, FUNCSEL 4:0 (datasheet table 285)


class GPIOPin:
    def __init__(
        self,
        rp2040: "RP2040",
        index: int,
        name: str | None = None,
        always_output_enabled: bool = False,
        bank: GPIOPinBank | None = None,
    ):
        """`bank` is the shared `GPIOPinBank` this pin is a slot of (slot = `index` less the bank's first pin; the bank decides `always_output_enabled`); without one the pin gets a
        private bank of its own, so a pin on its own (tests, a stand-in chip) behaves as before. The same contract as the native `GPIOPin`."""
        if bank is None:
            bank = GPIOPinBank(rp2040, 1, always_output_enabled, index)
            slot = 0
        else:
            if index < bank.first or index - bank.first >= len(bank.shells):
                raise ValueError(
                    f"pin {index} is not in the bank (pins {bank.first}..{bank.first + len(bank.shells) - 1})"
                )
            slot = index - bank.first
            if bank.shells[slot] is not None:
                raise ValueError(f"pin {index} of the bank already has an object")
            always_output_enabled = bank.always_output_enabled
        self._bank = bank
        self._slot = slot
        bank.shells[slot] = self
        self.rp2040 = rp2040
        self.index = index
        self.name = name if name is not None else str(index)
        # True only for the 6 dedicated QSPI pins (rp2040.qspi) - unlike general-purpose GPIOs,
        # which need an explicit SIO/PWM/PIO function-select (or an output-enable override) before
        # OUTOVER has any visible effect on .value, these are permanently wired to the XIP/SSI
        # peripheral with no separate "not driving the bus" state to model. Real firmware (e.g.
        # flash_cs_force() bit-banging QSPI_SS - "the bootrom does the same", per its own comment
        # in pico-sdk's flash.c) only ever touches OUTOVER on these pins, relying on output-enable
        # already effectively being on - which for a normal rp2040.gpio pin would require a
        # separate, explicit output_enable_override write this real code never makes.
        self._always_output_enabled = always_output_enabled

        self._raw_input_value = False
        # Whether something has ever actively driven this pin via set_input_value() (a button/
        # peripheral/test harness wired to it) - as opposed to it just floating. Real hardware
        # resolves a floating pin's read to whichever pull resistor is enabled instead of a fixed
        # low; _raw_input_value alone can't distinguish "actively driven low" from "never driven"
        # (both start/stay False), so firmware reading an undriven, pulled-up pin (e.g. Kaluma's
        # GP22 "skip auto-run" check) would otherwise always see it as low regardless of the pull.
        self._driven = False

        # NOTE: matches upstream rp2040js field-initializer ordering, where `lastValue = this.value`
        # is evaluated before `ctrl`/`padValue` receive their real initial values below - the
        # `value` getter reads ctrl=0/padValue=0 at that point, deterministically yielding
        # GPIOPinState.INPUT. Kept as-is for behavioral parity rather than hardcoding INPUT
        # directly, in case upstream's actual defaults ever change.
        self.ctrl = 0
        self.pad_value = 0
        self._last_value = self.value

        self.ctrl = 0x1F
        self.pad_value = PAD_RESET
        self.irq_enable_mask = 0
        self.irq_force_mask = 0
        self.irq_status = 0
        self._seen = False
        self._sync_irq_levels(edges=False)

        self._listeners: set[GPIOPinListener] = set()

    def reset(self, io=True, pads=True, pad_reset=PAD_RESET) -> None:
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
        if io:
            self.ctrl = 0x1F
            self.irq_enable_mask = 0
            self.irq_force_mask = 0
            self.irq_status = 0
        if pads:
            self.pad_value = pad_reset
        self._last_value = self.value
        self._seen = False
        self._sync_irq_levels(edges=False)  # a reset raises no edge; the level bits follow the pin

    @property
    def raw_interrupt(self) -> bool:
        return bool((self.irq_status & self.irq_enable_mask) | self.irq_force_mask)

    @property
    def is_slew_fast(self) -> bool:
        return bool(self.pad_value & 1)

    @property
    def schmitt_enabled(self) -> bool:
        return bool(self.pad_value & 2)

    @property
    def pulldown_enabled(self) -> bool:
        return bool(self.pad_value & 4)

    @property
    def pullup_enabled(self) -> bool:
        return bool(self.pad_value & 8)

    @property
    def drive_strength(self) -> int:
        return (self.pad_value >> 4) & 0x3

    @property
    def input_enable(self) -> bool:
        return bool(self.pad_value & 0x40)

    @property
    def output_disable(self) -> bool:
        return bool(self.pad_value & 0x80)

    @property
    def function_select(self) -> int:
        return self.ctrl & 0x1F

    @property
    def output_override(self) -> int:
        return (self.ctrl >> 8) & 0x3

    @property
    def output_enable_override(self) -> int:
        return (self.ctrl >> 12) & 0x3

    @property
    def input_override(self) -> int:
        return (self.ctrl >> 16) & 0x3

    @property
    def irq_override(self) -> int:
        return (self.ctrl >> 28) & 0x3

    @property
    def raw_output_enable(self) -> bool:
        index, rp2040, function_select = self.index, self.rp2040, self.function_select
        bitmask = 1 << index

        if self._always_output_enabled:  # a QSPI pin: SIO drives it through GPIO_HI_OE (datasheet 2.19.2, table 282), XIP (and the rest) keep it enabled
            return bool(rp2040.sio.qspi_gpio_output_enable & bitmask) if function_select == FUNCTION_SIO else True

        if function_select == FUNCTION_PWM:
            return bool(rp2040.pwm.gpio_direction & bitmask)

        if function_select == FUNCTION_SIO:
            return bool(rp2040.sio.gpio_output_enable & bitmask)

        if function_select == FUNCTION_PIO0:
            return bool(rp2040.pio[0].pin_directions & bitmask)

        if function_select == FUNCTION_PIO1:
            return bool(rp2040.pio[1].pin_directions & bitmask)

        return False

    @property
    def raw_output_value(self) -> bool:
        index, rp2040, function_select = self.index, self.rp2040, self.function_select
        bitmask = 1 << index

        if (
            self._always_output_enabled
        ):  # a QSPI pin: only SIO (GPIO_HI_OUT) drives its level here; the XIP side is not modelled
            return bool(rp2040.sio.qspi_gpio_value & bitmask) if function_select == FUNCTION_SIO else False

        if function_select == FUNCTION_PWM:
            return bool(rp2040.pwm.gpio_value & bitmask)

        if function_select == FUNCTION_SIO:
            return bool(rp2040.sio.gpio_value & bitmask)

        if function_select == FUNCTION_PIO0:
            return bool(rp2040.pio[0].pin_values & bitmask)

        if function_select == FUNCTION_PIO1:
            return bool(rp2040.pio[1].pin_values & bitmask)

        return False

    def _chip_drive_level(self) -> bool | None:
        """The level the chip itself drives onto the pad, or None if it does not drive it: the last announced state (HIGH / LOW are the only states in which the pad is driven; it
        is the state after the output enable, the overrides and PADS.OD). A QSPI pin that is not under SIO is driven by the XIP side, which is not modelled: not counted."""
        if self._last_value == GPIOPinState.HIGH or self._last_value == GPIOPinState.LOW:
            if self._always_output_enabled and self.function_select != FUNCTION_SIO:
                return None
            return self._last_value == GPIOPinState.HIGH
        return None

    @property
    def _effective_raw_input_value(self) -> bool:
        """The pad's actual electrical reading: whatever last actively drove it from outside; else the level the chip itself drives (the input is always connected, datasheet
        2.19.2: software reads back its own output); else - nothing drives it - resolved from whichever pull resistor is enabled (matching real hardware; a floating pin isn't
        guaranteed low). Bus-keeper mode (both pulls enabled) and no-pulls both fall back to the plain _raw_input_value default. An external drive wins over the chip's own: the
        model has no drive strengths to settle a contention with."""
        if self._driven:
            return self._raw_input_value
        driven_by_chip = self._chip_drive_level()
        if driven_by_chip is not None:
            return driven_by_chip
        if self.pullup_enabled and not self.pulldown_enabled:
            return True
        if self.pulldown_enabled and not self.pullup_enabled:
            return False
        return self._raw_input_value

    @property
    def input_value(self) -> bool:
        return _apply_override(self._effective_raw_input_value and self.input_enable, self.input_override)

    @property
    def irq_value(self) -> bool:
        return _apply_override(self.raw_interrupt, self.irq_override)

    @property
    def output_enable(self) -> bool:
        return _apply_override(self.raw_output_enable, self.output_enable_override)

    @property
    def output_value(self) -> bool:
        return _apply_override(self.raw_output_value, self.output_override)

    @property
    def status(self) -> int:
        """Returns the STATUS register value for the pin, as outlined in section 2.19.6 of the datasheet."""
        irq_to_proc = 1 << 26 if self.irq_value else 0
        irq_from_pad = 1 << 24 if self.raw_interrupt else 0
        in_to_peri = 1 << 19 if self.input_value else 0
        in_from_pad = 1 << 17 if self._effective_raw_input_value else 0
        oe_to_pad = 1 << 13 if self.output_enable else 0
        oe_from_peri = 1 << 12 if self.raw_output_enable else 0
        out_to_pad = 1 << 9 if self.output_value else 0
        out_from_peri = 1 << 8 if self.raw_output_value else 0
        return (
            irq_to_proc
            | irq_from_pad
            | in_to_peri
            | in_from_pad
            | oe_to_pad
            | oe_from_peri
            | out_to_pad
            | out_from_peri
        )

    @property
    def value(self) -> GPIOPinState:
        # PADS.OD "has priority over output enable from peripherals" (datasheet, GPIOn register): an output-disabled pad is not driven, whatever the function and the overrides say
        if self.output_enable and not self.output_disable:
            return GPIOPinState.HIGH if self.output_value else GPIOPinState.LOW
        # TODO: check what happens when we enable both pullup/pulldown
        # ANSWER: It is valid, see: 2.19.4.1. Bus Keeper Mode, datasheet p240
        if self.pulldown_enabled and self.pullup_enabled:
            # Pull high when high, pull low when low:
            return GPIOPinState.INPUT_BUS_KEEPER
        if self.pulldown_enabled:
            return GPIOPinState.INPUT_PULL_DOWN
        if self.pullup_enabled:
            return GPIOPinState.INPUT_PULL_UP
        return GPIOPinState.INPUT

    @property
    def driven(self) -> bool:
        """True while a device holds the pin with `set_input_value()` (until `release_input()`): a wire between two pins asks this to let go of the far end."""
        return self._driven

    def snapshot(self) -> dict[str, int | bool]:
        """The pin's state as plain values: its CTRL and pad words, the three interrupt words, what the outside holds on it (seen, raw, driven) and what is derived from them."""
        return {
            "ctrl": self.ctrl,
            "pad_value": self.pad_value,
            "irq_enable_mask": self.irq_enable_mask,
            "irq_force_mask": self.irq_force_mask,
            "irq_status": self.irq_status,
            "seen": bool(self._seen),
            "raw_input_value": bool(self._raw_input_value),
            "driven": bool(self._driven),
            "always_output_enabled": bool(self._always_output_enabled),
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

    def set_input_value(self, value: bool) -> None:
        self._driven = True
        self._apply_input_value(value)

    def release_input(self) -> None:
        """Stop driving the pad from outside, handing it back to whichever pull resistor is
        enabled (0006's model) - the electrical opposite of `set_input_value()`, not a way to set
        it high. A released button is high-Z, and on a pad like `GPIO_QSPI_SS` it is the pull-up,
        not the button, that then decides the level (see 0050 / `external/bootsel_button.py`)."""
        self._driven = False
        self._apply_input_value(self._effective_raw_input_value)

    def _seen_level(self) -> bool:
        """The level the interrupt logic sees: the pad's reading behind its input buffer (PADS IE) - "Level High: the GPIO pin is a logical 1" (datasheet 2.19.3)."""
        return self._effective_raw_input_value and self.input_enable

    def _sync_irq_levels(self, *, edges: bool) -> None:
        """INTR's level bits follow the seen level at all times (they are read only, "not latched"); the edge bits latch on a *transition* of it."""
        seen = self._seen_level()
        if edges and seen != self._seen:
            self.irq_status |= IRQ_EDGE_HIGH if seen else IRQ_EDGE_LOW
        self._seen = seen
        self.irq_status = (self.irq_status & ~(IRQ_LEVEL_HIGH | IRQ_LEVEL_LOW)) | (
            IRQ_LEVEL_HIGH if seen else IRQ_LEVEL_LOW
        )

    def _apply_input_value(self, value: bool) -> None:
        # Shared by set_input_value() (an actual external drive) and refresh_input() (just
        # re-evaluating IRQ/PWM/PIO wait state after e.g. input_enable toggled) - only the former
        # should mark the pin as driven, so _driven is set in set_input_value() itself, not here.
        self._raw_input_value = value
        prev_irq_value = self.irq_value
        self._sync_irq_levels(edges=True)
        if self.irq_value != prev_irq_value:
            self.rp2040.update_io_interrupt()
        if self.function_select == FUNCTION_PWM:
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

    def pad_changed(self) -> None:
        """The pad register was written: if that moved the level the interrupt logic sees (IE, or a pull on a floating pin), it is an input change."""
        if self._seen_level() != self._seen:
            self.refresh_input()

    def check_for_updates(self) -> None:
        last_value, value = self._last_value, self.value
        if value != last_value:
            input_before = self.input_value
            self._last_value = value
            for listener in self._listeners:
                listener(value, last_value)
            # the chip's own drive is part of what the pad reads: if it moved the level the interrupt logic sees, or the input the PIO / PWM read, it is an input change
            if self._seen_level() != self._seen or self.input_value != input_before:
                self.refresh_input()

    def refresh_input(self) -> None:
        self._apply_input_value(self._raw_input_value)

    def update_irq_value(self, value: int) -> None:
        if value & IRQ_EDGE_LOW and self.irq_status & IRQ_EDGE_LOW:
            self.irq_status &= ~IRQ_EDGE_LOW
            self.rp2040.update_io_interrupt()
        if value & IRQ_EDGE_HIGH and self.irq_status & IRQ_EDGE_HIGH:
            self.irq_status &= ~IRQ_EDGE_HIGH
            self.rp2040.update_io_interrupt()

    def add_listener(self, callback: GPIOPinListener) -> Callable[[], None]:
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)
