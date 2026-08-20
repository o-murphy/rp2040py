"""The RESET button, as an `ExternalDevice`.

The other button on almost every RP2040 board, and the one that could not be modelled until
docs/records/0089-one-reset-for-every-trigger.md's Phase 4 (docs/records/0057-run-pin-reset-hook.md
is the record this closes). Unlike BOOTSEL - which shorts an ordinary pad, `GPIO_QSPI_SS`, and is
therefore just a pin (see `bootsel_button.py`) - RESET pulls the **RUN** pin, which is not a GPIO,
is not memory-mapped, and has no pad, no pull configuration and no register anywhere on the chip.

The net, from a real vendor schematic (VCC-GND Studio's own `YD-2040 2022 V1.1 SCH`, quoted in
0057's addendum - the same board `boards/vcc_gnd_yd_rp2040/` models):

    3V3 --[ R12 10k ]--+-- RUN
                       +-- RST (ST-1185S) --/-- GND

Two consequences, and they are the whole design:

- **A press is a level, not a pulse.** The switch grounds RUN directly, with no series resistor,
  for as long as it is held - so `press()` *holds the chip in reset* (nothing executes, no
  simulated time passes) and `release()` is the edge that actually boots it. A one-shot
  "reset now" method would model a tap and quietly lie about a hold.
- **There is no pull direction to get wrong.** The board supplies the pull-up externally, so the
  released level is unconditional - unlike `KeyMock` (whose released level comes from whichever
  internal pull firmware configured) or `BootselButton` (which depends on the QSPI pad's own
  reset-value pull-up, record 0050). All of this device's difficulty is on the "what does a reset
  actually do" side, which is `RP2040.set_run_pin()`'s and `BaseDevice`'s half.

Not attached to `pico`/`pico_w`: a Raspberry Pi Pico has **no** RESET button (BOOTSEL is its only
one; RUN is exposed as a test point for the user to short themselves). Boards that do have one
attach it in their own `boards/` file.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("ResetButton",)


class ResetButton:
    """Simulates a board's RESET button: pressing grounds RUN and holds the chip in reset,
    releasing lets the board's pull-up take it high again, which resets and boots it."""

    def __init__(self) -> None:
        self._pressed = False
        self._rp2040: RP2040 | None = None

    def attach(self, rp2040: "RP2040") -> None:
        self._rp2040 = rp2040
        # No initial drive: an untouched button is open, and the board's external pull-up is what
        # holds RUN high. `RP2040` already starts with `run_pin_low` False, so there is nothing to
        # assert here either - the released level is the chip's own default.

    @property
    def _mcu(self) -> "RP2040":
        if self._rp2040 is None:
            raise RuntimeError("ResetButton is not attached (attach() was not called)")
        return self._rp2040

    def _drive(self, low: bool) -> None:
        """Hand the level change to the engine-room loop rather than applying it inline.

        `schedule_threadsafe()`, not a direct call, because of when this device is used: a RESET
        button is pressed *while the simulation is running*, from whatever thread the host UI, a
        test or a CLI handler happens to be on, and 0030's concurrency contract is that an
        `ExternalDevice` may not touch engine-room state from another thread. (`BootselButton` gets
        away with a direct pad write only because it is pressed before `start_execution()`.)

        This is also what makes the level safe to read once per batch: it can only ever change
        between batches, never during one - see `execute_batch()`.
        """
        mcu = self._mcu
        mcu.schedule_threadsafe(lambda: mcu.set_run_pin(low=low))

    def press(self) -> None:
        """Ground RUN: the chip stops executing and stays stopped until `release()`."""
        self._pressed = True
        self._drive(True)

    def release(self) -> None:
        """Let go - RUN rises through the board's pull-up, and *that* is what resets and boots the
        chip (via `RP2040.on_run_pin_reset`, which `BaseDevice` points at its own `hard_reset()`)."""
        self._pressed = False
        self._drive(False)

    def click(self) -> None:
        """Press and release, i.e. what a user tapping RESET does. The chip is held in reset for
        however long the engine room takes to run the two scheduled callbacks - a hold of
        unspecified length, not an instantaneous pulse, which is exactly what a real tap is too."""
        self.press()
        self.release()

    @property
    def is_pressed(self) -> bool:
        """The *host's* view of the button, updated as soon as `press()`/`release()` is called -
        the electrical effect lands on the engine-room loop a moment later. Same shape as
        `BootselButton.is_pressed`; read `RP2040.run_pin_low` for the chip's own view."""
        return self._pressed
