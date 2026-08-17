"""BoardSpec definition for the Waveshare RP2040-LCD-0.96 running **CircuitPython** - the same
physical board as `boards/micropython/WAVESHARE_RP2040_LCD_0_96/` (2 MiB flash, USB-C, battery
header, onboard 0.96inch 160x80 IPS panel on an ST7735S), with a different firmware family, so a
different flash layout and a different image. Load it with e.g.:

    rp2040py micropython --circuitpython --board-spec boards/circuitpython/waveshare_rp2040_lcd_0_96/__init__.py:BOARD
    PYTHONPATH=. rp2040py micropython --circuitpython --board-spec boards.circuitpython.waveshare_rp2040_lcd_0_96:BOARD

`--circuitpython` is still required alongside `--board-spec`: it is not merely a firmware-family
selector (the `BoardSpec` already carries the resolved image), it also picks the FAT12 loader/dump
functions and the post-boot console behavior - see docs/records/0049's flag-compatibility table,
which lists exactly this combination as compatible.

Directory named `waveshare_rp2040_lcd_0_96`, lowercase, because that is CircuitPython's own board
id (`ports/raspberrypi/boards/waveshare_rp2040_lcd_0_96/`, and the `downloads.circuitpython.org`
path); its MicroPython twin is `WAVESHARE_RP2040_LCD_0_96` for the same reason, in that firmware's
own spelling. See `boards/circuitpython/__init__.py`.

Every number derived from real upstream CircuitPython sources (docs/records/0027's "3g rule"):

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

`fs_blockcount = 512` follows this project's existing CircuitPython entries rather than the board's
real remaining flash: only the *start* address has to be right, since rp2040py's emulated flash
buffer is 16 MiB and a generously-sized region past the start collides with nothing (0035's
reasoning, unchanged here). `fs_blocksize = 4096` is the RP2040's sector-erase granularity.

Onboard extras - identical hardware to the MicroPython twin, so identical devices:

- The panel: `St7735s()`, whose constructor defaults are this board's wiring. CircuitPython drives
  it through the *same* pins from `board.c`'s `display_init()` (SPI1 CLK=GPIO10, MOSI=GPIO11,
  DC=GPIO8, CS=GPIO9, RST=GPIO12, backlight GPIO25) at 40 MHz.
- BOOTSEL: `BootselButton` (docs/records/0050/0051).
- The LCD backlight (GPIO25): `LEDMock`, on/off only - and here the caveat bites harder than on
  the MicroPython side, since CircuitPython drives this pin as a real PWM (`board.c` passes a
  50 kHz backlight frequency and `brightness = 1.0f`). See docs/records/0056's addendum.
- Not modelled: the RESET button (pulls RUN, not a GPIO - docs/records/0057), the MP28164
  buck-boost and battery header, USB-C.

**What makes this board interesting beyond "the same board again": CircuitPython initialises the
display itself, at boot.** `board_init()` -> `display_init()` builds a `busdisplay` with
`auto_refresh=true` at 60 fps, so simply booting this `BoardSpec` drives the emulated ST7735S with
no guest code at all - and it does so through a *different* orientation than the MicroPython
vendor driver: `MADCTL = 0xC8` (MY|MX, no MV) with `colstart=26, rowstart=1` and displayio
`rotation=90`, where the MicroPython driver uses `MADCTL = 0xA8` (MY|MV) with the offsets
transposed (`+1` column, `+26` row). Two firmwares, two memory->panel mappings, one piece of
glass: rendering both upright is exactly what `St7735s`'s MADCTL model claims to do
(docs/records/0056's addendum), so this board doubles as its independent check.
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

_VARIANT = "waveshare_rp2040_lcd_0_96"

# Real download URL from https://circuitpython.org/board/waveshare_rp2040_lcd_0_96/ - only the tag
# this file was built and verified against is listed; add more the same way
# scripts/fetch_firmware.py does for the officially-supported boards.
_CIRCUITPYTHON = FirmwareSpec(
    boards={
        _VARIANT: BoardFirmwareSpec(
            default_tag="10.2.1",
            fw={
                "10.2.1": "https://downloads.circuitpython.org/bin/waveshare_rp2040_lcd_0_96/"
                "en_US/adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.2.1.uf2"
            },
            layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
        )
    }
)


def _board(extras: "tuple[Callable[[], object], ...]") -> BoardSpec:
    # Resolved eagerly at import time, same pattern as this board's MicroPython twin - cached
    # under ~/.cache/rp2040py after the first real download.
    image = _retrieve(_CIRCUITPYTHON, None, _VARIANT)
    return BoardSpec(extras=extras, image=image, layout=FlashLayout(**_flash_layout(_CIRCUITPYTHON, _VARIANT)))


def board_with(on_frame: "Callable[[bytes], None]") -> BoardSpec:
    """The same board, with the panel's `on_frame` wired up - the one thing a plain `--board-spec`
    target cannot carry (`BoardSpec.extras` holds zero-arg factories and nothing hands the
    constructed device back). Worth more here than on the MicroPython twin: CircuitPython paints
    the display on its own from `board_init()`, so `MicroPythonDevice(board=board_with(...),
    circuitpython=True)` produces frames with no guest code at all."""
    return _board((lambda: St7735s(on_frame=on_frame), *_EXTRAS[1:]))


BOARD = _board(_EXTRAS)
