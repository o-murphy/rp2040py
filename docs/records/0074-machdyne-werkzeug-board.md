# 0074. Machdyne Werkzeug board, and `LEDMock` gains `active_low`

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from),
  [0073](0073-garatronic-pybstick26-rp2040-board.md) (the previous board, same flash geometry),
  0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

Machdyne Werkzeug (https://machdyne.com/product/werkzeug-multi-tool/) - a Pico-class RP2040 board
with a smaller 1 MiB flash part (vs. a Pico's 2 MiB) and two plain LEDs: green on GPIO20, red on
GPIO21. MicroPython-only: no CircuitPython port exists for this board (`gh api` 404s on
`ports/raspberrypi/boards/machdyne_werkzeug/`).

## What upstream actually says

- `pins.csv`: `LED_GREEN,GPIO20` / `LED_RED,GPIO21` - the only two entries beyond plain `GPx`/
  `PMODx`/`USBA_*` breakout aliases.
- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "Machdyne Werkzeug"`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (384 * 1024)` - no USB VID/PID override, so this board uses the
  generic rp2-port default.
- The pico-sdk board header: `PICO_DEFAULT_LED_PIN 20` **with `PICO_DEFAULT_LED_PIN_INVERTED 1`** -
  the green LED is wired active-low. `PICO_FLASH_SIZE_BYTES (1 * 1024 * 1024)` matches
  [0073](0073-garatronic-pybstick26-rp2040-board.md)'s board exactly.

## `LEDMock` had no polarity concept at all - this is the first board that needed one

Every plain LED this project has modelled so far (`vcc_gnd_yd_rp2040`, `weactstudio`,
`adafruit_feather_rp2040`, `adafruit_itsybitsy_rp2040`, `garatronic_pybstick26_rp2040`) happened to
be active-high, so `LEDMock` never needed to represent the alternative. This board's green LED is
genuinely, sourcedly active-low - the first real case - so `rp2040py.external.led_mock.LEDMock`
gained an `active_low: bool = False` constructor argument (default preserves every existing board's
behavior unchanged) mirroring the polarity pattern `KeyMock.active_high` already established for
buttons. `.on`/`.toggle_count` report the LED's true logical state either way, not the raw pin
level - a caller never has to know a board's wiring polarity to read it.

Verified two ways: `tests/test_led_mock.py::test_active_low_reports_on_when_the_pin_is_driven_low`
(direct register-level drive, mirroring the file's existing pattern), and live-boot - constructing
the board's own green-LED factory, attaching it to a real `RP2040`, and driving GPIO20 low the same
way firmware would: `.on` reported `True`.

**The red LED's polarity is not stated in either source checked** - the pico-sdk header only names
a single "default LED" (green), and `pins.csv` gives no polarity information at all. Modelled
active-high (this project's default) rather than guessed active-low by inference from its sibling -
a stated gap, not an oversight; a real schematic would be needed to settle it (the same shape of
gap `adafruit_feather_rp2040`'s second button and `garatronic_pybstick26_rp2040`'s RESET button
document).

## Flash geometry

`fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES = 1 MiB - 384 KiB = 0xa0000`,
`fs_blockcount = 384 KiB / 4 KiB = 96` (0035's derivation) - confirmed by live boot
(`os.statvfs('/')` reporting exactly 96 blocks). Identical numbers to
`garatronic_pybstick26_rp2040`, since both boards share the same 1 MiB/384 KiB split.

## What was built

`boards/machdyne_werkzeug/__init__.py` - one firmware family (MicroPython only), with
`LEDMock(gpio=20, active_low=True)` + `LEDMock(gpio=21)` + `BootselButton`. Not modelled: RESET
(pulls RUN, not a GPIO - 0057), the USB-A host port (`USBA_POWER`/`USBA_DN`/`USBA_DP`/
`USBA_DP_PU` in `pins.csv` - a real onboard receptacle exposed as plain GPIO, out of scope for a
board-file device mix), the PMOD headers (plain GPIO breakouts). Firmware history fetched via
`scripts/fetch_firmware.py list` and verified byte-for-byte against the fetched JSON - 9 releases.

## Live-boot verification

```
statvfs (4096, 4096, 96, 94, 94, 0, 0, 0, 0, 255)
pins ok Pin(GPIO20, mode=OUT) Pin(GPIO21, mode=OUT)
```

Plus the direct `LEDMock` active-low check described above.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- The red LED's real polarity stays an open gap pending a real schematic.
