import logging
from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING

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


class GPIOPin:
    def __init__(self, rp2040: "RP2040", index: int, name: str | None = None, always_output_enabled: bool = False):
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
        self.pad_value = 0b0110110
        self.irq_enable_mask = 0
        self.irq_force_mask = 0
        self.irq_status = 0

        self._listeners: set[GPIOPinListener] = set()

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
        if self._always_output_enabled:
            return True

        index, rp2040, function_select = self.index, self.rp2040, self.function_select
        bitmask = 1 << index

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

        if function_select == FUNCTION_PWM:
            return bool(rp2040.pwm.gpio_value & bitmask)

        if function_select == FUNCTION_SIO:
            return bool(rp2040.sio.gpio_value & bitmask)

        if function_select == FUNCTION_PIO0:
            return bool(rp2040.pio[0].pin_values & bitmask)

        if function_select == FUNCTION_PIO1:
            return bool(rp2040.pio[1].pin_values & bitmask)

        return False

    @property
    def _effective_raw_input_value(self) -> bool:
        """The pad's actual electrical reading: whatever last actively drove it, or - if nothing
        ever has - resolved from whichever pull resistor is enabled (matching real hardware; a
        floating pin isn't guaranteed low). Bus-keeper mode (both pulls enabled) and no-pulls both
        fall back to the plain _raw_input_value default, same as before this resolution existed."""
        if self._driven:
            return self._raw_input_value
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
        if self.output_enable:
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

    def set_input_value(self, value: bool) -> None:
        self._driven = True
        self._apply_input_value(value)

    def _apply_input_value(self, value: bool) -> None:
        # Shared by set_input_value() (an actual external drive) and refresh_input() (just
        # re-evaluating IRQ/PWM/PIO wait state after e.g. input_enable toggled) - only the former
        # should mark the pin as driven, so _driven is set in set_input_value() itself, not here.
        self._raw_input_value = value
        prev_irq_value = self.irq_value
        if value and self.input_enable:
            self.irq_status |= IRQ_EDGE_HIGH | IRQ_LEVEL_HIGH
            self.irq_status &= ~IRQ_LEVEL_LOW
        else:
            self.irq_status |= IRQ_EDGE_LOW | IRQ_LEVEL_LOW
            self.irq_status &= ~IRQ_LEVEL_HIGH
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

    def check_for_updates(self) -> None:
        last_value, value = self._last_value, self.value
        if value != last_value:
            self._last_value = value
            for listener in self._listeners:
                listener(value, last_value)

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
