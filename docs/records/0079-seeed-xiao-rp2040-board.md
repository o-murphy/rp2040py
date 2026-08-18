# 0079. Seeed Studio XIAO RP2040 board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from),
  [0071](0071-adafruit-qtpy-rp2040-board.md) (the WS2812-plus-power-pin shape this board repeats),
  [0075](0075-nullbits-bit-c-pro-board.md)/[0077](0077-pimoroni-tiny2040-board.md) (the
  three-`LEDMock` active-low RGB shape this board repeats), [0074](0074-machdyne-werkzeug-board.md)
  (`LEDMock.active_low` itself), [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec`
  firmware resolution), 0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

Seeed Studio XIAO RP2040 (https://www.seeedstudio.com/XIAO-RP2040-v1-0-p-5026.html) - a 21x17.5mm
RP2040 board with plain-Pico flash geometry and, unusually, **two RGB LED systems at once**: a
WS2812 NeoPixel on GPIO12 with a real power-enable pin on GPIO11, *and* a second RGB LED built from
three plain active-low GPIO LEDs (green GPIO16, red GPIO17, blue GPIO25). The first board in this
project to carry both kinds. Both firmware families exist, under **different board ids** -
MicroPython's `SEEED_XIAO_RP2040` vs. CircuitPython's `seeeduino_xiao_rp2040`.

## What upstream actually says

- MicroPython, `ports/rp2/boards/SEEED_XIAO_RP2040/`: `mpconfigboard.cmake`
  (`set(PICO_BOARD "seeed_xiao_rp2040")`), `mpconfigboard.h`
  (`MICROPY_HW_BOARD_NAME "Seeed Studio XIAO RP2040"`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (1408 * 1024)`, and **no USB VID/PID at all** - both `#define`s
  commented out under its own `// No VID/PID defined for the Seeed XIAO RP2040`, the first board
  this project has added with no USB identity declared on the MicroPython side), `pins.csv`
  (`NEOPIXEL_POWER,GPIO11` / `NEOPIXEL,GPIO12` / `LED_R,GPIO17` / `LED_G,GPIO16` / `LED_B,GPIO25` /
  `LED,GPIO25`). The pico-sdk board header `seeed_xiao_rp2040.h`: `PICO_DEFAULT_LED_PIN 25` +
  `PICO_DEFAULT_LED_PIN_INVERTED 1`, `PICO_DEFAULT_WS2812_PIN 12`,
  `PICO_DEFAULT_WS2812_POWER_PIN 11`, `PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)`, and
  `PICO_XOSC_STARTUP_DELAY_MULTIPLIER 64` (`// On some samples, the xosc can take longer to
  stabilize than is usual` - a real board quirk this project doesn't model; the emulated clock has
  no oscillator warm-up phase at all).
- CircuitPython, `ports/raspberrypi/boards/seeeduino_xiao_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO12)`, `CIRCUITPY_STATUS_LED_POWER (&pin_GPIO11)` - agreeing with
  MicroPython on both NeoPixel pins), `mpconfigboard.mk` (USB VID `0x2886`/PID `0x0042` - a real
  pair where MicroPython declines to define one; `EXTERNAL_FLASH_DEVICES = "P25Q16H"`, 16 Mbit =
  2 MiB, agreeing with the pico-sdk header), `pins.c` (`LED_GREEN`/`LED_RED`/`LED_BLUE`/`LED` ->
  GPIO16/17/25/25, `NEOPIXEL`/`NEOPIXEL_POWER` -> GPIO12/11 - agreeing with `pins.csv` on all six),
  `board.c` (no board-specific init). No board-specific `link.ld`.

Flash geometry is byte-for-byte a plain Pico's: `fs_start = 2 MiB - 1408 KiB = 0xa0000`,
`fs_blockcount = 352` for MicroPython; the generic `0x100000`/`512` for CircuitPython.

## The polarity question, and where the answer came from

`LEDMock.active_low` on all three plain LEDs is the one fact **neither firmware port fully
states**, and it is worth recording how it was settled rather than just what it settled on.

