# 0051. BOOTSEL button as an `ExternalDevice`

- Status: Implemented (2026-08-16)
- Conceived: 2026-08-16
- Related: 0050 (the pad defaults this device reads against - without them it could not work at
  all), 0030 (`ExternalDevice` concurrency model), 0029 (board composition), 0049 (this is the
  first non-trivial worked example that record's "write your own device" section can point at)

## Decision

The BOOTSEL button is a `KeyMock`-shaped device, with one difference that is the whole point of it:
it is **not on a GPIO**. On every RP2040 board that boots from QSPI flash - `pico` and `pico_w`
alike, with identical wiring - the button shorts **`GPIO_QSPI_SS`** to ground. Firmware reads the
pad through SIO's `GPIO_HI_IN` and treats **low as pressed**, the inverse of a typical button mock.

`external/bootsel_button.py` therefore drives `rp2040.qspi[1]`, not `rp2040.gpio[n]`, and
`boards.py` attaches it to both boards unconditionally. Unlike `LEDMock`'s entry in that same
registry - which carries a caveat, because a real Pico W's LED is on the CYW43439 rather than
GPIO25 - there is nothing board-specific to get wrong here.

## Release is high-Z, not "drive high"

`release()` calls a new `GPIOPin.release_input()` rather than `set_input_value(True)`. Both make
the pad read high, so this looks like a distinction without a difference - it is not:

- A real released button is high-Z; what pulls the line up is the pad's own pull-up
  (`GPIO_QSPI_SS` resets to `0x5A`, per 0050). Driving it high models the wrong circuit.
- More usefully, driving it high would **mask a regression in exactly the defaults this device
  exercises**. If the QSPI pad reset values were broken again tomorrow, a `release()` that forces
  the line high still reads high, and the test passes; a `release()` that hands the pad back to a
  pull-up that is no longer there reads low, and the test fails - which is what you want.

`release_input()` sets `_driven = False` and re-applies the pull-resolved level so IRQ/PIO state
stays consistent. It is the electrical opposite of `set_input_value()` and was simply missing:
`_driven` was a one-way flag until now. Added to both backends (`_gpio_pin.py` and
`native/_gpio_pin.pyx`).

## What this does *not* model

The pin, not the mode. Holding BOOTSEL at power-on makes a real board come up in USB
mass-storage bootloader mode; nothing here implements that, and the bootrom path that would
implement it is not exercised by this emulator's cold boot (which jumps straight to
`FLASH_START_ADDRESS`). What the device does give is a readable pad, so firmware that polls
BOOTSEL - CircuitPython does, from RAM, during boot - sees a real answer, and
`machine.bootloader()`-style paths have something to react to.

## A test that guarded nothing

Worth recording, because it is the same mistake this record argues against one section up. The
first version of the "both boards get one" test built each board and asserted that `GPIO_HI_IN`
read SS high - which is true from the pad's own pull-up whether or not a button is attached. It
would have passed with `BootselButton` deleted from both board specs. Replaced with an assertion
against `BOARDS[board].extras` directly, and checked by mutation: removing the device from
`boards.py` now fails the test.

The general shape to watch for here: when a device's *released/idle* state is indistinguishable
from "device absent", any assertion about the idle state proves nothing about the device.

## Verified

`tests/test_bootsel_button.py`: press pulls SS low, release lets the pull-up win, release leaves
`_driven` false (the point above), click round-trips, using it before `attach()` raises, and both
boards come up with SS reading high. Full `pre-commit run --all-files` clean, and CircuitPython
10.2.1 still boots to its REPL with the device attached.
