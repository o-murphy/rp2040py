# 0077. Pimoroni Tiny 2040 board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from),
  [0076](0076-pimoroni-picolipo-board.md) (the flat-file, two-flash-variant pattern this board
  follows), [0075](0075-nullbits-bit-c-pro-board.md) (`LEDMock(active_low=True)` ×3 for an RGB LED
  wired as plain GPIOs, not a `Ws2812`), [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec`
  firmware resolution), 0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

Pimoroni Tiny 2040 (https://shop.pimoroni.com/products/tiny-2040) - a postage-stamp RP2040 board
with a user-controllable RGB LED and a single pushbutton, shipped in **two flash-size variants**: 2
MiB (upstream's default, unnamed variant) and 8 MiB (`FLASH_8M`). Both firmware families exist for
both variants (MicroPython via a `BOARD_VARIANT` cmake mechanism, CircuitPython as two entirely
separate board ids).

## What upstream actually says

- MicroPython, `ports/rp2/boards/PIMORONI_TINY2040/`: `board.json`'s `"variants": {"FLASH_8M": "8
  MiB Flash"}` confirms the *unnamed* default variant is the 2 MiB one, not the 8 MiB one the
  product page leads with. `mpconfigvariant.cmake` (`set(PICO_BOARD "pimoroni_tiny2040_2mb")`, the
  default) and `mpconfigvariant_FLASH_8M.cmake` (`set(PICO_BOARD "pimoroni_tiny2040")`),
  `mpconfigboard.h` (USB VID `0x16D0`/PID `0x08C7`, shared by both variants;
  `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 * 1024 * 1024))` - the same formula
  shape [0076](0076-pimoroni-picolipo-board.md) already saw), `pins.csv` (`LED_RED,GPIO18` /
  `LED_GREEN,GPIO19` / `LED_BLUE,GPIO20` / `LED,GPIO19` (green aliased as the generic LED) /
  `BOOT,GPIO23` - `BOOT` here is `USER_SW`, not the QSPI-SS BOOTSEL pad). The pico-sdk board
  headers (`pimoroni_tiny2040.h`/`_2mb.h`): `TINY2040_LED_R_PIN 18`/`_G_PIN 19`/`_B_PIN 20`,
  `TINY2040_USER_SW_PIN 23`, `PICO_DEFAULT_LED_PIN_INVERTED 1` (active-low, identical in both
  headers), an explicit `// no PICO_DEFAULT_WS2812_PIN` comment, and `PICO_FLASH_SIZE_BYTES` 8/2
  MiB respectively - otherwise byte-for-byte identical between variants.
- CircuitPython, `ports/raspberrypi/boards/pimoroni_tiny2040{,_2mb}/`: `pins.c` agrees on every pin
  (`LED_R`/`LED_G`/`LED_B` -> GPIO18/19/20, `USER_SW`/`BUTTON` -> GPIO23, no `NEOPIXEL`).
  `mpconfigboard.h`: `CIRCUITPY_RGB_STATUS_INVERTED_PWM` + `CIRCUITPY_RGB_STATUS_R/_G/_B` ->
  GPIO18/19/20 in both variants - the same "PWM-driven active-low RGB status LED" shape
  [0075](0075-nullbits-bit-c-pro-board.md) already showed, confirming active-low independently of
  the pico-sdk headers. `mpconfigboard.mk`: USB identity differs by variant - the 8 MiB build keeps
  Pimoroni's own `0x16D0`/`0x08C7` (matching MicroPython's), the 2 MiB build instead uses the
  Raspberry Pi Foundation's community VID `0x2E8A`/PID `0x1016` (a genuinely distinct USB device
  identity, unlike `pimoroni_picolipo` where both variants share one VID and differ only in PID).
  `EXTERNAL_FLASH_DEVICES` `"W25Q64JVxQ"` (64 Mbit = 8 MiB) / `"W25Q16JVxQ"` (16 Mbit = 2 MiB),
  agreeing with the pico-sdk headers. No board-specific `link.ld` for either variant.

Flash geometry, MicroPython: both variants reserve exactly 1 MiB for firmware code, so
`fs_start = 0x100000` for both - only `fs_blockcount` changes with the real flash size (2 MiB -> 256
blocks, 8 MiB -> 1792 blocks) - confirmed by live boot for both. CircuitPython's own start is the
generic `0x100000`/`fs_blockcount=512` convention for both.

## `USER_SW` (GPIO23) is not modelled - same gap as Pico LiPo, for the same reason

Pimoroni's own product page names this "Switch for basic input (doubles up as DFU select on
boot)" - the identical phrasing [0076](0076-pimoroni-picolipo-board.md) already found insufficient
on its own. Neither firmware port's source states a pull direction, and a search of Pimoroni's own
C++ SDK (`pimoroni-pico`) for `TINY2040_USER_SW_PIN` usage returned zero hits - nothing there
settles the DFU-select claim's actual electrical mechanism either. Per the 3g rule, left unmodelled
rather than guessed.

## What was built

`boards/pimoroni_tiny2040.py` - a single flat file declaring two `BoardSpec`s, `BOARD` (2 MiB,
upstream's default) and `BOARD_8MB`, each with both firmware families. Extras identical between
variants: `LEDMock(gpio=18, active_low=True)` / `LEDMock(gpio=19, active_low=True)` /
`LEDMock(gpio=20, active_low=True)` (red/green/blue) + `BootselButton` - the same three-`LEDMock`
RGB shape [0075](0075-nullbits-bit-c-pro-board.md) established, not a `Ws2812`. Not modelled:
`USER_SW` (see above), the **RESET** button (`pins.csv` lists no RESET net at all for this board,
so there's nothing board-specific beyond the RUN pin every board here already leaves unmodelled -
0057), USB-C, the RP2040's own RTC. Firmware histories fetched via `scripts/fetch_firmware.py list`
(with `--page PIMORONI_TINY2040` for MicroPython's shared-page variant naming) and verified
byte-for-byte against the fetched JSON for all four combinations (2 variants × 2 families) - 19/11
MicroPython releases per variant (2 MiB/8 MiB), 151/149 CircuitPython releases (2 MiB/8 MiB).

## Live-boot verification

MicroPython, both variants:

```
BOARD:     statvfs (4096, 4096, 256, 254, 254, 0, 0, 0, 0, 255)
BOARD_8MB: statvfs (4096, 4096, 1792, 1790, 1790, 0, 0, 0, 0, 255)
```

256/1792 blocks confirm the flash-layout derivation for each variant exactly. CircuitPython, both
variants: `board.board_id == "pimoroni_tiny2040_2mb"` / `"pimoroni_tiny2040"` respectively, and
`board.LED_R`/`board.LED_G`/`board.LED_B`/`board.USER_SW` all resolve - confirming the pin map
matches `pins.c` for both.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- `USER_SW`'s real wiring stays an open gap pending a real schematic.
- The other addable-now boards [0066](0066-board-support-expansion.md) still lists unchecked
  (`SEEED_XIAO_RP2040`, `SPARKFUN_PROMICRO`, `WAVESHARE_RP2040_PLUS`) - not picked up here.
