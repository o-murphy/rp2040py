# 0070. Adafruit ItsyBitsy RP2040 board

- Status: **Implemented (2026-08-18).**
- Conceived: 2026-08-18
- Related: [0066](0066-board-support-expansion.md) (the survey this board was picked from - third
  board picked up, after [0068](0068-waveshare-rp2040-zero-board.md) and
  [0069](0069-adafruit-feather-rp2040-board.md)), [0062](0062-yd-rp2040-board-and-ws2812.md) (the
  `Ws2812`/`board_with()` pattern, and the USRKEY schematic precedent this board's BOOT button
  follows), [0050](0050-qspi-pad-reset-values.md)/[0051](0051-bootsel-button.md) (`BootselButton`,
  and the real `GPIO_QSPI_SS` pad this board's BOOT button is diode-coupled into), 0035
  (flash-offset derivation), 0027 (the "3g rule")

## The board

Adafruit ItsyBitsy RP2040 (https://www.adafruit.com/product/4888) - a small-form-factor RP2040
board, electrically a Pico-class board with the same 8 MiB flash part as
[0069](0069-adafruit-feather-rp2040-board.md)'s Feather RP2040 and three onboard indicators: a
plain red LED on GPIO11, a WS2812 RGB NeoPixel on GPIO17 with a genuine power-enable pin on GPIO16,
and a second, non-BOOTSEL BOOT button on GPIO13.

## What upstream actually says

Every number below is cross-checked against two independent firmware ports at a local checkout's
current tags, plus Adafruit's own published schematic where firmware source alone left a question
open (0027's "3g rule"):

- MicroPython, `ports/rp2/boards/ADAFRUIT_ITSYBITSY_RP2040/`: `pins.csv` (`LED,GPIO11`),
  `mpconfigboard.h` (USB VID `0x239A`/PID `0x80FE`, `MICROPY_HW_FLASH_STORAGE_BYTES (7 * 1024 *
  1024)`, and its own comments `// NeoPixel data GPIO17, power GPIO16` / `// Red user LED GPIO11`
  / `// Boot button GPIO13`), and the pico-sdk board header
  (`PICO_DEFAULT_LED_PIN 11`, `PICO_DEFAULT_WS2812_PIN 17`, `PICO_DEFAULT_WS2812_POWER_PIN 16`,
  `PICO_FLASH_SIZE_BYTES (8 * 1024 * 1024)`).
- CircuitPython, `ports/raspberrypi/boards/adafruit_itsybitsy_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO17)`, `CIRCUITPY_STATUS_LED_POWER (&pin_GPIO16)`),
  `mpconfigboard.mk` (`EXTERNAL_FLASH_DEVICES = "W25Q64JVxQ"` - 64 Mbit = 8 MiB), `pins.c`
  (`LED`/`D13` -> GPIO11, `NEOPIXEL` -> GPIO17, `NEOPIXEL_POWER` -> GPIO16, `BUTTON` -> GPIO13). No
  board-specific `link.ld` in either port.

Flash geometry matches `adafruit_feather_rp2040` exactly (`fs_start = 0x100000`,
`fs_blockcount = 1792` for MicroPython, `fs_blockcount = 512` for CircuitPython) since both boards
share the same 8 MiB flash / 7 MiB storage split - confirmed by live boot
(`os.statvfs('/')` reporting exactly 1792 blocks).

## A genuine NeoPixel power pin, this time - marketing copy was right here, wrong on the Feather

0069 documented Adafruit's product-page claim of a NeoPixel power pin as **false** for the Feather
RP2040 (contradicted by both firmware sources). This board's product page makes the same claim,
and here it is **true**: `PICO_DEFAULT_WS2812_POWER_PIN 16` and MicroPython's own comment both
confirm it, and CircuitPython's `CIRCUITPY_STATUS_LED_POWER`/`NEOPIXEL_POWER` agree. The lesson from
both boards together: marketing copy is evidence to check, not evidence to trust *or* distrust
wholesale - the two boards' identical claim resolved oppositely, which is exactly why the 3g rule
insists on checking every board rather than pattern-matching from a sibling.

`Ws2812` (`rp2040py.external.ws2812`) has no concept of a power-enable input at all - it decodes
whatever waveform arrives on its data pin regardless of any other pin's state. GPIO16 is an
ordinary GPIO in this emulation, not wired to anything that would refuse to "light" without it - a
stated fidelity gap, not a silent one. Live boot confirms CircuitPython does drive GPIO16 high
before any guest code runs (checked directly, by attaching a GPIO listener rather than inferring it
from the macro's existence): `GPIO16 driven high before/during boot: True`.

## GPIO13's BOOT button: sourced from Adafruit's own EAGLE schematic, not firmware

Neither firmware port's source states GPIO13's pull/polarity - the same shape of open question 0069
left for the Feather's GPIO4 button. Here it was resolved, from Adafruit's published schematic
(`adafruit/Adafruit-ItsyBitsy-RP2040-PCB`, `Adafruit ItsyBitsy RP2040.sch`):

- **`SW3`** (the physical BOOT button) shorts directly to GND on one side and to net `USBBOOT`
  (GPIO13) on the other - no series resistor, no external pull-up, the same shape
  `vcc_gnd_yd_rp2040`'s USRKEY button has (0062's own schematic addendum). Modelled as
  `KeyMock(gpio=13, active_high=False)`; live-boot-verified reading HIGH (released) under an
  internal `PULL_UP` firmware itself configures.
- **The same switch is also diode-coupled into the real BOOTSEL path** - `D3` (cathode on the
  `USBBOOT` net) routes through `R11` (1k) to net `QSPI_CS`, the actual `GPIO_QSPI_SS` pad
  `BootselButton` already models (0050/0051). Pressing `SW3` pulls GPIO13 low *and* weakly pulls
  the real BOOTSEL pad toward GND - one physical switch doing double duty. **Not modelled**: this
  is an analog current path *between* two pins, which `ExternalDevice`/`GPIOPin` has no
  representation for (every existing device drives one pin from its own state, none routes current
  between two pins). Pressing the emulated GPIO13 button does not additionally trigger
  `BootselButton`; the two stay independent here, a stated gap rather than a silent one.

## What was built

`boards/adafruit_itsybitsy_rp2040.py` - both firmware families declared, with:

- `LEDMock(gpio=11)`, `Ws2812(gpio=17)`, `KeyMock(gpio=13, active_high=False)`, `BootselButton`.
- `board_with(on_pixels)` - the same closure pattern 0062/0068/0069 established.
- Not modelled: RESET (0057), the diode/BOOTSEL coupling and power-gating semantics above, the
  Vhigh pin and USB/battery auto-switching (analog), broken-out SWD, USB Micro-B.

Firmware histories fetched via `scripts/fetch_firmware.py list` and verified byte-for-byte against
the fetched JSON - 19 MicroPython releases, 155 CircuitPython releases.

## Live-boot verification

MicroPython:

```
statvfs (4096, 4096, 1792, 1790, 1790, 0, 0, 0, 0, 255)
pins ok Pin(GPIO11, mode=OUT) Pin(GPIO17, mode=OUT) Pin(GPIO13, mode=IN, pull=PULL_UP) Pin(GPIO16, mode=OUT)
boot btn released reads 1
```

CircuitPython, via `tests/ws2812_boot_decode.py`'s pattern pointed at this board file: `neopixel_write`
decoded back as `ff 00 aa`, 475 clean pulses on GPIO17, 11 status frames decoded during boot alone
- and, checked directly with a separate GPIO16 listener rather than inferred, GPIO16 driven high
before/during boot.

Unlike 0068 and 0069, no docstring claim needed correcting after the live boot this time - both the
boot-status-indicator behavior and the GPIO16 power-pin assertion were verified directly before
being written down, applying 0069's own closing lesson ("assume driven at boot unless evidence says
otherwise, and check rather than infer").

## Not done here

- Not promoted to `boards.BOARDS` - stays an example under `boards/`.
- No CI step added to `ci-micropython.yml`'s `test-board-spec` job.
- The diode/BOOTSEL coupling stays unmodelled - would need a new kind of `ExternalDevice` (or a
  board-level mechanism) able to route current between two pins, which nothing in this project's
  device model currently supports; a real follow-up if a board ever needs it for correctness rather
  than completeness.
