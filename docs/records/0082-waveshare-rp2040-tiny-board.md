# 0082. Waveshare RP2040-Tiny board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0081](0081-waveshare-rp2040-one-board.md) (its twin, added in the same pass - same
  vendor, same WS2812 on the same GPIO16, same one-family shape; read that record's `--image`
  correction too, it applies here), [0068](0068-waveshare-rp2040-zero-board.md) (same vendor, same
  2 MiB part), [0066](0066-board-support-expansion.md) (the survey),
  [0059](0059-boardspec-firmware-resolution.md), 0035, 0027 (the "3g rule")

## The board

Waveshare RP2040-Tiny (https://circuitpython.org/board/waveshare_rp2040_tiny/) - a postage-stamp
RP2040 module whose only onboard indicator is a single WS2812 RGB LED on GPIO16, on a 2 MiB flash
part. **CircuitPython-only**, same as [0081](0081-waveshare-rp2040-one-board.md).

Two things make it worth having alongside its twin rather than being a duplicate:

- **The narrowest pin breakout of any board in this project**: `pins.c` declares `GP0`-`GP16` and
  `GP26`-`GP29` only - GPIO17-25 are absent from the table entirely, where
  [0081](0081-waveshare-rp2040-one-board.md)'s RP2040-One exposes all of `GP0`-`GP29`.
- **Its 2 MiB part happens to match a plain Pico's exactly**, which makes the MicroPython fallback
  described below a genuinely correct configuration rather than a compromise - the only
  CircuitPython-only board here where that is true.

## What upstream actually says

`ports/raspberrypi/boards/waveshare_rp2040_tiny/`:

- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "Waveshare RP2040-Tiny"`,
  `MICROPY_HW_NEOPIXEL (&pin_GPIO16)` (the RGB LED is CircuitPython's own status indicator),
  `DEFAULT_UART_BUS_TX/RX` GPIO0/1 - and, unlike
  [0081](0081-waveshare-rp2040-one-board.md)'s board, **no I2C or SPI defaults at all**.
- `mpconfigboard.mk`: USB VID `0x2E8A`/PID `0x1084`, `"RP2040-Tiny"`/`"Waveshare Electronics"`,
  `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ"` (16 Mbit = 2 MiB, the same part
  [0068](0068-waveshare-rp2040-zero-board.md) carries), `CIRCUITPY__EVE = 1` (a compiled-in
  display-driver module, not an onboard chip).
- `pins.c`: `GP0`-`GP16` + `GP26`-`GP29` (plus `A0`-`A3` aliases and `TX`/`RX`), `NEOPIXEL` ->
  GPIO16, **no `LED` and no `BUTTON`**, and `board_uart_obj` as the only bus object - consistent
  with the header declaring no I2C/SPI defaults.
- `board.c`: no board-specific init. `pico-sdk-configboard.h` is the empty required stub. No
  board-specific `link.ld`, so `link-rp2040.ld`'s default `firmware_size = 1020K` applies.
- Both files carry a **2024 copyright by a community contributor (Bill Sideris)** rather than
  Adafruit's own 2021 header - a newer, externally-contributed port than the rest of this project's
  CircuitPython boards. Noted because it sets expectations about how much upstream review the pin
  table has had, not as a criticism of it.

Flash geometry: the generic CircuitPython `fs_start = 0x100000`, `fs_blockcount = 512`.

As with [0081](0081-waveshare-rp2040-one-board.md), **Waveshare's own wiki and product pages
returned HTTP 403 this session**, so nothing beyond firmware source is claimed about this board's
physical controls or its packaging.

## What was built

`boards/waveshare_rp2040_tiny.py` - a single flat file, one `BoardSpec`, one firmware family.
Extras: `Ws2812(gpio=16)` + `BootselButton`, plus `board_with(on_pixels)`. Not modelled: any
RESET/BOOT pushbutton beyond BOOTSEL (`pins.c` declares none, and a RESET control pulls RUN rather
than a GPIO - 0057), the RP2040's own RTC. Firmware history fetched via
`scripts/fetch_firmware.py list` - 63 CircuitPython releases.

GPIO16 is now the third Waveshare board in this project to put its WS2812 there
([0068](0068-waveshare-rp2040-zero-board.md), [0081](0081-waveshare-rp2040-one-board.md), this one).
Each was read from its own source; none was assumed from the others.

## Live-boot verification

CircuitPython 10.2.1:

```
board_id  waveshare_rp2040_tiny
statvfs   (512, 512, 2008)
NEOPIXEL  board.GP16
has_LED False  has_BUTTON False  has_SPI False  has_I2C False
```

The WS2812, measured through a real `Ws2812` attached to the board: **11 frames decoded before any
guest code ran**, and a guest `neopixel_write(pin, bytearray([0xFF, 0x00, 0xAA]))` came back off the
wire as `ff 00 aa`.

MicroPython via the local-path `--image` fallback (see
[0081](0081-waveshare-rp2040-one-board.md)'s correction - a bare tag does *not* work on a
one-family spec):

```
rp2040py micropython --board-spec boards/waveshare_rp2040_tiny.py:BOARD \
    --image ~/.cache/rp2040py/RPI_PICO-20260406-v1.28.0.uf2
-> Raspberry Pi Pico with RP2040
-> statvfs (4096, 4096, 352, 350, 350, 0, 0, 0, 0, 255)
```

352 blocks over a 2 MiB part is **correct for this board**, not a stranded-flash compromise - which
is the claim the "same chip size as a Pico" point above rests on, measured rather than reasoned.
The same command on [0081](0081-waveshare-rp2040-one-board.md)'s 4 MiB RP2040-One gives the same 352
blocks, where it *is* a compromise: half the chip goes unused.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- No MicroPython `firmware` family declared, despite the fallback above being electrically exact -
  upstream genuinely never built an image for this board, and a `firmware` key means "built *for*
  this board" (0062).
