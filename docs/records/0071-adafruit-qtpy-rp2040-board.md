# 0071. Adafruit QT Py RP2040 board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - fourth
  board picked up, after [0068](0068-waveshare-rp2040-zero-board.md),
  [0069](0069-adafruit-feather-rp2040-board.md) and
  [0070](0070-adafruit-itsybitsy-rp2040-board.md)), [0070](0070-adafruit-itsybitsy-rp2040-board.md)
  (the near-identical BOOT-button-into-BOOTSEL schematic trick, component-for-component the same
  design), [0062](0062-yd-rp2040-board-and-ws2812.md) (the `Ws2812`/`board_with()` pattern), 0035
  (flash-offset derivation), 0027 (the "3g rule")

## The board

Adafruit QT Py RP2040 (https://www.adafruit.com/product/4900) - a very small RP2040 board,
electrically a Pico-class board with the same 8 MiB flash part as
[0069](0069-adafruit-feather-rp2040-board.md)/[0070](0070-adafruit-itsybitsy-rp2040-board.md), no
plain LED at all, a WS2812 RGB NeoPixel on GPIO12 with a genuine power-enable pin on GPIO11, and a
BOOT button on GPIO21 wired the same double-duty way 0070's board's is.

## What upstream actually says

- MicroPython, `ports/rp2/boards/ADAFRUIT_QTPY_RP2040/`: `pins.csv` is **empty** - no plain LED to
  name at all. `mpconfigboard.h` (USB VID `0x239A`/PID `0x80F8`, `MICROPY_HW_FLASH_STORAGE_BYTES
  (7 * 1024 * 1024)`, comments `// NeoPixel data GPIO12, power GPIO11` / `// Boot button GPIO21`).
  The pico-sdk board header explicitly comments out `PICO_DEFAULT_LED_PIN` (`// No normal LED`),
  and confirms `PICO_DEFAULT_WS2812_PIN 12`, `PICO_DEFAULT_WS2812_POWER_PIN 11`,
  `PICO_FLASH_SIZE_BYTES (8 * 1024 * 1024)`.
- CircuitPython, `ports/raspberrypi/boards/adafruit_qtpy_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO12)`, `CIRCUITPY_STATUS_LED_POWER (&pin_GPIO11)`),
  `mpconfigboard.mk` (`EXTERNAL_FLASH_DEVICES = "W25Q64JVxQ"` - 8 MiB), `pins.c` (`NEOPIXEL` ->
  GPIO12, `NEOPIXEL_POWER` -> GPIO11, `BUTTON` -> GPIO21, no `LED` entry). No board-specific
  `link.ld` in either port.

**Adafruit's product page contradicts itself on flash size** - "8 MB SPI FLASH" in the
technical-details list, but "there is 4MB" in prose a few paragraphs earlier. Firmware source
settles it at 8 MiB, agreeing with the technical-details figure. Flash geometry matches
`adafruit_feather_rp2040`/`adafruit_itsybitsy_rp2040` exactly (`fs_start = 0x100000`,
`fs_blockcount = 1792` MicroPython / `512` CircuitPython) - confirmed by live boot.

## Same design as the ItsyBitsy, confirmed component-for-component from Adafruit's own schematic

Both the NeoPixel power pin and the BOOT button turned out to be the *identical* design to
[0070](0070-adafruit-itsybitsy-rp2040-board.md)'s ItsyBitsy, not merely similar:

- **GPIO11 (NeoPixel power)**: real, per both firmware sources (unlike
  `adafruit_feather_rp2040`'s false marketing claim). `Ws2812` has no power-input concept, so
  GPIO11 stays an ordinary, unmodelled GPIO - a stated fidelity gap. Checked directly (not
  inferred): `GPIO11 driven high before/during boot: True`.
- **GPIO21 (BOOT button)**, sourced from Adafruit's own EAGLE schematic
  (`adafruit/Adafruit-QT-Py-RP2040-PCB`, `Adafruit QT Py RP2040.sch`): `SW2` shorts directly to GND
  on one side and to net `USBBOOT` (GPIO21) on the other - no external pull-up, modelled as
  `KeyMock(gpio=21, active_high=False)`, live-boot-verified reading HIGH under an internal
  `PULL_UP`. `D2` (cathode on `USBBOOT`) routes through `R16` (1k) to net `QSPI_CS` - the real
  `GPIO_QSPI_SS` pad `BootselButton` models - identical in every component value to 0070's
  `SW3`/`D3`/`R11`. Not modelled, same reasoning as 0070: an analog current path between two pins,
  which `ExternalDevice`/`GPIOPin` cannot represent.

## What was built

`boards/adafruit_qtpy_rp2040.py` - both firmware families declared, with:

- `Ws2812(gpio=12)`, `KeyMock(gpio=21, active_high=False)`, `BootselButton`. No `LEDMock` - this
  board genuinely has no plain LED.
- `board_with(on_pixels)` - the same closure pattern 0062/0068/0069/0070 established.
- Not modelled: no plain LED (confirmed absent from every source), RESET (0057), the NeoPixel
  power-enable semantics, the diode/BOOTSEL coupling, the STEMMA QT I2C connector (electrically
  just `I2C(1)`), USB-C.

Firmware histories fetched via `scripts/fetch_firmware.py list` and verified byte-for-byte against
the fetched JSON - 19 MicroPython releases, 155 CircuitPython releases.

## Live-boot verification

MicroPython:

```
statvfs (4096, 4096, 1792, 1790, 1790, 0, 0, 0, 0, 255)
pins ok Pin(GPIO12, mode=OUT) Pin(GPIO11, mode=OUT) Pin(GPIO21, mode=IN, pull=PULL_UP)
boot btn released reads 1
```

CircuitPython, via `tests/ws2812_boot_decode.py`'s pattern: `neopixel_write` decoded back as
`ff 00 aa`, 475 clean pulses on GPIO12, 11 status frames decoded during boot alone - matching every
claim in the docstring, checked before writing rather than after (0069/0070's own closing lesson,
now three boards straight without a post-hoc correction needed for the ones actually verified
directly).

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- The diode/BOOTSEL coupling stays unmodelled, same open follow-up 0070 named.
