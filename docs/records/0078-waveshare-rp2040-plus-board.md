# 0078. Waveshare RP2040-Plus board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from),
  [0076](0076-pimoroni-picolipo-board.md) (the flat-file, two-flash-variant pattern this board
  follows, and numerically identical flash geometry - same chip sizes, same 1 MiB reservation),
  [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec` firmware resolution), 0035
  (flash-offset derivation), 0027 (the "3g rule")

## The board

Waveshare RP2040-Plus (https://www.waveshare.com/rp2040-plus.htm) - a Pico-class RP2040 board with
onboard LiPo battery charging, shipped in **two flash-size variants**: 4 MiB (default) and 16 MiB.
Both firmware families exist for both variants (MicroPython via a `BOARD_VARIANT` cmake mechanism,
CircuitPython as two entirely separate board ids).

## What upstream actually says

- MicroPython, `ports/rp2/boards/WAVESHARE_RP2040_PLUS/`: `mpconfigvariant.cmake`
  (`set(PICO_BOARD "waveshare_rp2040_plus_4mb")`, the default) and
  `mpconfigvariant_FLASH_16M.cmake` (`set(PICO_BOARD "waveshare_rp2040_plus_16mb")`),
  `mpconfigboard.h` (USB VID `0x2E8A`/PID `0x1020`, shared by both variants -
  [0076](0076-pimoroni-picolipo-board.md)/[0077](0077-pimoroni-tiny2040-board.md) both differed
  their two variants by USB identity, this one doesn't at all;
  `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 * 1024 * 1024))`, the same formula
  shape both of those already used). **No `pins.csv` exists for this board** - the first board this
  project has added where that file is simply absent, meaning it relies entirely on the pico-sdk
  board header's own pin defaults. The pico-sdk board headers
  (`waveshare_rp2040_plus_4mb.h`/`_16mb.h`): `PICO_DEFAULT_LED_PIN 25` (no inversion macro -
  active-high), an explicit `// no PICO_DEFAULT_WS2812_PIN` comment, `PICO_SMPS_MODE_PIN 23` (an
  output pin driven high to force the onboard switching regulator into low-ripple PWM mode - not a
  discrete component), and `PICO_FLASH_SIZE_BYTES` 4/16 MiB respectively - otherwise byte-for-byte
  identical between variants.
- CircuitPython, `ports/raspberrypi/boards/waveshare_rp2040_plus_{4,16}mb/`: `pins.c` agrees on
  every pin (`LED` -> GPIO25, `SMPS_MODE` -> GPIO23, `VBUS_SENSE` -> GPIO24, `VOLTAGE_MONITOR`/`A3`
  -> GPIO29 - a battery-voltage ADC divider matching `board.json`'s "Battery Charging" feature tag
  - no `NEOPIXEL`, and critically **no `USER_SW`/`BUTTON` entry at all**: unlike
  [0076](0076-pimoroni-picolipo-board.md)/[0077](0077-pimoroni-tiny2040-board.md), this board has no
  pushbutton beyond BOOTSEL. `mpconfigboard.h` declares no `CIRCUITPY_RGB_STATUS_*` macros -
  CircuitPython doesn't drive this LED as its own status indicator, unlike
  `nullbits_bit_c_pro`/`pimoroni_tiny2040`. `mpconfigboard.mk`: USB VID/PID `0x2E8A`/`0x1020` for
  *both* variants (confirming the MicroPython-side identity match), `EXTERNAL_FLASH_DEVICES`
  `"W25Q32JVxQ"` (32 Mbit = 4 MiB) / `"W25Q128JVxQ"` (128 Mbit = 16 MiB), agreeing with the pico-sdk
  headers. No board-specific `link.ld` for either variant.

Flash geometry, MicroPython: both variants reserve exactly 1 MiB for firmware code, so
`fs_start = 0x100000` for both - only `fs_blockcount` changes with the real flash size (4 MiB -> 768
blocks, 16 MiB -> 3840 blocks) - confirmed by live boot for both, numerically identical to
[0076](0076-pimoroni-picolipo-board.md)'s own table since both boards use the same-sized flash
chips with the same 1 MiB reservation. CircuitPython's own start is the generic
`0x100000`/`fs_blockcount=512` convention for both.

## What was built

`boards/waveshare_rp2040_plus.py` - a single flat file declaring two `BoardSpec`s, `BOARD` (4 MiB,
default) and `BOARD_16MB`, each with both firmware families. Extras identical between variants:
`LEDMock(gpio=25)` + `BootselButton` - no `USER_SW` equivalent exists on this board at all, so
unlike the two prior Waveshare/Pimoroni-style boards there is nothing left as an open pull-direction
gap here. Not modelled: `SMPS_MODE`/GPIO23 (an output-only regulator-mode control pin, not a
discrete component with observable behavior), `VBUS_SENSE`/GPIO24 and `VOLTAGE_MONITOR`/GPIO29 (real
ADC inputs, but nothing board-specific beyond the RP2040's own ADC), the RESET/power button (pulls
RUN, not a GPIO - 0057), the onboard LiPo charge circuit, USB-C, the RP2040's own RTC. Firmware
histories fetched via `scripts/fetch_firmware.py list` (with `--page WAVESHARE_RP2040_PLUS` for
MicroPython's shared-page variant naming) and verified byte-for-byte against the fetched JSON for
all four combinations (2 variants × 2 families) - 5 MicroPython releases per variant (this board is
new enough upstream to have only one stable tag, `1.28.0`, plus previews), 71/70 CircuitPython
releases (4 MiB/16 MiB).

## Live-boot verification

MicroPython, both variants:

```
BOARD:      statvfs (4096, 4096, 768, 766, 766, 0, 0, 0, 0, 255)
BOARD_16MB: statvfs (4096, 4096, 3840, 3838, 3838, 0, 0, 0, 0, 255)
```

768/3840 blocks confirm the flash-layout derivation for each variant exactly. CircuitPython, both
variants: `board.board_id == "waveshare_rp2040_plus_4mb"` / `"waveshare_rp2040_plus_16mb"`
respectively, and `board.LED`/`board.SMPS_MODE`/`board.VBUS_SENSE`/`board.VOLTAGE_MONITOR` all
resolve without error (the last repr's as `board.A3`, its first-declared alias in `pins.c` for the
same GPIO29 pin object - confirms the alias, not a mismatch).

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- The remaining addable-now boards [0066](0066-board-support-expansion.md) still lists unchecked
  (`SEEED_XIAO_RP2040`, `SPARKFUN_PROMICRO`) - not picked up here.
