# 0081. Waveshare RP2040-One board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - the
  **first** board taken off its CircuitPython-only list), [0068](0068-waveshare-rp2040-zero-board.md)
  (same vendor, same WS2812-only shape, same GPIO16, half the flash),
  [0062](0062-yd-rp2040-board-and-ws2812.md)/`boards/vcc_gnd_yd_rp2040/` (the one-firmware-family
  pattern this board follows), [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec` firmware
  resolution), 0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

Waveshare RP2040-One (https://circuitpython.org/board/waveshare_rp2040_one/) - a castellated
USB-A-stick RP2040 board whose only onboard indicator is a single WS2812 RGB LED on GPIO16, on a
4 MiB flash part, with all of GPIO0-29 broken out. **CircuitPython-only**: MicroPython ships no
`ports/rp2/boards/` port for it (confirmed absent from a local `v1.28.0` checkout, which carries
only `WAVESHARE_RP2040_LCD_0_96`, `WAVESHARE_RP2040_PLUS`, `WAVESHARE_RP2040_ZERO` and one RP2350
board).

This is the first board added off [0066](0066-board-support-expansion.md)'s **CircuitPython-only**
checklist - every board before it ([0068](0068-waveshare-rp2040-zero-board.md)-[0080](0080-sparkfun-promicro-board.md))
came from the MicroPython-port list, which is now exhausted.

## What upstream actually says

`ports/raspberrypi/boards/waveshare_rp2040_one/`:

- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "Waveshare RP2040-One"`,
  `MICROPY_HW_NEOPIXEL (&pin_GPIO16)` (so the RGB LED is CircuitPython's own *status* indicator),
  `DEFAULT_UART_BUS_TX/RX` GPIO0/1, `DEFAULT_I2C_BUS_SDA/SCL` GPIO4/5, and a SPI trio spelled
  `DEFAULT_SPI_BUS_CK`/`_MOSI`/`_MISO` GPIO6/7/8.
- `mpconfigboard.mk`: USB VID `0x2E8A`/PID `0x103A`, `"RP2040-One"`/`"Waveshare Electronics"`,
  `EXTERNAL_FLASH_DEVICES = "W25Q32JVxQ"` (32 Mbit = 4 MiB - twice
  [0068](0068-waveshare-rp2040-zero-board.md)'s part), `CIRCUITPY__EVE = 1` (an FT800/EVE
  display-driver *module* compiled into the firmware - a build option, not an onboard chip;
  `boards/vcc_gnd_yd_rp2040/` carries the same flag).
- `pins.c`: `GP0`-`GP29` all broken out, `NEOPIXEL` -> GPIO16, **no `LED` and no `BUTTON` entry at
  all**. The only bus object in the table is `board_uart_obj` - despite the I2C/SPI defaults in the
  header, no `board_i2c_obj`/`board_spi_obj` appears, so `board.I2C`/`board.SPI` do not exist on
  this board's firmware. Recorded as source reads it rather than diagnosed, but worth noting in
  passing that the SPI macro is spelled `DEFAULT_SPI_BUS_CK` where CircuitPython's shared code
  elsewhere uses `DEFAULT_SPI_BUS_SCK`. Live boot confirms all four absences directly (below).
- `board.c`: no board-specific init, only the `MP_WEAK supervisor/shared/board.c` defaults.
  `pico-sdk-configboard.h` is the empty stub CircuitPython requires every board to carry. No
  board-specific `link.ld`, so `link-rp2040.ld`'s default `firmware_size = 1020K` applies.

Flash geometry: the generic CircuitPython `fs_start = 1020 KiB + 4 KiB = 0x100000`,
`fs_blockcount = 512`. The 4 MiB part changes nothing in the layout - it only means more of the chip
is genuinely present.

**Waveshare's own wiki and product pages returned HTTP 403 this session**, so nothing beyond the
firmware source is claimed about this board's physical controls. That is a stated limit, not a
silent one: the RESET/BOOT-pushbutton gap below rests on `pins.c`'s own silence plus 0057's
board-independent "RESET pulls RUN, not a GPIO", not on a vendor page nobody here could read.

## A correction this board turned up: `--image` with a bare tag doesn't work on a one-family spec

`boards/vcc_gnd_yd_rp2040/__init__.py`'s docstring documents the MicroPython escape hatch for a
CircuitPython-only board as:

    rp2040py micropython --board-spec boards/vcc_gnd_yd_rp2040/__init__.py:BOARD --image 1.28.0

That form **fails**, verified this session against the YD board file itself, not only the new ones:

```
--board-spec ...:BOARD: This BoardSpec declares no 'micropython' firmware, so there is nothing
to resolve the firmware tag '1.28.0' against - it declares ['circuitpython']
```

A bare tag is resolved *against the spec's own declared families*, so it cannot work on a spec that
declares only the other one. A local `.uf2` **path** does work, and produces exactly what the YD
docstring describes:

```
rp2040py micropython --board-spec boards/waveshare_rp2040_one.py:BOARD \
    --image ~/.cache/rp2040py/RPI_PICO-20260406-v1.28.0.uf2
-> Raspberry Pi Pico with RP2040
-> statvfs (4096, 4096, 352, 350, 350, 0, 0, 0, 0, 255)
```

Both new board files document the path form and say why. **`boards/vcc_gnd_yd_rp2040/`'s own
docstring is left unchanged here** - correcting another board file is a separate change, flagged
rather than folded in.

## What was built

`boards/waveshare_rp2040_one.py` - a single flat file, one `BoardSpec`, one firmware family.
Extras: `Ws2812(gpio=16)` + `BootselButton`, plus the usual `board_with(on_pixels)` closure. Not
modelled: any RESET/BOOT pushbutton beyond BOOTSEL (see above), the USB-A plug, the RP2040's own
RTC. Firmware history fetched via `scripts/fetch_firmware.py list` - 45 CircuitPython releases.

## Live-boot verification

CircuitPython 10.2.1:

```
board_id  waveshare_rp2040_one
statvfs   (512, 512, 2008)
NEOPIXEL  board.GP16
has_LED False  has_BUTTON False  has_SPI False  has_I2C False
```

The last line is the interesting one - it confirms all four `pins.c` absences from the running
firmware rather than from reading the table, including the missing `board.SPI`/`board.I2C` the
header's defaults might otherwise have implied.

The WS2812, measured through a real `Ws2812` attached to the board: CircuitPython drives it as its
own status indicator - **11 frames decoded before any guest code ran** - and a guest
`neopixel_write(pin, bytearray([0xFF, 0x00, 0xAA]))` came back off the wire as `ff 00 aa`.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- `boards/vcc_gnd_yd_rp2040/`'s stale `--image 1.28.0` line is **not** fixed here (see above).
