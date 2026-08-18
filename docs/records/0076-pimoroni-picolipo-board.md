# 0076. Pimoroni Pico LiPo board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from),
  [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec` firmware resolution, and the
  `weactstudio`-style multi-variant pattern this board follows), 0035 (flash-offset derivation),
  0027 (the "3g rule")

## The board

Pimoroni Pico LiPo (https://shop.pimoroni.com/products/pimoroni-pico-lipo) - a Pico-class RP2040
board with onboard LiPo charging, shipped in **two flash-size variants**: 4 MiB (default) and 16
MiB. Both firmware families exist for both variants (MicroPython via a `BOARD_VARIANT` cmake
mechanism, CircuitPython as two entirely separate board ids).

## What upstream actually says

- MicroPython, `ports/rp2/boards/PIMORONI_PICOLIPO/`: `mpconfigvariant.cmake`
  (`set(PICO_BOARD "pimoroni_picolipo_4mb")`, the default) and `mpconfigvariant_FLASH_16M.cmake`
  (`set(PICO_BOARD "pimoroni_picolipo_16mb")`), `pins.csv` (`LED,GPIO25` / `BOOT,GPIO23` /
  `VBUS_SENSE,GPIO24` / `BAT_SENSE,GPIO29`), `mpconfigboard.h` (USB VID `0x2E8A`/PID `0x1002`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 * 1024 * 1024))` - a formula, same
  shape [0075](0075-nullbits-bit-c-pro-board.md) already saw). The pico-sdk board headers
  (`pimoroni_picolipo_4mb.h`/`_16mb.h`): `PICOLIPO_USER_SW_PIN 23`, `PICOLIPO_VBUS_DETECT_PIN 24`,
  `PICOLIPO_BAT_SENSE_PIN 29`, `PICO_DEFAULT_LED_PIN 25` (no inversion macro - active-high), an
  explicit `// no PICO_DEFAULT_WS2812_PIN` comment, and `PICO_FLASH_SIZE_BYTES` 4/16 MiB
  respectively - otherwise byte-for-byte identical between variants.
- CircuitPython, `ports/raspberrypi/boards/pimoroni_picolipo_{4,16}mb/`: `pins.c` agrees on every
  pin (`USER_SW` -> GPIO23, `VBUS_DETECT` -> GPIO24, `LED` -> GPIO25, `BAT_SENSE` -> GPIO29, no
  `NEOPIXEL`). `mpconfigboard.mk`: USB PID differs per variant (`0x1002`/`0x1003` - two distinct
  USB device identities), `EXTERNAL_FLASH_DEVICES` `"W25Q32JVxQ"` (32 Mbit = 4 MiB) /
  `"W25Q128JVxQ"` (128 Mbit = 16 MiB), agreeing with the pico-sdk headers. No board-specific
  `link.ld` for either variant.

Flash geometry, MicroPython: both variants reserve exactly 1 MiB for firmware code, so
`fs_start = 0x100000` for both - only `fs_blockcount` changes with the real flash size (4 MiB ->
768 blocks, 16 MiB -> 3840 blocks) - confirmed by live boot for both. CircuitPython's own start is
the generic `0x100000`/`fs_blockcount=512` convention for both.

## `USER_SW` (GPIO23) is not modelled - no schematic found, and marketing text alone isn't enough

Pimoroni's own product page names GPIO23 "Switch for basic input (doubles up as DFU select on
boot)" - but neither firmware port's source states a pull direction, and no vendor schematic was
found (`pimoroni/pico-lipo`, the closest available repo, is a firmware repo - board definitions
only, no KiCad/EAGLE design files). The "DFU select" phrasing raises a real possibility that this
switch is diode-coupled into the real BOOTSEL pad the way
[0070](0070-adafruit-itsybitsy-rp2040-board.md)/[0071](0071-adafruit-qtpy-rp2040-board.md)'s BOOT
buttons are - but per the 3g rule, "probably the same shape as the Adafruit boards" is exactly the
kind of inference not to make without a real source. Left unmodelled rather than guessed - a real
schematic would settle it, the same way one did for ItsyBitsy/QT Py.

## What was built

`boards/pimoroni_picolipo.py` - a single flat file (not a directory - see below) declaring two
`BoardSpec`s, `BOARD` (4 MiB, default) and `BOARD_16MB`, each with both firmware families. Extras
identical between variants: `LEDMock(gpio=25)` + `BootselButton`. Not modelled: `USER_SW` (see
above), RESET/power button (pulls RUN, not a GPIO - 0057), the MCP73831 LiPo charger and its
charge-status LED, the STEMMA QT/Qwiic connector (electrically just `I2C(0)`), USB-C. Firmware
histories fetched via `scripts/fetch_firmware.py list` (with `--page PIMORONI_PICOLIPO` for
MicroPython's shared-page variant naming, same mechanism `weactstudio` already needed) and
verified byte-for-byte against the fetched JSON for all four combinations (2 variants × 2
families) - 11 MicroPython releases per variant, 152/151 CircuitPython releases.

## A flat file, not a directory - and why the ones before it were

This is the first board built after re-examining the `boards/` directory convention: 0059's own
text already says "`my_board.py` - a single file is still fine," and this project's module-layout
rule (no device implementation belongs inside `boards/` unless it is genuinely unique to that one
board and not meant to be shared) means a directory was never structurally required for a board
using only generic, already-shared devices (`LEDMock`, `BootselButton`, `KeyMock`, `Ws2812`) - which
is every board built so far. The seven boards added earlier this session
([0068](0068-waveshare-rp2040-zero-board.md)-[0071](0071-adafruit-qtpy-rp2040-board.md),
[0073](0073-garatronic-pybstick26-rp2040-board.md)-[0075](0075-nullbits-bit-c-pro-board.md)) were
retroactively flattened from `boards/<name>/__init__.py` to `boards/<name>.py` alongside this one,
each re-verified to still live-boot identically from its new path before the old directory was
removed. `weactstudio`, `vcc_gnd_yd_rp2040` and `waveshare_rp2040_lcd_0_96` (predating this
session) keep their directories - not touched, since they are cited by name as canonical examples
throughout the skill and reference doc, and retitling them would ripple further than this record's
own scope.

The `.claude/skills/external-devices-and-boards/SKILL.md` text that motivated the original
directory-per-board habit also overstated its own citation: it read "never nest a new device
inside `boards/`" as an absolute rule, where [0059](0059-boardspec-firmware-resolution.md)'s own
promotion-checklist item 4 only requires devices to live in `rp2040py.external` for a board
*graduating* into `boards.BOARDS` (real `--board` support). Corrected in the skill file itself:
a device genuinely unique to one board and not meant to be shared may live under
`boards/<slug>/devices/`, which then simply makes that board ineligible for promotion without
first moving the device out.

## Live-boot verification

MicroPython, both variants:

```
BOARD:      statvfs (4096, 4096, 768, 766, 766, 0, 0, 0, 0, 255)
BOARD_16MB: statvfs (4096, 4096, 3840, 3838, 3838, 0, 0, 0, 0, 255)
```

768/3840 blocks confirm the flash-layout derivation for each variant exactly. CircuitPython, both
variants: `board.board_id == "pimoroni_picolipo_4mb"` / `"pimoroni_picolipo_16mb"` respectively.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- `USER_SW`'s real wiring stays an open gap pending a real schematic.
- The other pending flat-file conversions this record's own retroactive pass did *not* touch
  (`weactstudio`/`vcc_gnd_yd_rp2040`/`waveshare_rp2040_lcd_0_96`) - deliberately out of scope, not
  forgotten.
