"""A minimal `ExternalDevice`: watches one GPIO pin and tracks whether firmware is currently
driving it high, plus how many times it's toggled. Not board-specific/CYW43-related - built
specifically as a validation vehicle for the `attach()`/`attach_external_devices()` machinery
itself (docs/records/0027-cyw43-wifi.md's "Implementation order" section, step 1: "prove the
`ExternalDevice` pattern on something already understood" before building `Cyw43439`'s much more
involved gSPI protocol on the same plumbing). A worked-example write-up of writing your own
`ExternalDevice` lives in docs/reference/external-devices-and-boards.md.

**Not a claim about real Pico/Pico W LED wiring.** On a plain Pico, the onboard LED genuinely is
wired straight to GPIO25 (`machine.Pin(25, ...)`/`machine.Pin("LED", ...)` - both resolve the same
way there) - accurate for `--board pico`. On a real Pico W, the onboard LED is instead wired to
the CYW43439 chip itself, not any RP2040 GPIO at all (see this doc's own "Onboard LED and pin
differences vs. plain Pico") - a GPIO listener structurally cannot see that. `boards.py` attaches
this to `pico_w` anyway, purely so the `ExternalDevice` plumbing gets exercised identically
regardless of `--board` - `pico_w`'s LED behavior here is a placeholder, not real hardware
emulation, and should be superseded there once `Cyw43439` (`external/cyw43/chip.py`, step 3)
grows its own LED handling.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("LEDMock",)


class LEDMock:
    """Implements `ExternalDevice` structurally (see `external/device.py`). `.on` reflects
    whatever firmware last drove `gpio` to (only meaningful once `attach()`'s listener has fired
    at least once - see its own note); `.toggle_count` counts every on<->off transition, for
    asserting things like "blinked N times" in a test without any real hardware involved.

    `active_low=True` is for the real, sourced case of a board wiring its LED so firmware drives
    the pin *low* to light it (e.g. Machdyne Werkzeug's green LED - the pico-sdk board header's own
    `PICO_DEFAULT_LED_PIN_INVERTED 1`) - `.on` still reports the LED's true logical state either
    way, not the raw pin level, so a caller never has to know a board's polarity to read it.
    Defaults to `False` (active-high), the wiring every existing board here uses."""

    def __init__(self, gpio: int = 25, *, active_low: bool = False) -> None:
        self.gpio = gpio
        self.active_low = active_low
        # False until the first real transition - matches a real LED's own unknown-until-driven
        # power-on state closely enough for a test double; nothing here claims to know the pin's
        # value before firmware has ever touched it (attach() doesn't read the pin's current
        # state, only future changes - see its own docstring).
        self.on = False
        self.toggle_count = 0
        self._unsubscribe: Callable[[], None] | None = None

    def attach(self, rp2040: "RP2040") -> None:
        """Wires a `GPIOPin.add_listener()` callback (the same primitive `ssi.py`/a future
        `Cyw43439` use) onto `rp2040.gpio[self.gpio]`. Only reacts to *changes* - `check_for_updates()`
        (`gpio_pin.py`) fires listeners on transitions, not on the pin's value at attach time - so
        `.on`/`.toggle_count` only start reflecting reality once firmware has actually written to
        the pin at least once, same pre-run-only timing contract as every other `ExternalDevice`
        (see `external/device.py`'s `attach_external_devices()` docstring)."""

        def _on_change(new_state: "GPIOPinState", _old_state: "GPIOPinState") -> None:
            lit_state = GPIOPinState.LOW if self.active_low else GPIOPinState.HIGH
            new_on = new_state == lit_state
            if new_on != self.on:
                self.on = new_on
                self.toggle_count += 1

        self._unsubscribe = rp2040.gpio[self.gpio].add_listener(_on_change)
