"""BoardSpec definitions for the WeAct Studio RP2040 ("Pico Board RP2040" - see
https://github.com/WeActTC/WeActStudio.RP2040CoreBoard), electrically a Pico with a bigger
external QSPI flash chip and no CYW43. Built as a worked `--board-spec` example for
docs/reference/external-devices-and-boards.md - load it with e.g.:

    rp2040py micropython --board-spec boards/weactstudio/__init__.py:BOARD --littlefs littlefs.img
    rp2040py micropython --circuitpython --board-spec boards/weactstudio/__init__.py:BOARD
    PYTHONPATH=. rp2040py micropython --board-spec boards.weactstudio:BOARD_FLASH_4M ...

(the file-path form works from anywhere, no setup needed, for this single-file board; the dotted
module form needs `PYTHONPATH=.` - same as `python -m`/`-c` already puts the current directory on
`sys.path` for free - but is what a board organized as its own multi-file package, with relative
imports of its own, would need instead).

Directory named `weactstudio` after MicroPython's own board id (`ports/rp2/boards/WEACTSTUDIO`),
case-normalized - the naming rule docs/records/0059 kept when `boards/` stopped being split per
firmware family.

Nothing is downloaded when this module is imported: `firmware` is data, and `rp2040py.boards.
resolve_firmware()` turns it into a concrete image only when something actually boots the board
(docs/records/0059). `retrieve()` caches under `~/.cache/rp2040py`, so an offline run just needs
the `.uf2` dropped there under the exact filename its URL ends with - or the URL replaced with a
local path, which `fw`'s values accept just as readily.

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry (see docs/records/0059's "Promotion checklist" for what
landing this in `boards.BOARDS` for real `--board` support would take - a different, further step,
not what this file does).

Every number below is derived from MicroPython's own upstream WEACTSTUDIO board port
(`ports/rp2/boards/WEACTSTUDIO`), not guessed (docs/records/0027's "3g rule" / 0049's own repeated
emphasis on this) - cross-checked against two independent copies in a local checkout at the
v1.28.0 tag: `lib/pico-sdk/src/boards/include/boards/weact_studio_rp2040_{2,4,8,16}mb.h` and
`ports/rp2/boards/WEACTSTUDIO/weactstudio_{2,4,8,16}MiB.h` (byte-for-byte identical). MicroPython
builds four separate images for this board - `FLASH_2M`/`FLASH_4M`/`FLASH_8M` via `BOARD_VARIANT`,
and a 16 MiB image by default when no variant is given - each with its own
`PICO_FLASH_SIZE_BYTES`/`MICROPY_HW_FLASH_STORAGE_BYTES`:

    variant     PICO_FLASH_SIZE_BYTES   MICROPY_HW_FLASH_STORAGE_BYTES   fs_blockcount (/4096)
    FLASH_2M    2 MiB  (0x200000)       1 MiB  (0x100000)                256
    FLASH_4M    4 MiB  (0x400000)       3 MiB  (0x300000)                768
    FLASH_8M    8 MiB  (0x800000)       7 MiB  (0x700000)                1792
    (default)   16 MiB (0x1000000)      15 MiB (0xf00000)                3840

Every variant reserves exactly 1 MiB for firmware code (`MICROPY_HW_FLASH_STORAGE_BYTES =
PICO_FLASH_SIZE_BYTES - 1 MiB`, true for all four), so `fs_start` (= `PICO_FLASH_SIZE_BYTES -
MICROPY_HW_FLASH_STORAGE_BYTES`, same derivation docs/records/0035 already used for pico/pico_w)
is `0x100000` for every variant - only `fs_blockcount` changes with the flash chip's real size.
`fs_blocksize=4096` is the RP2040's flash sector-erase granularity, not firmware-specific (same as
every other board this project tracks - see docs/records/0049's "Design update" section).

CircuitPython ships this board as **`weact_studio_pico`** - a different id for the same hardware,
which is the case docs/records/0059's naming rule covers by citing both rather than splitting the
file. Its numbers come from that port's own config, the same derivation
`boards/waveshare_rp2040_lcd_0_96/` documents:

- `ports/raspberrypi/boards/weact_studio_pico/mpconfigboard.mk` sets only USB ids,
  `CHIP_VARIANT`/`CHIP_FAMILY`, `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ"` and `CIRCUITPY__EVE` - no
  `CIRCUITPY_FIRMWARE_SIZE`, and the board ships no `link.ld` of its own (confirmed 404 upstream),
  so `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies. With
  `CIRCUITPY_INTERNAL_NVM_SIZE = 4 * 1024`, `CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR` is
  1020 KiB + 4 KiB = `0x100000`.
- `fs_blockcount = 512` follows this project's existing CircuitPython entries rather than the
  board's real remaining flash (0035: only the *start* has to be right, since the emulated flash
  buffer is 16 MiB).

Upstream builds exactly **one** CircuitPython image for this board - against the 2 MiB
`W25Q16JVxQ` named above - where MicroPython builds four. The CIRCUITPY drive's start does not
depend on which flash chip is fitted, so every variant below names that same image rather than
three of them declaring no CircuitPython at all.

Onboard LED: GPIO25 (`weactstudio_common.h`'s `PICO_DEFAULT_LED_PIN`, same wiring as a plain
Pico; CircuitPython's own `pins.c` agrees - `"LED"` -> GPIO25) - reuses the built-in `LEDMock`,
nothing board-specific to model. BOOTSEL: wired identically on every RP2040 board that boots from
QSPI flash (docs/records/0050), reuses the built-in `BootselButton`. USR button (GPIO23,
`Pin.PULL_UP`, exposed as `board.key` by MicroPython's own `modules/board.py`, and as `"BUTTON"`
by CircuitPython's `pins.c` - both firmwares agree on the pin): the one genuinely board-specific
pin here, but still no new device class needed - `KeyMock(gpio=23, active_high=False)` below is
already the generic "button on a GPIO pin, released state resolved through whichever pull resistor
firmware configured" device (`external/key_mock.py`).

Not modelled: the **RESET** button. It pulls the RP2040's RUN pin, which is not a GPIO and has no
model here at all - unlike BOOTSEL, which shorts `GPIO_QSPI_SS`, an ordinary pad an
`ExternalDevice` can drive (docs/records/0050/0051). Doing it faithfully needs a reset hook on
`RP2040`, designed in docs/records/0057 and deliberately not implemented yet; until then a guest's
own `machine.reset()` is the working equivalent.

Confirmed **not** the same board as "YD-RP2040" (VCC-GND Studio) - a different vendor's board,
with an onboard NeoPixel this board does not have (confirmed absent from upstream WEACTSTUDIO's
own `pins.csv`/`modules/board.py`) and its USR button on a different pin (GPIO24, not GPIO23).
Deliberately out of scope here, not just left out by oversight.
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.key_mock import KeyMock
from rp2040py.external.led_mock import LEDMock
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "BOARD_FLASH_2M", "BOARD_FLASH_4M", "BOARD_FLASH_8M", "BOARD_FLASH_16M")

_FS_START = "0x100000"
_FS_BLOCKSIZE = 4096
_USR_BUTTON_GPIO = 23

_EXTRAS = (lambda: LEDMock(gpio=25), BootselButton, lambda: KeyMock(gpio=_USR_BUTTON_GPIO, active_high=False))

# Real download URLs from https://micropython.org/download/WEACTSTUDIO/ and
# https://circuitpython.org/board/weact_studio_pico/ - only the tags this file was built/verified
# against are listed; add more the same way scripts/fetch_firmware.py does for the
# officially-supported boards, if an older one is ever needed. A value may equally be a local
# `.uf2` path, which is what makes a hand-written board file able to be fully offline.
_FIRMWARE_URLS = {
    "flash_2m": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260406-v1.28.0.uf2",
    "flash_4m": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260406-v1.28.0.uf2",
    "flash_8m": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260406-v1.28.0.uf2",
    "flash_16m": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260406-v1.28.0.uf2",
}

# One image upstream, for every MicroPython flash variant - see the docstring.
_CIRCUITPYTHON = BoardFirmwareSpec(
    default_tag="10.2.1",
    fw={
        "10.2.1": "https://downloads.circuitpython.org/bin/weact_studio_pico/en_US/"
        "adafruit-circuitpython-weact_studio_pico-en_US-10.2.1.uf2"
    },
    layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": _FS_BLOCKSIZE},
)


def _board(variant: str, fs_blockcount: int) -> BoardSpec:
    """One flash-size variant, as pure data - nothing here downloads anything (0059). A variant is
    its own `BoardSpec` rather than a `firmware` key, because `firmware` keys are *families*: what
    differs between these four is the physical flash chip, not the firmware family running on it.
    The two families this board does have - MicroPython and CircuitPython - are keys, as designed."""
    return BoardSpec(
        extras=_EXTRAS,
        firmware={
            "micropython": BoardFirmwareSpec(
                default_tag="1.28.0",
                fw={"1.28.0": _FIRMWARE_URLS[variant]},
                layout={"fs_start": _FS_START, "fs_blockcount": fs_blockcount, "fs_blocksize": _FS_BLOCKSIZE},
            ),
            "circuitpython": _CIRCUITPYTHON,
        },
    )


BOARD_FLASH_2M = _board("flash_2m", 256)
BOARD_FLASH_4M = _board("flash_4m", 768)
BOARD_FLASH_8M = _board("flash_8m", 1792)
BOARD_FLASH_16M = _board("flash_16m", 3840)

# The default MicroPython WEACTSTUDIO build (no BOARD_VARIANT given) targets 16 MiB.
BOARD = BOARD_FLASH_16M
