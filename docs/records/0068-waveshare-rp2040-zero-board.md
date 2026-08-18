# 0068. Waveshare RP2040-Zero board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - flagged
  by the user as the one to pick up first, the smallest candidate on that list), [0062](0062-yd-rp2040-board-and-ws2812.md)
  (the `Ws2812`/`board_with()` pattern this board file follows), [0059](0059-boardspec-firmware-resolution.md)
  (`BoardSpec` firmware resolution), 0035 (flash-offset derivation), 0027 (the "3g rule")

## The board

Waveshare RP2040-Zero (https://www.waveshare.com/product/rp2040-zero.htm) - a tiny (18x23.5mm)
castellated-module RP2040 board, electrically a Pico-class board with the same 2 MiB flash
geometry as a plain Pico, but with **no plain GPIO25 LED at all**: the only onboard indicator is a
single WS2812 RGB LED on GPIO16. [0066](0066-board-support-expansion.md)'s survey placed it in the
"addable now" list - zero new `ExternalDevice`s needed, since `Ws2812` already exists as of
[0062](0062-yd-rp2040-board-and-ws2812.md) - and named it the smallest candidate on the whole
checklist (one `Ws2812`, nothing else).

## What upstream actually says

Every number below is cross-checked against two independent ports at a local checkout's current
tags (0027's "3g rule"):

- MicroPython, `ports/rp2/boards/WAVESHARE_RP2040_ZERO/`: `board.json` ("RGB LED" feature, MCU
  `rp2040`), `pins.csv` (`NEOPIXEL,GPIO16` - the file's only entry), `mpconfigboard.h`
  (`MICROPY_HW_BOARD_NAME "Waveshare RP2040-Zero"`, USB VID `0x2E8A`/PID `0x101F`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (1408 * 1024)`, no `MICROPY_HW_LED_PIN` at all - confirming no
  plain LED), and the pico-sdk board header `lib/pico-sdk/src/boards/include/boards/
  waveshare_rp2040_zero.h` (`PICO_DEFAULT_WS2812_PIN 16`, `PICO_FLASH_SIZE_BYTES (2 * 1024 *
  1024)`, no board-specific `link.ld`/firmware-size override).
- CircuitPython, `ports/raspberrypi/boards/waveshare_rp2040_zero/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO16)`, agreeing with MicroPython's pin), `mpconfigboard.mk` (same
  USB VID/PID, `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ"` - 16 Mbit = 2 MiB, agreeing with the
  pico-sdk header), and `pins.c` (only `NEOPIXEL` -> GPIO16 beyond the plain `GPx`/UART/analog
  entries - no `LED`, no `BUTTON`). No board-specific `link.ld` (confirmed absent from the
  directory listing), so `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K`
  applies, same derivation `boards/vcc_gnd_yd_rp2040/` and `boards/weactstudio/` already document.

Flash geometry is **identical to a plain Pico**, not merely similar: `PICO_FLASH_SIZE_BYTES` (2
MiB) and `MICROPY_HW_FLASH_STORAGE_BYTES` (1408 KiB) both match `RPI_PICO`'s own values byte for
byte, giving the same `fs_start = 0xa0000`, `fs_blockcount = 352` this project already uses for
`"pico"` (0035's derivation) - confirmed by live boot (`os.statvfs('/')` reporting exactly 352
blocks). CircuitPython's own start is the generic `firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE =
0x100000`, `fs_blockcount = 512` following this project's existing CircuitPython convention (0035:
only the *start* has to be right, since the emulated flash buffer is 16 MiB).

## What was built

`boards/waveshare_rp2040_zero.py` - both firmware families declared (unlike
`vcc_gnd_yd_rp2040`, which has no MicroPython port at all, this board has one for each), with:

- `Ws2812(gpio=16)` - the board's only device, reusing 0062's device unchanged.
- `BootselButton` - wired identically on every RP2040 board that boots from QSPI flash (0050/0051).
- `board_with(on_pixels)` - the same closure pattern 0062 established, since `BoardSpec.extras`
  holds zero-arg factories and nothing else hands a constructed device's callback back to an SDK
  caller.
- Not modelled: **RESET** (pulls RUN, not a GPIO - 0057's gap, same as every other board file
  here) and USB-C/the RP2040's own RTC (not board-specific).

Firmware histories for both families fetched via `scripts/fetch_firmware.py list --family
micropython --slug WAVESHARE_RP2040_ZERO` and `--family circuitpython --slug
waveshare_rp2040_zero`, then verified byte-for-byte against the fetched JSON (not hand-typed) - 5
MicroPython releases (this board's MicroPython port is recent; only 1.28.0 is a stable tag, plus
four 1.29.0 previews) and 128 CircuitPython releases going back to 7.2.0.

## Live-boot verification

MicroPython, `--board-spec boards/waveshare_rp2040_zero.py:BOARD`:

```
statvfs (4096, 4096, 352, 350, 350, 0, 0, 0, 0, 255)
GPIO16 usable Pin(GPIO16, mode=OUT)
```

352 blocks confirms the flash-layout derivation above.

CircuitPython, via `tests/ws2812_boot_decode.py`'s own pattern (its `BOARD_FILE` pointed at this
board instead, since the script itself is `vcc_gnd_yd_rp2040`-specific and not generalized here):
a guest `neopixel_write(board.NEOPIXEL, bytearray([0xFF, 0x00, 0xAA]))` decoded back as `ff 00 aa`
off the wire, with 475 bit-length pulses on GPIO16 and none in the "two populations overlap"
failure mode 0063 originally fixed.

**One surprise, corrected in the board file's docstring before it shipped:** this board's
CircuitPython build *does* drive the NeoPixel as a boot-time status indicator - 11 frames decoded
before any guest code ran - which the docstring's first draft claimed it did not (reasoning from
the absence of an explicit status-LED line in `board.c`, rather than from a live boot). The
corrected docstring now states this as measured, matching `vcc_gnd_yd_rp2040`'s own behavior;
`board_with(on_pixels)` sees pixels with no guest code at all on CircuitPython, same as that board.
MicroPython's build declares no such default and needs guest code to write to `NEOPIXEL`/GPIO16.

## Not done here

- Not promoted to `boards.BOARDS` (real `--board` support) - clears neither item 2
  (`firmware_specs.json` entry) nor item 5 (a named maintainer) of 0059's promotion checklist.
  Stays an example under `boards/`, the intended steady state for most new boards.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job - this record's live-boot
  verification was done manually, once, not wired into permanent CI the way 0059's own boards are.