[0075](0075-nullbits-bit-c-pro-board.md)/[0077](0077-pimoroni-tiny2040-board.md) each had *two*
independent confirmations: the pico-sdk header's `PICO_DEFAULT_LED_PIN_INVERTED 1` **and**
CircuitPython's `CIRCUITPY_RGB_STATUS_INVERTED_PWM` naming all three pins. This board has neither
half in full. CircuitPython declares no RGB status LED at all here (it drives the *NeoPixel* as its
status indicator instead), and `PICO_DEFAULT_LED_PIN_INVERTED` by definition qualifies only
`PICO_DEFAULT_LED_PIN` - GPIO25/blue. GPIO16 and GPIO17 are unaddressed by both.

"They're one RGB package, so all three must match" is exactly the plausible-sounding inference the
3g rule exists to reject, so it was not used. Instead the polarity comes from Seeed's own board
documentation, which states it for the whole set:

> The behavior of the built-in programmable Single-colour LEDs (They are red, blue and green) are
> reversed to the one on an Arduino. On the Seeed Studio XIAO RP2040, the pin has to be pulled low
> to enable.
> - https://wiki.seeedstudio.com/XIAO-RP2040/ (which also publishes the board schematic,
>   https://files.seeedstudio.com/wiki/XIAO-RP2040/res/Seeed-Studio-XIAO-RP2040-v1.3.pdf)

That vendor statement agrees with the pico-sdk header on the one pin they overlap on (GPIO25),
which is what makes it usable for the other two rather than merely asserted. Checked and rejected
along the way: `earlephilhower/arduino-pico`'s `variants/seeed_xiao_rp2040/pins_arduino.h`, which
agrees on all three pin numbers but says nothing at all about polarity.

## What was built

`boards/seeed_xiao_rp2040.py` - a single flat file, one `BoardSpec`, both firmware families.
Extras: `Ws2812(gpio=12)` + `LEDMock(gpio=16/17/25, active_low=True)` + `BootselButton`, plus the
usual `board_with(on_pixels)` closure for the NeoPixel's frames. File named after MicroPython's
board id (case-normalized), which is also the pico-sdk board name; CircuitPython's differing
`seeeduino_xiao_rp2040` is cited in the docstring next to every number it contributed - the same
id-disagreement situation `boards/weactstudio/` documents.

Not modelled: the NeoPixel power-enable pin's actual gating (GPIO11 is a real pin both ports drive,
but `Ws2812` has no concept of a power input - it decodes whatever waveform arrives on its data pin
regardless of any other pin's state; identical treatment to
[0070](0070-adafruit-itsybitsy-rp2040-board.md)/[0071](0071-adafruit-qtpy-rp2040-board.md), and the
gap is stated rather than hidden), the RESET button (pulls RUN, not a GPIO - 0057), the
`PICO_XOSC_STARTUP_DELAY_MULTIPLIER` crystal warm-up, USB-C, the RP2040's own RTC. Neither port
declares a separate GPIO pushbutton, so BOOTSEL is the only button modelled.

Firmware histories fetched via `scripts/fetch_firmware.py list` - 5 MicroPython releases (this
board's page carries only one stable tag, `1.28.0`, plus previews) and 130 CircuitPython releases.

## Live-boot verification

MicroPython 1.28.0:

```
BOARD    Seeed Studio XIAO RP2040 with RP2040
statvfs  (4096, 4096, 352, 350, 350, 0, 0, 0, 0, 255)
```

CircuitPython 10.2.1: `board.board_id == "seeeduino_xiao_rp2040"`, `os.statvfs('/')[:3] ==
(512, 512, 2008)`.

Devices, measured through real instances attached to the board (not asserted from the sources):

- The NeoPixel is CircuitPython's own status indicator here - **11 frames decoded before any guest
  code ran**, same as `vcc_gnd_yd_rp2040`/`waveshare_rp2040_zero`. A guest
  `neopixel_write(pin, bytearray([0xFF, 0x00, 0xAA]))` then came back off the wire as `ff 00 aa`.
  MicroPython: 0 frames at boot, as expected for a build that declares no default.
- The three plain LEDs are driven by **neither** family at boot - `.on = False`/`.toggle_count = 0`
  on all three after boot in both, unlike
  [0075](0075-nullbits-bit-c-pro-board.md)/[0077](0077-pimoroni-tiny2040-board.md) where
  CircuitPython drove them as its PWM status indicator. Guest MicroPython writing `0` then `1` to
  GPIO16/17/25 produced `toggle_count == 2` on each with `.on` back to `False` - i.e. `0` lit them,
  confirming the active-low modelling end to end.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- The NeoPixel power pin still isn't gated (see above) - the same open item every
  power-pin board in this project shares, not new here.
