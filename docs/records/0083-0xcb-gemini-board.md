# 0083. 0xCB Gemini board

- Status: **Implemented (2026-08-19).**
- Conceived: 2026-08-19
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from -
  CircuitPython-only checklist, third board off it after
  [0081](0081-waveshare-rp2040-one-board.md)/[0082](0082-waveshare-rp2040-tiny-board.md)),
  [0081](0081-waveshare-rp2040-one-board.md) (same WS2812-only, GPIO16, one-firmware-family shape,
  and the `--image` bare-tag-doesn't-work correction this board reuses), 0080 (16 MiB flash part
  precedent, `W25Q128JV`), [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec` firmware
  resolution), 0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

0xCB Gemini (https://circuitpython.org/board/0xcb_gemini/) - a split-mechanical-keyboard RP2040
mainboard whose only onboard indicator is a single WS2812 RGB LED on GPIO16, on a 16 MiB flash
part, with all of GPIO0-29 broken out and all three buses (I2C/SPI/UART) exposed as board objects.
**CircuitPython-only**: MicroPython ships no `ports/rp2/boards/` port for it (confirmed absent from
the current `ports/rp2/boards/` listing at `micropython/micropython`'s default branch - no
`0XCB`/`GEMINI` entry among its 45 board directories).

Third board added off [0066](0066-board-support-expansion.md)'s CircuitPython-only checklist, after
[0081](0081-waveshare-rp2040-one-board.md)/[0082](0082-waveshare-rp2040-tiny-board.md).

## What upstream actually says

`ports/raspberrypi/boards/0xcb_gemini/` (fetched via `gh api`, no local checkout available):

- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "0xCB Gemini"`, `MICROPY_HW_NEOPIXEL (&pin_GPIO16)` (so
  the RGB LED is CircuitPython's own *status* indicator), `DEFAULT_I2C_BUS_SDA/SCL` GPIO2/3,
  `DEFAULT_SPI_BUS_SCK/MOSI/MISO` GPIO6/7/4, `DEFAULT_UART_BUS_TX/RX` GPIO0/1. No plain-LED macro
  of any kind.
- `mpconfigboard.mk`: USB VID `0x1209`/PID `0xCB65` (`0x1209` is the shared pid.codes
  open-hardware VID), `"Gemini"`/`"0xCB"`, `EXTERNAL_FLASH_DEVICES = "W25Q128JVxQ"` (128 Mbit =
  16 MiB - same capacity as [0080](0080-sparkfun-promicro-board.md)'s `W25Q128JVxM`, different
  package suffix).
- `pins.c`: `GP0`-`GP29` all broken out (with the usual `A0`-`A3`/`TX`/`RX`/`SDA`/`SCL`/`SDI`/
  `CS`/`SCK`/`SDO`/`NEOPIXEL` aliases), **no `LED` and no `BUTTON` entry at all**. Unlike
  [0081](0081-waveshare-rp2040-one-board.md), all three bus objects *are* exposed
  (`board_i2c_obj`/`board_spi_obj`/`board_uart_obj`), agreeing with the `DEFAULT_*_BUS_*` macros.
  A `VBUS_SENSE` pin on GPIO19 carries its own comment citing
  `https://docs.keeb.supply/0xcb-gemini/guide/#split-capability` - a voltage-divider sense pin for
  this board's split-keyboard-half detection, not a fixed onboard chip.
- `board.c`: no board-specific init, only the `MP_WEAK supervisor/shared/board.c` defaults.
  `pico-sdk-configboard.h` is the empty stub CircuitPython requires every board to carry. No
  board-specific `link.ld` (directory holds only those five files), so `link-rp2040.ld`'s default
  `firmware_size = 1020K` applies.

Flash geometry: the generic CircuitPython `fs_start = 1020 KiB + 4 KiB = 0x100000`,
`fs_blockcount = 512`. The 16 MiB part changes nothing in the layout - only more of the chip is
genuinely present, same as [0080](0080-sparkfun-promicro-board.md)'s CircuitPython side.

## A naming wrinkle this board turned up: no dotted-module-path form

Every board file added so far has an identifier-safe filename, so the docstrings all show both a
`--board-spec path:ATTR` form and a `PYTHONPATH=. --board-spec module.path:ATTR` form. `0xcb_gemini`
is not a legal Python identifier - it starts with a digit - so `import boards.0xcb_gemini` cannot
work at all. `boards/0xcb_gemini.py`'s docstring documents only the file-path form and states the
gap explicitly rather than silently dropping the second example.

## What was built

`boards/0xcb_gemini.py` - a single flat file, one `BoardSpec`, one firmware family. Extras:
`Ws2812(gpio=16)` + `BootselButton`, plus the usual `board_with(on_pixels)` closure. Not modelled:
any RESET/BOOT pushbutton beyond BOOTSEL (`pins.c` declares no `BUTTON`, and RESET pulls RUN rather
than a GPIO per 0057), the `VBUS_SENSE` ADC pin (a sense input, no fixed chip behind it), the
split-keyboard half-to-half link hardware itself (a second physical PCB, out of scope), and the
RP2040's own RTC. Firmware history fetched via `scripts/fetch_firmware.py list` - 38 CircuitPython
releases.

## Live-boot verification

CircuitPython 10.2.1:

```
board_id  0xcb_gemini
statvfs   (512, 512)
has_LED False  has_BUTTON False
```

The WS2812, measured through a real `Ws2812` attached via `board_with`: CircuitPython drives it as
its own status indicator - **11 frames decoded before any guest code ran**, matching
[0080](0080-sparkfun-promicro-board.md)'s own measured boot count on unrelated hardware.

MicroPython fallback via `--image` (a local `.uf2` path, not a bare tag - the same correction
[0081](0081-waveshare-rp2040-one-board.md) documented for `waveshare_rp2040_one.py`, reused here
rather than re-derived):

```
rp2040py micropython --board-spec boards/0xcb_gemini.py:BOARD \
    --image ~/.cache/rp2040py/RPI_PICO-20260406-v1.28.0.uf2
-> statvfs (4096, 4096)
```

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job (same as every other example
  board except the three curated ones already there).
