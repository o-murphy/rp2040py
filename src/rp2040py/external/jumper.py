"""`Jumper` - a wire between two GPIO pads: what MicroPython's hardware tests ask for in
`tests/target_wiring/rp2.py` ("Connect GPIO0 to GPIO1"). Whatever the chip drives onto one end shows
up as an external drive on the other; when neither end is driven the wire floats and both pads go
back to their own pull resistors (`GPIOPin.release_input()`).

The model has no drive strengths, so two ends both driven by the chip are a contention it does not
settle: the wire carries the level of the end with the lower index, and the other end keeps its own.

Only what reaches a pad is carried: SIO, PWM and PIO outputs (the functions `GPIOPin` models). The
UART, SPI and I2C functions are not routed to pads by the model (record 0106), so a jumper between a UART's TX and RX pins carries nothing yet.
"""

from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("Jumper",)

_DRIVEN = (GPIOPinState.LOW, GPIOPinState.HIGH)


class Jumper:
    """Implements `ExternalDevice` structurally (see `external/device.py`)."""

    def __init__(self, gpio_a: int, gpio_b: int) -> None:
        if gpio_a == gpio_b:
            raise ValueError("a jumper joins two different pins")
        self.gpio_a, self.gpio_b = sorted((gpio_a, gpio_b))
        self._rp2040: RP2040 | None = None
        self._busy = False  # (re-entry: carrying a level to one end raises that end's listener)

    def attach(self, rp2040: "RP2040") -> None:
        self._rp2040 = rp2040
        for index in (self.gpio_a, self.gpio_b):
            rp2040.gpio[index].add_listener(self._changed)
        self._carry()

    def _changed(self, _value: GPIOPinState, _last: GPIOPinState) -> None:
        self._carry()

    def _carry(self) -> None:
        if self._busy or self._rp2040 is None:
            return
        self._busy = True
        try:
            a, b = self._rp2040.gpio[self.gpio_a], self._rp2040.gpio[self.gpio_b]
            source, sink = (a, b) if a.value in _DRIVEN else (b, a) if b.value in _DRIVEN else (None, None)
            if source is None or sink is None:
                a.release_input()
                b.release_input()
                return
            sink.set_input_value(source.value == GPIOPinState.HIGH)
            if source.driven:  # (the far end was driven by this wire earlier: let go of it)
                source.release_input()
        finally:
            self._busy = False
