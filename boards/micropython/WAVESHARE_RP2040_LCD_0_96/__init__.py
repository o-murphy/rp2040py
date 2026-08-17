"""BoardSpec definition for the Waveshare RP2040-LCD-0.96 (https://www.waveshare.com/wiki/RP2040-LCD-0.96)
- a Pico-class RP2040 board with 2 MiB flash, USB-C, a battery header, and an onboard 0.96inch
160x80 65K-color IPS panel (ST7735S controller) wired to SPI1. Built as a second worked
`--board-spec` example for docs/reference/external-devices-and-boards.md, alongside
`boards/micropython/WEACTSTUDIO/` - load it with e.g.:

    rp2040py micropython --board-spec boards/micropython/WAVESHARE_RP2040_LCD_0_96/__init__.py:BOARD
    PYTHONPATH=. rp2040py micropython --board-spec boards.micropython.WAVESHARE_RP2040_LCD_0_96:BOARD

(the file-path form works from anywhere, no setup needed, for this single-file board; the dotted
module form needs `PYTHONPATH=.` - same as `python -m`/`-c` already puts the current directory on
`sys.path`.)

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry (see `boards/__init__.py`, and docs/records/0049's
"Contributing upstream instead" framing for what promoting one into `boards.BOARDS` would take).

Unlike `WEACTSTUDIO`, this board is the *interesting* case for external devices, not just for
flash numbers: what makes it this board rather than a plain Pico is the panel soldered to it, so
the `BoardSpec` attaches an `St7735s` (`rp2040py.external.st7735s`) as a fixed extra - a board
you can boot and actually get pixels out of, without the caller wiring anything up. Pass your own
`on_frame` by building a `BoardSpec` from `LCD`/`_EXTRAS` below if you want to see them (see the
`board_with()` helper).

Every number here is derived from real upstream sources, not guessed (docs/records/0027's "3g
rule", 0049's own repeated emphasis):

- `ports/rp2/boards/WAVESHARE_RP2040_LCD_0_96/mpconfigboard.cmake` (MicroPython): `PICO_BOARD =
  waveshare_rp2040_lcd_0.96`, `MICROPY_HW_FLASH_STORAGE_BYTES = 1441792` (1408 KiB).
- `src/boards/include/boards/waveshare_rp2040_lcd_0.96.h` (pico-sdk): `PICO_FLASH_SIZE_BYTES =
  2 MiB`, and the LCD's own pin map - `WAVESHARE_LCD_SPI 1`, `WAVESHARE_LCD_DC_PIN 8`,
  `WAVESHARE_LCD_CS_PIN 9`, `WAVESHARE_LCD_SCLK_PIN 10`, `WAVESHARE_LCD_TX_PIN 11`,
  `WAVESHARE_LCD_RST_PIN 12`, `WAVESHARE_LCD_BL_PIN 25`.
- `ports/rp2/boards/WAVESHARE_RP2040_LCD_0_96/mpconfigboard.h` (MicroPython): `MICROPY_HW_SPI1_SCK
  10`, `MICROPY_HW_SPI1_MOSI 11` - i.e. MicroPython's *default* SPI1 pins on this board are
  already the panel's, so firmware that just says `machine.SPI(1)` is talking to the LCD. (Its
  `MICROPY_HW_SPI1_MISO 8` overlaps the LCD's DC pin; the panel is write-only here, and the
  vendor driver passes `miso=None`, so nothing reads it.)

    PICO_FLASH_SIZE_BYTES            2 MiB   (0x200000)
    MICROPY_HW_FLASH_STORAGE_BYTES   1408 KiB (0x160000)
    fs_start  = 0x200000 - 0x160000 = 0xa0000
    fs_blockcount = 0x160000 / 4096 = 352

`fs_blocksize=4096` is the RP2040's flash sector-erase granularity, not firmware-specific (same as
every other board this project tracks - docs/records/0049's "Design update" section).

Only one flash variant exists upstream (unlike WEACTSTUDIO's four), so there is one `BOARD`.

Onboard extras, and the one thing deliberately *not* modelled:

- The panel: `St7735s()` - its constructor defaults are this board's wiring, since that is where
  they were taken from (see `rp2040py/external/st7735s.py`).
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050).
- The LCD backlight (GPIO25): reuses `LEDMock`, which tracks "is firmware driving this pin high".
  That is honest for on/off control but *not* a brightness model - the vendor driver dims the
  backlight with `PWM(Pin(25))`, and a duty cycle is not something a GPIO-level listener can
  report. Named `BACKLIGHT` below rather than "LED" because this board has no user LED at all
  (the pico-sdk header defines no `PICO_DEFAULT_LED_PIN`, and no `PICO_DEFAULT_WS2812_PIN`
  either - "no PICO_DEFAULT_WS2812_PIN" is an explicit comment in it).
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
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec, FlashLayout
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.st7735s import St7735s
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec, FirmwareSpec
from rp2040py.utils.firmware_retrieve import flash_layout as _flash_layout
from rp2040py.utils.firmware_retrieve import retrieve as _retrieve

__all__ = ("BACKLIGHT_GPIO", "BOARD", "LCD", "board_with")

BACKLIGHT_GPIO = 25

LCD = St7735s  # zero-arg factory: the constructor's defaults already are this board's pin map

_EXTRAS = (LCD, BootselButton, lambda: LEDMock(gpio=BACKLIGHT_GPIO))

_VARIANT = "flash_2m"

# Real download URL from https://micropython.org/download/WAVESHARE_RP2040_LCD_0_96/ - only the tag
# this file was built against (v1.28.0) is listed; add more the same way scripts/fetch_firmware.py
# does for the officially-supported boards, if an older one is ever needed.
_MICROPYTHON = FirmwareSpec(
    boards={
        _VARIANT: BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_LCD_0_96-20260406-v1.28.0.uf2"},
            layout={"fs_start": "0xa0000", "fs_blockcount": 352, "fs_blocksize": 4096},
        )
    }
)


def _board(extras: "tuple[Callable[[], object], ...]") -> BoardSpec:
    # Resolved eagerly at import time (same pattern as boards/micropython/WEACTSTUDIO and
    # tests/pico_spec.py) - cached under ~/.cache/rp2040py after the first real download.
    image = _retrieve(_MICROPYTHON, None, _VARIANT)
    return BoardSpec(extras=extras, image=image, layout=FlashLayout(**_flash_layout(_MICROPYTHON, _VARIANT)))


def board_with(on_frame: "Callable[[bytes], None]") -> BoardSpec:
    """The same board, with the panel's `on_frame` wired to `on_frame` - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller. An SDK caller that wants the pixels
    (a viewer, a test asserting on what was drawn) builds its board through this instead of
    `BOARD`, then passes it as `MicroPythonDevice(board=...)`."""
    return _board((lambda: St7735s(on_frame=on_frame), *_EXTRAS[1:]))


BOARD = _board(_EXTRAS)
