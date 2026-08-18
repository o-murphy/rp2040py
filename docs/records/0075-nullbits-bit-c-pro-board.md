# 0075. nullbits Bit-C PRO board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from),
  [0074](0074-machdyne-werkzeug-board.md) (`LEDMock.active_low`, needed by all three LEDs here),
  0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

nullbits Bit-C PRO (https://nullbits.co/bit-c-pro) - a Pico-class RP2040 board with a bigger 4 MiB
flash part (vs. a Pico's 2 MiB) and an RGB LED implemented as three separate active-low GPIO LEDs
- not a WS2812/NeoPixel, despite `board.json`'s own `"RGB LED"` feature tag: red on GPIO16, green
on GPIO17, blue on GPIO18.

## What upstream actually says

- MicroPython, `ports/rp2/boards/NULLBITS_BIT_C_PRO/`: `pins.csv` (`LED,GPIO16` / `LED_RED,GPIO16`
  / `LED_GREEN,GPIO17` / `LED_BLUE,GPIO18`), `mpconfigboard.h`'s own comment `// RGB LED, active
  low`, and `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 * 512 * 1024))` - the
  first board in this project whose storage size is a *formula* off `PICO_FLASH_SIZE_BYTES` rather
  than a fixed literal. The pico-sdk board header: `BIT_C_PRO_LED_R_PIN 16`/`_G_PIN 17`/`_B_PIN
  18`, `PICO_DEFAULT_LED_PIN_INVERTED 1` (confirming active-low), an explicit `// no
  PICO_DEFAULT_WS2812_PIN` comment, and `PICO_FLASH_SIZE_BYTES (4 * 1024 * 1024)` (the header's
  own comment: `// Bit-C PRO has 4MB SPI flash`).
- CircuitPython, `ports/raspberrypi/boards/nullbits_bit_c_pro/`: `mpconfigboard.h`
  (`CIRCUITPY_RGB_STATUS_INVERTED_PWM`, `CIRCUITPY_RGB_STATUS_R/_G/_B` -> GPIO16/17/18 - agreeing
  with MicroPython on every pin and the active-low polarity, independently), `mpconfigboard.mk`
  (`EXTERNAL_FLASH_DEVICES = "GD25Q32C"` - 32 Mbit = 4 MiB), `pins.c` (`LED_RED`/`LED_GREEN`/
  `LED_BLUE` -> GPIO16/17/18, `LED` -> GPIO18, no `NEOPIXEL` entry at all). No board-specific
  `link.ld`.

Flash geometry, MicroPython: `MICROPY_HW_FLASH_STORAGE_BYTES = 4 MiB - 512 KiB = 3584 KiB`, so
`fs_start = 4 MiB - 3584 KiB = 0x80000`, `fs_blockcount = 3584 KiB / 4 KiB = 896` - confirmed by
live boot (`os.statvfs('/')` reporting exactly 896 blocks). CircuitPython's own start is the
generic `0x100000`/`fs_blockcount=512` convention.

## Three active-low LEDs at once - `LEDMock.active_low` earns its keep

[0074](0074-machdyne-werkzeug-board.md) added `LEDMock`'s `active_low` argument for one LED on one
board. This board needed it on all three simultaneously, and - unlike Werkzeug's red LED, whose
polarity was an unstated gap - both firmware ports here independently confirm active-low for every
one of the three (MicroPython's own comment, `PICO_DEFAULT_LED_PIN_INVERTED`, and
`CIRCUITPY_RGB_STATUS_INVERTED_PWM` all agree), so nothing here is guessed.

## CircuitPython drives the RGB status LED at boot - PWM, not WS2812, same pattern otherwise

Checked directly (GPIO listeners, not inferred): booting the CircuitPython image, GPIO16/17
(red/green) toggled tens of thousands of times during boot and GPIO18 (blue) 8 times.
`CIRCUITPY_RGB_STATUS_INVERTED_PWM` is CircuitPython's status-indicator mechanism for boards
*without* a NeoPixel - a PWM-driven plain RGB LED instead - so this is the same
"status-indicator-drives-from-boot-with-no-guest-code" pattern
[0068](0068-waveshare-rp2040-zero-board.md)/[0069](0069-adafruit-feather-rp2040-board.md)/
[0070](0070-adafruit-itsybitsy-rp2040-board.md)/[0071](0071-adafruit-qtpy-rp2040-board.md) already
established for WS2812-equipped boards, just via three plain GPIOs instead of one addressable LED.
MicroPython's build declares no such default.

## What was built

`boards/nullbits_bit_c_pro/__init__.py` - both firmware families declared, with three
`LEDMock(gpio=N, active_low=True)` instances (red/green/blue) + `BootselButton`. Not modelled:
RESET (pulls RUN, not a GPIO - 0057), USB-C, the RP2040's own RTC. Firmware histories fetched via
`scripts/fetch_firmware.py list` and verified byte-for-byte against the fetched JSON - 17
MicroPython releases, 100 CircuitPython releases.

## Live-boot verification

MicroPython:

```
statvfs (4096, 4096, 896, 894, 894, 0, 0, 0, 0, 255)
pins ok Pin(GPIO16, mode=OUT) Pin(GPIO17, mode=OUT) Pin(GPIO18, mode=OUT)
```

CircuitPython: `board.board_id == "nullbits_bit_c_pro"`, `board.LED_RED`/`LED_GREEN`/`LED_BLUE`
all exist, and (checked directly with GPIO listeners) the boot-time PWM activity described above.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
