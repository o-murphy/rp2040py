# 0084. 0xCB Helios board

- Status: **Implemented (2026-08-19).**
- Conceived: 2026-08-19
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - flagged
  there as "LED + a single-wire RGB pin that looks WS2812-compatible (not fully confirmed)", a gap
  this record closes), [0083](0083-0xcb-gemini-board.md) (same vendor, same identifier-unsafe
  filename gap, one firmware family), [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec`
  firmware resolution), 0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

0xCB Helios (https://circuitpython.org/board/0xcb_helios/) - a split-mechanical-keyboard RP2040
mainboard, same vendor and product family as [0083](0083-0xcb-gemini-board.md)'s Gemini. Unlike
Gemini, it carries **two** separate LEDs: a plain status LED (GPIO17) and a genuinely separate
WS2812 RGB LED (GPIO25) - not one device wearing two names. 16 MiB flash, CircuitPython-only
(confirmed absent from `ports/rp2/boards/` at `micropython/micropython`'s default branch, same
check as [0083](0083-0xcb-gemini-board.md)).

Fourth board added off [0066](0066-board-support-expansion.md)'s CircuitPython-only checklist.

## Closing 0066's "not fully confirmed" flag

0066's own checklist entry for this board reads: "LED + a single-wire RGB pin that looks
WS2812-compatible (not fully confirmed)". CircuitPython's own port source doesn't settle it:
`pins.c` names the pin `RGB` and binds it to GPIO25, but no `MICROPY_HW_NEOPIXEL`/`NEOPIXEL` macro
anywhere in `ports/raspberrypi/boards/0xcb_helios/` says what protocol it speaks - a plain
single-color LED can be GPIO-shaped too.

The pico-sdk board header settles it: `lib/pico-sdk/src/boards/include/boards/0xcb_helios.h`
declares `PICO_DEFAULT_WS2812_PIN 25` explicitly, alongside `PICO_DEFAULT_LED_PIN 17` for the plain
LED, under a `// User LED and level shifted PIN` comment. GPIO25 genuinely is a WS2812-class part.
0xCB Gemini has **no** pico-sdk header of its own at all (checked while fetching this one) - an
asymmetry between two boards from the same vendor, noted here rather than chased further; out of
scope for this board file.

## What upstream actually says

`ports/raspberrypi/boards/0xcb_helios/` (fetched via `gh api`) plus the pico-sdk header above:

- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "0xCB Helios"`, `MICROPY_HW_LED_STATUS (&pin_GPIO17)` -
  a plain status-LED macro, not `MICROPY_HW_NEOPIXEL`. Also `DEFAULT_I2C_BUS_SDA/SCL` GPIO2/3,
  `DEFAULT_SPI_BUS_SCK/MOSI/MISO` GPIO22/23/20, `DEFAULT_UART_BUS_TX/RX` GPIO0/1.
- `pins.c`: `LED` -> GPIO17 (agreeing with `MICROPY_HW_LED_STATUS`), `RGB` -> GPIO25, **no `BUTTON`
  entry at all**. All three bus objects exposed (`board_i2c_obj`/`board_spi_obj`/`board_uart_obj`).
  A `VBUS_SENSE` pin on GPIO19, same split-keyboard-sense role
  [0083](0083-0xcb-gemini-board.md) documents for Gemini's copy of the pin.
- `mpconfigboard.mk`: USB VID `0x1209`/PID `0xCB74`, `"Helios"`/`"0xCB"`,
  `EXTERNAL_FLASH_DEVICES = "W25Q128JVxQ"` (128 Mbit = 16 MiB, agreeing with the pico-sdk header's
  own `PICO_FLASH_SIZE_BYTES` - same capacity as [0083](0083-0xcb-gemini-board.md)).
- No `PICO_DEFAULT_LED_PIN_INVERTED` in the pico-sdk header, so GPIO17 is assumed active-high by the
  same absence-based convention every prior board file here uses (stated explicitly rather than
  silently defaulted).
- `board.c`/`pico-sdk-configboard.h`: no board-specific init, the usual `MP_WEAK`/empty-stub
  defaults. No board-specific `link.ld`, so the generic `firmware_size = 1020K` applies.

Flash geometry: the generic CircuitPython `fs_start = 1020 KiB + 4 KiB = 0x100000`,
`fs_blockcount = 512` - same values as [0083](0083-0xcb-gemini-board.md).

## What was built

`boards/0xcb_helios.py` - a single flat file, one `BoardSpec`, one firmware family. Extras:
`LEDMock(gpio=17)` + `Ws2812(gpio=25)` + `BootselButton`, plus `board_with(on_pixels)` wiring only
the RGB LED's callback (the status LED has no callback surface - it's a plain on/off device).
Same identifier-unsafe-filename gap [0083](0083-0xcb-gemini-board.md) documented: `0xcb_helios`
starts with a digit, so no dotted `--board-spec module.path:ATTR` form exists, only the file-path
one; `pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]` gained a matching `N999` exemption
alongside Gemini's. Not modelled: any RESET/BOOT pushbutton beyond BOOTSEL, the `VBUS_SENSE` ADC
pin, the split-keyboard link hardware, the RP2040's own RTC. Firmware history fetched via
`scripts/fetch_firmware.py list` - 100 CircuitPython releases (the longest history of any board
file here, back to 8.0.0).

## Live-boot verification

CircuitPython 10.2.1:

```
board_id  0xcb_helios
statvfs   (512, 512)
has_LED True  has_BUTTON False  has_RGB True
```

Measured through real devices attached via `board_with`, no guest code run:

- **LED (GPIO17): 16 toggle events during boot** - CircuitPython drives it as its blinking status
  indicator, confirming `MICROPY_HW_LED_STATUS`/`pins.c`'s `LED` entry from the running firmware.
- **RGB (GPIO25): 0 WS2812 frames decoded during boot** - confirming this LED is *not* driven as a
  status indicator (no `MICROPY_HW_NEOPIXEL` binds it), the opposite of every other WS2812 board
  file in this project so far; guest code would have to drive it itself.

MicroPython fallback via `--image` (local `.uf2` path, not a bare tag - same
[0081](0081-waveshare-rp2040-one-board.md)/[0083](0083-0xcb-gemini-board.md) limit):

```
rp2040py micropython --board-spec boards/0xcb_helios.py:BOARD \
    --image ~/.cache/rp2040py/RPI_PICO-20260406-v1.28.0.uf2
-> statvfs (4096, 4096)
```

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- The 0xCB Gemini/Helios pico-sdk header asymmetry (Helios has one, Gemini doesn't) is noted but not
  investigated further - out of scope for either board file.
