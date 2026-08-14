from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("KeyMock",)


class KeyMock:
    """Key mock. Allows you to simulate pressing (HIGH/LOW) on a specific RP2040 pin,
    so that the firmware can read this state via machine.Pin()."""

    def __init__(self, gpio: int = 20, active_high: bool = True) -> None:
        self.gpio = gpio
        self.active_high = active_high  # True, if pressing gives HIGH (3.3V), False — if LOW (Ground/Pull-up)
        self._pressed = False
        self._rp2040: RP2040 | None = None

    def attach(self, rp2040: "RP2040") -> None:
        """Stores a reference to the simulator to be able to change the pin state."""
        self._rp2040 = rp2040
        # Set the initial state (not pressed)
        self.release()

    def press(self) -> None:
        """Simulates a button press."""
        if self._rp2040 is None:
            raise RuntimeError("KeyMock is not attached (attach() is not called)")
        self._pressed = True

        # We determine the logical voltage level depending on the circuitry
        high_level = self.active_high
        self._rp2040.gpio[self.gpio].set_input_value(high_level)

    def release(self) -> None:
        """Simulates releasing a button."""
        if self._rp2040 is None:
            raise RuntimeError("KeyMock is not attached (attach() is not called)")
        self._pressed = False

        # Level when button is released (opposite of pressing)
        high_level = not self.active_high
        self._rp2040.gpio[self.gpio].set_input_value(high_level)

    def click(self) -> None:
        """Fast short click simulator (press and immediately release)."""
        self.press()
        self.release()

    @property
    def is_pressed(self) -> bool:
        return self._pressed
