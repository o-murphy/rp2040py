"""The BOOTSEL button, as an `ExternalDevice`.

Every RP2040 board that boots from QSPI flash has one, `pico` and `pico_w` alike, and it is wired
the same way on both: it does not sit on any ordinary GPIO at all. Pressing it shorts
**`GPIO_QSPI_SS`** to ground; releasing it leaves the line to that pad's own pull-up (see
docs/records/0050-qspi-pad-reset-values.md, which is where those pad defaults came from). Firmware
therefore reads the pad and treats **low as pressed** - the inverse of a typical `KeyMock` button.

Reading it on real hardware is awkward for a reason worth knowing: driving/sampling the QSPI pads
disturbs XIP, so the polling routine has to run from RAM. That is a firmware-side constraint, not
something this emulation imposes - here the pin state is simply readable via SIO's `GPIO_HI_IN`.

Attached to both boards by `boards.py`, so `machine.bootloader()`-style paths and CircuitPython's
own BOOTSEL polling have something real to read. Held down at power-on it is what a real board
uses to enter USB mass-storage bootloader mode; this device models the *pin*, not that mode.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("BootselButton",)

# Index into `RP2040.qspi` - SCLK, SS, SD0..SD3.
_QSPI_SS = 1


class BootselButton:
    """Simulates the BOOTSEL button. Active-low by construction: pressed pulls `GPIO_QSPI_SS`
    down, released hands the pad back to its pull-up rather than driving it high."""

    def __init__(self) -> None:
        self._pressed = False
        self._rp2040: RP2040 | None = None

    def attach(self, rp2040: "RP2040") -> None:
        self._rp2040 = rp2040
        # No initial drive at all: an untouched button is high-Z, and the pad's own pull-up is what
        # makes SS read high. Forcing it high here would look identical to firmware but would mask
        # a regression in exactly the pad defaults this device exists to exercise.

    @property
    def _pin(self):
        if self._rp2040 is None:
            raise RuntimeError("BootselButton is not attached (attach() was not called)")
        return self._rp2040.qspi[_QSPI_SS]

    def press(self) -> None:
        """Short QSPI_SS to ground, which is what firmware reads as "BOOTSEL held"."""
        pin = self._pin
        self._pressed = True
        pin.set_input_value(False)

    def release(self) -> None:
        """Let go - the pad floats back up through its own pull-up."""
        pin = self._pin
        self._pressed = False
        pin.release_input()

    def click(self) -> None:
        self.press()
        self.release()

    @property
    def is_pressed(self) -> bool:
        return self._pressed
