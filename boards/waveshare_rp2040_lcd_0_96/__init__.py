"""BoardSpec definition for the Waveshare RP2040-LCD-0.96 (https://www.waveshare.com/wiki/RP2040-LCD-0.96)
- a Pico-class RP2040 board with 2 MiB flash, USB-C, a battery header, and an onboard 0.96inch
160x80 65K-color IPS panel (ST7735S controller) wired to SPI1. Built as a worked `--board-spec`
example for docs/reference/external-devices-and-boards.md, alongside `boards/weactstudio/` - load
it with e.g.:

    rp2040py micropython --board-spec boards/waveshare_rp2040_lcd_0_96/__init__.py:BOARD
    PYTHONPATH=. rp2040py micropython --circuitpython --board-spec boards.waveshare_rp2040_lcd_0_96:BOARD

(the file-path form works from anywhere, no setup needed, for this single-file board; the dotted
module form needs `PYTHONPATH=.` - same as `python -m`/`-c` already puts the current directory on
`sys.path`.)

**One board, two firmwares.** This file used to exist twice - once under `boards/micropython/
WAVESHARE_RP2040_LCD_0_96/` and once under `boards/circuitpython/waveshare_rp2040_lcd_0_96/` -
with identical `extras` (the same soldered hardware) differing only in which image and which flash
layout they carried. `BoardSpec.firmware` is keyed by firmware family, so both declarations live
here and `--circuitpython` picks between them at run time, exactly as it already picks the FAT12
loader and the post-boot console behavior (docs/records/0059). Nothing about *the board* differed
between those two files, which is why there is now one.

Nothing is downloaded when this module is imported: `firmware` is data, and `rp2040py.boards.
resolve_firmware()` turns it into a concrete image only when something actually boots the board.
`retrieve()` caches under `~/.cache/rp2040py`, so an offline run just needs the `.uf2` dropped
there under the exact filename its URL ends with (or the URL below replaced with a local path -
`fw`'s values accept either).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry (see `boards/__init__.py`, and docs/records/0059's
"Promotion checklist" for what moving one into `boards.BOARDS` would take).

Unlike `weactstudio`, this board is the *interesting* case for external devices, not just for
flash numbers: what makes it this board rather than a plain Pico is the panel soldered to it, so
the `BoardSpec` attaches an `St7735s` (`rp2040py.external.st7735s`) as a fixed extra - a board
you can boot and actually get pixels out of, without the caller wiring anything up. Pass your own
`on_frame` by building a `BoardSpec` from `LCD`/`_EXTRAS` below if you want to see them (see the
`board_with()` helper).

Directory named `waveshare_rp2040_lcd_0_96` after the firmware's own board id, case-normalized -
both firmwares happen to use the same string, MicroPython's `ports/rp2/boards/
WAVESHARE_RP2040_LCD_0_96` in uppercase and CircuitPython's `ports/raspberrypi/boards/
waveshare_rp2040_lcd_0_96` in lowercase.

Every number here is derived from real upstream sources, not guessed (docs/records/0027's "3g
rule", 0049's own repeated emphasis).

MicroPython:

- `ports/rp2/boards/WAVESHARE_RP2040_LCD_0_96/mpconfigboard.cmake`: `PICO_BOARD =
  waveshare_rp2040_lcd_0.96`, `MICROPY_HW_FLASH_STORAGE_BYTES = 1441792` (1408 KiB).
- `src/boards/include/boards/waveshare_rp2040_lcd_0.96.h` (pico-sdk): `PICO_FLASH_SIZE_BYTES =
  2 MiB`, and the LCD's own pin map - `WAVESHARE_LCD_SPI 1`, `WAVESHARE_LCD_DC_PIN 8`,
  `WAVESHARE_LCD_CS_PIN 9`, `WAVESHARE_LCD_SCLK_PIN 10`, `WAVESHARE_LCD_TX_PIN 11`,
  `WAVESHARE_LCD_RST_PIN 12`, `WAVESHARE_LCD_BL_PIN 25`.
- `ports/rp2/boards/WAVESHARE_RP2040_LCD_0_96/mpconfigboard.h`: `MICROPY_HW_SPI1_SCK 10`,
  `MICROPY_HW_SPI1_MOSI 11` - i.e. MicroPython's *default* SPI1 pins on this board are already the
  panel's, so firmware that just says `machine.SPI(1)` is talking to the LCD. (Its
  `MICROPY_HW_SPI1_MISO 8` overlaps the LCD's DC pin; the panel is write-only here, and the vendor
  driver passes `miso=None`, so nothing reads it.)

    PICO_FLASH_SIZE_BYTES            2 MiB   (0x200000)
    MICROPY_HW_FLASH_STORAGE_BYTES   1408 KiB (0x160000)
    fs_start  = 0x200000 - 0x160000 = 0xa0000
    fs_blockcount = 0x160000 / 4096 = 352

Only one flash variant exists upstream (unlike weactstudio's four), so there is one `BOARD`.

CircuitPython:

- `ports/raspberrypi/mpconfigport.h`: `CIRCUITPY_FIRMWARE_SIZE` defaults to `1020 * 1024`,
  `CIRCUITPY_INTERNAL_NVM_SIZE` is `4 * 1024`, and
  `CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR = CIRCUITPY_FIRMWARE_SIZE + CIRCUITPY_INTERNAL_NVM_SIZE`.
- `ports/raspberrypi/link-rp2040.ld`: `firmware_size = DEFINED(firmware_size) ? firmware_size :
  1020K`, and this board ships **no** `link.ld`/`firmware_size` override of its own (confirmed:
  `boards/waveshare_rp2040_lcd_0_96/link.ld` is a 404 upstream, and its `mpconfigboard.mk` sets
  only USB ids, `CHIP_VARIANT`/`CHIP_FAMILY`, `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ"` and
  `CIRCUITPY__EVE`), so the default applies:

    fs_start = 1020 KiB + 4 KiB = 0x100000

  i.e. the same offset as plain `pico` under CircuitPython - unlike `pico_w`, which *does* override
  `firmware_size` to 1532K and lands at `0x180000` (docs/records/0035's own CircuitPython audit).

  `fs_blockcount = 512` follows this project's existing CircuitPython entries rather than the
  board's real remaining flash: only the *start* address has to be right, since rp2040py's
  emulated flash buffer is 16 MiB and a generously-sized region past the start collides with
  nothing (0035's reasoning, unchanged here).

`fs_blocksize=4096` is the RP2040's flash sector-erase granularity for both, not firmware-specific
(same as every other board this project tracks - docs/records/0049's "Design update" section).

Onboard extras - one set, since it is one piece of hardware:

- The panel: `St7735s()` - its constructor defaults are this board's wiring, since that is where
  they were taken from (see `rp2040py/external/st7735s.py`). CircuitPython drives it through the
  *same* pins from `board.c`'s `display_init()` (SPI1 CLK=GPIO10, MOSI=GPIO11, DC=GPIO8, CS=GPIO9,
  RST=GPIO12, backlight GPIO25) at 40 MHz.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050).
- The LCD backlight (GPIO25): reuses `LEDMock`, which tracks "is firmware driving this pin high".
  That is honest for on/off control but *not* a brightness model - the vendor driver dims the
  backlight with `PWM(Pin(25))`, and CircuitPython drives it as a real 50 kHz PWM at
  `brightness = 1.0f`; a duty cycle is not something a GPIO-level listener can report. Named
  `BACKLIGHT` below rather than "LED" because this board has no user LED at all (the pico-sdk
  header defines no `PICO_DEFAULT_LED_PIN`, and no `PICO_DEFAULT_WS2812_PIN` either - "no
  PICO_DEFAULT_WS2812_PIN" is an explicit comment in it).
- Not modelled: **the RESET button**, the MP28164 buck-boost converter and the battery
  charge/discharge header (no RP2040-visible interface to emulate - `PICO_SMPS_MODE_PIN 23` just
  forces PWM mode), and the USB-C connector (indistinguishable from any other USB port at this
  level).

The RESET button is the interesting omission of those, so it is written down rather than left
silent: unlike BOOTSEL (which shorts `GPIO_QSPI_SS`, an ordinary pad an `ExternalDevice` can drive
- 0050/0051), RESET pulls the **RUN** pin low, and RUN is not a GPIO at all. Nothing in this
emulator models it: the one live-reset path that exists is `BaseDevice._on_watchdog_trigger()`
(what a guest's own `machine.reset()` reaches through the watchdog's TRIGGER bit), and it is a
*device*-level sequence - `mcu.reset(preserve_flash=True)`, `core.pc = FLASH_START_ADDRESS`, **and**
`cdc.reset()` - whose USB half an `ExternalDevice` cannot reach, since it only ever gets the
`RP2040` and nothing hangs the `USBCDC` off it. A faithful `ResetButton` therefore needs a new
reset hook on `RP2040` (the shape `RPWatchdog.on_watchdog_trigger` already uses) - designed, with
its alternatives and open semantics, in docs/records/0057, and deliberately not implemented yet.
Until then: `machine.reset()` from guest code is the working equivalent.

**What makes the CircuitPython side interesting beyond "the same board again": CircuitPython
initialises the display itself, at boot.** `board_init()` -> `display_init()` builds a
`busdisplay` with `auto_refresh=true` at 60 fps, so simply booting this `BoardSpec` under
`--circuitpython` drives the emulated ST7735S with no guest code at all - and it does so through a
*different* orientation than the MicroPython vendor driver: `MADCTL = 0xC8` (MY|MX, no MV) with
`colstart=26, rowstart=1` and displayio `rotation=90`, where the MicroPython driver uses
`MADCTL = 0xA8` (MY|MV) with the offsets transposed (`+1` column, `+26` row). Two firmwares, two
memory->panel mappings, one piece of glass: rendering both upright is exactly what `St7735s`'s
MADCTL model claims to do (docs/records/0056's addendum), so this board doubles as its
independent check.
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.st7735s import St7735s
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BACKLIGHT_GPIO", "BOARD", "FIRMWARE", "LCD", "board_with")

BACKLIGHT_GPIO = 25

LCD = St7735s  # zero-arg factory: the constructor's defaults already are this board's pin map

_EXTRAS = (LCD, BootselButton, lambda: LEDMock(gpio=BACKLIGHT_GPIO))

# Real download URLs from https://micropython.org/download/WAVESHARE_RP2040_LCD_0_96/ and
# https://circuitpython.org/board/waveshare_rp2040_lcd_0_96/ - only the tags this file was built
# and verified against are listed; add more the same way scripts/fetch_firmware.py does for the
# officially-supported boards, if an older one is ever needed. A value may equally be a local
# `.uf2` path, which is what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_LCD_0_96-20260406-v1.28.0.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_LCD_0_96-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_LCD_0_96-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_LCD_0_96-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_LCD_0_96-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        },
        layout={"fs_start": "0xa0000", "fs_blockcount": 352, "fs_blocksize": 4096},
    ),
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.1.1.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.3.0-alpha.4.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_lcd_0_96/en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-9.2.9.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_frame: "Callable[[bytes], None]") -> BoardSpec:
    """The same board, with the panel's `on_frame` wired to `on_frame` - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller. An SDK caller that wants the pixels
    (a viewer, a test asserting on what was drawn) builds its board through this instead of
    `BOARD`, resolves it (`rp2040py.boards.resolve_firmware(board, "micropython")`, or
    `"circuitpython"`), then passes it as `MicroPythonDevice(board=...)`. Worth more on the
    CircuitPython side: that firmware paints the display from `board_init()`, so frames arrive
    with no guest code at all."""
    return BoardSpec(extras=(lambda: St7735s(on_frame=on_frame), *_EXTRAS[1:]), firmware=FIRMWARE)


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
