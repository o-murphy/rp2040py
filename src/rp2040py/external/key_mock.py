from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("KeyMock",)


class KeyMock:
    """Key mock: a button on an ordinary GPIO pin, one side wired to the rail `active_high`
    implies (VCC if True, GND if False), the other to the pin. Pressed actively drives that rail's
    level - a real short, so `set_input_value()` is correct there. Released is different: a real
    button open-circuits, it does not drive anything - the pin then reads whatever its own pull
    resistor (if firmware enabled one via `machine.Pin(..., Pin.PULL_UP)`/`PULL_DOWN`) says, same
    as any other floating pad (see `GPIOPin.release_input()`, 0006's pull-resistor-resolution
    model). Forcing the released level via `set_input_value()` instead - the previous
    implementation here - would look identical when firmware configures the pull correctly, but
    would silently mask firmware forgetting to (see `external/bootsel_button.py`'s own
    `release()`, which this mirrors, for the same reasoning applied to `GPIO_QSPI_SS`)."""

    def __init__(self, gpio: int = 20, active_high: bool = True) -> None:
        self.gpio = gpio
        self.active_high = active_high  # True, if pressing gives HIGH (3.3V), False — if LOW (Ground/Pull-up)
        self._pressed = False
        self._rp2040: RP2040 | None = None

    def attach(self, rp2040: "RP2040") -> None:
        """Stores a reference to the simulator to be able to change the pin state."""
        self._rp2040 = rp2040
        # No initial drive: an untouched button is open-circuit, same as BootselButton.attach() -
        # release() below (release_input()) is what hands the pin to its own pull resistor.
        self.release()

    def press(self) -> None:
        """Simulates a button press - a real short to whichever rail `active_high` names."""
        if self._rp2040 is None:
            raise RuntimeError("KeyMock is not attached (attach() is not called)")
        self._pressed = True
        self._rp2040.gpio[self.gpio].set_input_value(self.active_high)

    def release(self) -> None:
        """Simulates releasing a button - lets go, rather than driving the opposite rail: the pin
        reads back through its own pull resistor (if firmware configured one), not a value this
        mock asserts on its behalf."""
        if self._rp2040 is None:
            raise RuntimeError("KeyMock is not attached (attach() is not called)")
        self._pressed = False
        self._rp2040.gpio[self.gpio].release_input()

    def click(self) -> None:
        """Fast short click simulator (press and immediately release)."""
        self.press()
        self.release()

    @property
    def is_pressed(self) -> bool:
        return self._pressed
