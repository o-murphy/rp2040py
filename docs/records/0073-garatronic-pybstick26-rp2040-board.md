# 0073. Garatronic/McHobby PYBStick26 RP2040 board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - the
  smallest remaining "addable now" candidate: MicroPython-only, one device, no schematic or second
  firmware family needed), 0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

Garatronic/McHobby PYBStick26 RP2040 (https://shop.mchobby.be/product.php?id_product=2331) - a
Pico-class RP2040 board with a smaller 1 MiB flash part (vs. a Pico's 2 MiB) and a single plain LED
on GPIO23. MicroPython-only: no CircuitPython port exists for this board at all (`gh api` 404s on
`ports/raspberrypi/boards/garatronic_pybstick26_rp2040/`), confirmed before starting rather than
assumed from 0066's own survey note.

## What upstream actually says

- `pins.csv`: `LED,GPIO23` - the file's only entry.
- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "GARATRONIC_PYBSTICK26_RP2040"`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (384 * 1024)` - no USB VID/PID override, so this board uses the
  generic rp2-port default (`0x2E8A`/`0x0005`, `ports/rp2/mpconfigport.h`).
- The pico-sdk board header (`lib/pico-sdk/src/boards/include/boards/
  garatronic_pybstick26_rp2040.h`): `PICO_DEFAULT_LED_PIN 23` (agreeing with `pins.csv`),
  `PICO_FLASH_SIZE_BYTES (1 * 1024 * 1024)` - a smaller flash part than a plain Pico's 2 MiB - and
  an explicit `// no PICO_DEFAULT_WS2812_PIN` comment.

## Another marketing-tag-vs-source discrepancy, same shape as 0069's

`board.json`'s own `features` list includes `"RGB LED"`. Every other source - `pins.csv`,
`mpconfigboard.h`, and the pico-sdk header's explicit "no `PICO_DEFAULT_WS2812_PIN`" comment -
contradicts it: one plain GPIO LED, no addressable/multi-pin RGB anywhere. Same lesson
[0069](0069-adafruit-feather-rp2040-board.md)'s NeoPixel-power-pin claim drew from the opposite
direction (there, a true-sounding claim turned out false on one board and true on a sibling); here
a loose feature tag simply isn't backed by any electrical fact in either independent source
checked. Modelled as `LEDMock`, not `Ws2812`.

## Flash geometry

`fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES = 1 MiB - 384 KiB = 0xa0000`,
`fs_blockcount = 384 KiB / 4 KiB = 96` (0035's derivation) - confirmed by live boot
(`os.statvfs('/')` reporting exactly 96 blocks). Coincidentally the same `fs_start` hex value as
`waveshare_rp2040_zero`'s (`0xa0000`), since both storage splits happen to leave that remainder
despite different total flash sizes (1 MiB here vs. 2 MiB there) - `fs_blockcount` is what actually
differs (96 vs. 352).

## What was built

`boards/garatronic_pybstick26_rp2040/__init__.py` - one firmware family (MicroPython only, no
generic fallback the way `vcc_gnd_yd_rp2040` has for the reverse case), with `LEDMock(gpio=23)` +
`BootselButton`. Not modelled: RESET (pulls RUN, not a GPIO - 0057, same gap every other board file
here documents; McHobby's product page shows a physical RESET button, but with no schematic
available the way `vcc_gnd_yd_rp2040`'s VCC-GND Studio one was, the gap is stated rather than
guessed at). Firmware history fetched via `scripts/fetch_firmware.py list` and verified
byte-for-byte against the fetched JSON - 19 releases back to 1.18.

## Live-boot verification

```
statvfs (4096, 4096, 96, 94, 94, 0, 0, 0, 0, 255)
pin ok Pin(GPIO23, mode=OUT)
```

96 blocks confirms the flash-layout derivation above. No CircuitPython half to verify - this
board genuinely has none.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
