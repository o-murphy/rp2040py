# 0080. SparkFun Pro Micro RP2040 board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - and its
  last remaining "addable now, has a MicroPython port" row), [0068](0068-waveshare-rp2040-zero-board.md)
  (the WS2812-only board this one's device list matches exactly),
  [0078](0078-waveshare-rp2040-plus-board.md)/[0076](0076-pimoroni-picolipo-board.md) (the same
  16 MiB flash split, reached as a variant there and as the only configuration here),
  [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec` firmware resolution), 0035
  (flash-offset derivation), 0027 (the "3g rule")

## The board

SparkFun Pro Micro RP2040 (https://www.sparkfun.com/products/18288) - a Pro-Micro-footprint RP2040
board with **no plain LED at all**, a single WS2812 NeoPixel on GPIO25, a Qwiic/STEMMA JST-SH I2C
connector, and the **largest flash part any board in this project has**: 16 MiB, 15 MiB of it
filesystem under MicroPython. Both firmware families exist, under different board ids -
MicroPython's `SPARKFUN_PROMICRO` vs. CircuitPython's `sparkfun_pro_micro_rp2040`.

## What upstream actually says

- MicroPython, `ports/rp2/boards/SPARKFUN_PROMICRO/`: `mpconfigboard.h`
  (`MICROPY_HW_BOARD_NAME "SparkFun Pro Micro RP2040"`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (15 * 1024 * 1024)`, USB VID `0x1B4F`/PID `0x0026`, and its own
  closing comment `// NeoPixel data GPIO25, power not toggleable` - which is what rules out the
  power-enable pin [0079](0079-seeed-xiao-rp2040-board.md)/[0071](0071-adafruit-qtpy-rp2040-board.md)
  both have). Two absences matter and are both derivations, not assumptions:
  - **No `pins.csv`** - the directory holds only `board.json`, `mpconfigboard.cmake` and
    `mpconfigboard.h`. [0078](0078-waveshare-rp2040-plus-board.md) hit the same absence first; every
    pin here therefore comes from the pico-sdk header and CircuitPython's `pins.c`.
  - **No `set(PICO_BOARD ...)`** - `mpconfigboard.cmake` is a bare comment, so
    `ports/rp2/CMakeLists.txt`'s own fallback applies (`if(NOT PICO_BOARD)` ->
    `string(TOLOWER ${MICROPY_BOARD} PICO_BOARD)`, lines 58-60), resolving the board header to
    `sparkfun_promicro`. Derived from the build system, not from the names happening to match.
- The pico-sdk board header `sparkfun_promicro.h`: `PICO_DEFAULT_WS2812_PIN 25` and, immediately
  above it, a *commented-out* `PICO_DEFAULT_LED_PIN 25` block under
  `// The PRO Micro doesn't have a plain LED, but a WS2812`. The absence of a plain LED is stated
  upstream rather than inferred from a missing `#define` - which matters more than usual here,
  because GPIO25 is exactly where a plain Pico puts its ordinary LED: modelling it as an `LEDMock`
  would have been the natural wrong guess. Also `PICO_DEFAULT_I2C_SDA_PIN 16`/`_SCL_PIN 17`
  (`// Default I2C - for the onboard qwiic connector`) and
  `PICO_FLASH_SIZE_BYTES (16 * 1024 * 1024)` (`// board has 16M onboard flash`).
- CircuitPython, `ports/raspberrypi/boards/sparkfun_pro_micro_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO25)`, agreeing with the pico-sdk header; no
  `CIRCUITPY_STATUS_LED_POWER`, agreeing with MicroPython's "power not toggleable" comment),
  `mpconfigboard.mk` (USB VID/PID `0x1B4F`/`0x0026`, byte-identical to MicroPython's;
  `EXTERNAL_FLASH_DEVICES = "W25Q128JVxM"`, 128 Mbit = 16 MiB, agreeing with the pico-sdk header),
  `pins.c` (`NEOPIXEL` -> GPIO25, `STEMMA_I2C` aliased onto the board I2C object for the Qwiic
  connector, and **no `LED` and no `BUTTON` entry at all** - independently confirming both the
  missing plain LED and the missing GPIO pushbutton), `board.c` (no board-specific init). No
  board-specific `link.ld`.

Flash geometry, MicroPython: `fs_start = 16 MiB - 15 MiB = 0x100000`,
`fs_blockcount = 15 MiB / 4 KiB = 3840` - numerically identical to
[0076](0076-pimoroni-picolipo-board.md)'s and [0078](0078-waveshare-rp2040-plus-board.md)'s 16 MiB
variants, which reach the same split from the same-sized chip reserving the same 1 MiB for firmware
code. Unlike both of those, this board has **one** flash configuration, not two. CircuitPython's own
start is the generic `0x100000`/`fs_blockcount = 512`.

## What was built

`boards/sparkfun_promicro.py` - a single flat file, one `BoardSpec`, both firmware families.
Extras: `Ws2812(gpio=25)` + `BootselButton`, plus the usual `board_with(on_pixels)` closure - the
exact device list [0068](0068-waveshare-rp2040-zero-board.md)'s RP2040-Zero has, on a very
different flash part. File named after MicroPython's board id (case-normalized), which is also the
pico-sdk board name; CircuitPython's differing `sparkfun_pro_micro_rp2040` is cited in the docstring
next to every number it contributed.

Not modelled: the RESET button (pulls RUN, not a GPIO - 0057; SparkFun's board carries BOOT and
RESET, but BOOT is the QSPI-SS pad `BootselButton` already models and neither port declares any GPIO
pushbutton), the Qwiic/STEMMA JST-SH connector (a bare I2C breakout - no fixed onboard chip behind
it, so there is nothing to emulate), USB-C, the RP2040's own RTC.

Firmware histories fetched via `scripts/fetch_firmware.py list` - 19 MicroPython releases (back to
`1.18`, a much longer history than the recently-added boards) and 155 CircuitPython releases.

## Live-boot verification

MicroPython 1.28.0:

```
BOARD    SparkFun Pro Micro RP2040 with RP2040
statvfs  (4096, 4096, 3840, 3838, 3838, 0, 0, 0, 0, 255)
```

3840 blocks confirm the 15 MiB split exactly. CircuitPython 10.2.1:
`board.board_id == "sparkfun_pro_micro_rp2040"`, `os.statvfs('/')[:3] == (512, 512, 2008)`.

The NeoPixel, measured through a real `Ws2812` attached to the board: CircuitPython drives it as its
own status indicator - **11 frames decoded before any guest code ran** - and a guest
`neopixel_write(pin, bytearray([0xFF, 0x00, 0xAA]))` came back off the wire as `ff 00 aa`.
MicroPython: 0 frames at boot, as expected for a build that declares no default.

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- With this board, [0066](0066-board-support-expansion.md)'s "addable now, has a MicroPython port"
  checklist is **fully checked off** - 12 of 12. What remains on that survey is the CircuitPython-only
  addable list (37 boards, none started) and everything gated behind a missing `ExternalDevice`.
