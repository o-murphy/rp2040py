# 0069. Adafruit Feather RP2040 board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - second
  board picked up, after [0068](0068-waveshare-rp2040-zero-board.md)'s Waveshare RP2040-Zero),
  [0062](0062-yd-rp2040-board-and-ws2812.md) (the `Ws2812`/`board_with()` pattern this board file
  follows), [0059](0059-boardspec-firmware-resolution.md) (`BoardSpec` firmware resolution), 0035
  (flash-offset derivation), 0027 (the "3g rule")

## The board

Adafruit Feather RP2040 (https://www.adafruit.com/product/4884) - a Feather-form-factor RP2040
board, electrically a Pico-class board with a bigger flash part (8 MiB vs. a Pico's 2 MiB) and two
onboard indicators: a plain red LED on GPIO13 and a WS2812 RGB NeoPixel on GPIO16.
[0066](0066-board-support-expansion.md)'s survey placed it in the "addable now" list - zero new
`ExternalDevice`s needed, since both `LEDMock` and `Ws2812` already exist.

## What upstream actually says

Every number below is cross-checked against two independent ports at a local checkout's current
tags (0027's "3g rule"):

- MicroPython, `ports/rp2/boards/ADAFRUIT_FEATHER_RP2040/`: `pins.csv` (`LED,GPIO13` - the file's
  only entry), `mpconfigboard.h` (`MICROPY_HW_BOARD_NAME "Adafruit Feather RP2040"`, USB VID
  `0x239A`/PID `0x80F2`, `MICROPY_HW_FLASH_STORAGE_BYTES (7 * 1024 * 1024)`, and its own comments
  `// NeoPixel GPIO16, power not toggleable` / `// Red user LED GPIO13`), and the pico-sdk board
  header `lib/pico-sdk/src/boards/include/boards/adafruit_feather_rp2040.h`
  (`PICO_DEFAULT_LED_PIN 13`, `PICO_DEFAULT_WS2812_PIN 16`, `PICO_FLASH_SIZE_BYTES (8 * 1024 *
  1024)`, no board-specific `link.ld`/firmware-size override).
- CircuitPython, `ports/raspberrypi/boards/adafruit_feather_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO16)`, agreeing with MicroPython's pin), `mpconfigboard.mk` (same
  USB VID/PID, `EXTERNAL_FLASH_DEVICES = "GD25Q64C,W25Q64JVxQ"` - both 64 Mbit = 8 MiB, agreeing
  with the pico-sdk header), and `pins.c` (`LED`/`D13` -> GPIO13, `NEOPIXEL` -> GPIO16, `BUTTON`/
  `BOOT` -> GPIO4 - a second, non-BOOTSEL button, not modelled here - see below). No board-specific
  `link.ld` (confirmed absent from the directory listing), so `ports/raspberrypi/link-rp2040.ld`'s
  default `firmware_size = 1020K` applies, same derivation every other board file in this project
  already documents.

Flash geometry, MicroPython: `fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES = 8
MiB - 7 MiB = 0x100000`, `fs_blockcount = 7 MiB / 4 KiB = 1792` (0035's derivation, same shape
`boards/weactstudio/` already uses per flash variant) - confirmed by live boot (`os.statvfs('/')`
reporting exactly 1792 blocks). CircuitPython's own start is the generic `firmware_size +
CIRCUITPY_INTERNAL_NVM_SIZE = 0x100000` (the same numeric value as the MicroPython start,
coincidentally - the two derivations are unrelated), `fs_blockcount = 512` following this
project's existing CircuitPython convention.

## Marketing copy vs. firmware source: the NeoPixel power pin

Adafruit's own product page for this board claims "RGB NeoPixel with power pin on GPIO so you can
depower it for low power usages." **This is contradicted by both firmware sources and is not
modelled.** Neither `mpconfigboard.h` (MicroPython's own comment states "power not toggleable" in
so many words) nor the pico-sdk board header defines a `PICO_DEFAULT_WS2812_POWER_PIN` for this
board at all - contrast with `adafruit_itsybitsy_rp2040.h`, a sibling board that *does* define one.
CircuitPython's `pins.c`/`mpconfigboard.h` agree: no `NEOPIXEL_POWER` pin exported. Per the 3g
rule, firmware's own build configuration - checked twice, independently, across both ports -
outweighs marketing copy; this may describe a later hardware revision, a sibling product, or
simply be wrong, but it is not what either upstream port was built against.

This is a sharper instance of the same principle 0068 hit from the other direction (a docstring
guess corrected by live-boot evidence): here the correct call was made *before* writing the
docstring, by preferring firmware source over marketing text in the first place, rather than
needing a later correction.

## What was built

`boards/adafruit_feather_rp2040/__init__.py` - both firmware families declared, with:

- `LEDMock(gpio=13)` - the plain red LED.
- `Ws2812(gpio=16)` - the RGB NeoPixel, reusing 0062's device unchanged.
- `BootselButton` - wired identically on every RP2040 board that boots from QSPI flash (0050/0051).
- `board_with(on_pixels)` - the same closure pattern 0062/0068 established.
- Not modelled: the second, non-BOOTSEL `BUTTON`/`BOOT` on GPIO4 CircuitPython's `pins.c` names -
  a real button this board has, but neither port's source states a pull direction, so per the 3g
  rule it stays an open gap rather than a guess (unlike `vcc_gnd_yd_rp2040`'s USRKEY, where a
  vendor schematic settled the same question). Also not modelled: RESET (pulls RUN, not a GPIO -
  0057), the STEMMA QT I2C connector (electrically just `I2C(1)`, nothing board-specific), the
  LiPo charging circuit and its status LED (analog, not RP2040-visible), and USB-C.

Firmware histories for both families fetched via `scripts/fetch_firmware.py list` and verified
byte-for-byte against the fetched JSON (not hand-typed, after 0068 caught a transcription gap the
first time this pattern was used) - 19 MicroPython releases back to 1.18, and 160 CircuitPython
releases.

## Live-boot verification

MicroPython, `--board-spec boards/adafruit_feather_rp2040/__init__.py:BOARD`:

```
statvfs (4096, 4096, 1792, 1790, 1790, 0, 0, 0, 0, 255)
pins ok Pin(GPIO13, mode=OUT) Pin(GPIO16, mode=OUT)
```

1792 blocks confirms the flash-layout derivation above.

CircuitPython, via `tests/ws2812_boot_decode.py`'s own pattern, pointed at this board file: a guest
`neopixel_write(board.NEOPIXEL, bytearray([0xFF, 0x00, 0xAA]))` decoded back as `ff 00 aa` off the
wire, with 475 bit-length pulses on GPIO16 and none in the "two populations overlap" failure mode
0063 originally fixed.

**Same surprise as 0068, caught the same way.** The docstring's first draft claimed this board's
CircuitPython build does not drive the NeoPixel as a boot-time status indicator, reasoning from
the absence of an explicit status-LED line in `board.c` rather than from a live boot. The live
boot showed 11 frames decoded before any guest code ran - the same behavior as
`vcc_gnd_yd_rp2040`/`waveshare_rp2040_zero` - so the docstring was corrected before the record was
written, not after. This is now the second time this exact mistake pattern has occurred
(reasoning from `board.c`'s absence of code rather than from measurement); worth remembering for
the next board in this survey that CircuitPython's NeoPixel-as-status-indicator behavior appears
to be a supervisor-level default whenever `MICROPY_HW_NEOPIXEL` is declared, not something
`board.c` opts into explicitly - so the safe assumption going forward is "driven at boot unless
live-boot evidence says otherwise," not the reverse.

## Not done here

- Not promoted to `boards.BOARDS` (real `--board` support) - clears neither item 2
  (`firmware_specs.json` entry) nor item 5 (a named maintainer) of 0059's promotion checklist.
  Stays an example under `boards/`, the intended steady state for most new boards.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job - live-boot verification was
  done manually, once, not wired into permanent CI.
- GPIO4's second button stays undocumented pending a vendor schematic (same gap `vcc_gnd_yd_rp2040`
  closed with one) - a real follow-up, not forgotten.
