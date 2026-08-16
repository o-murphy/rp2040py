"""BoardSpec definitions for the WeAct Studio RP2040 ("Pico Board RP2040" - see
https://github.com/WeActTC/WeActStudio.RP2040CoreBoard), electrically a Pico with a bigger
external QSPI flash chip and no CYW43. Built as a worked `--board-spec` example for
docs/reference/external-devices-and-boards.md - load it with e.g.:

    rp2040py micropython --board-spec boards/micropython/WEACTSTUDIO/__init__.py:BOARD --littlefs littlefs.img
    PYTHONPATH=. rp2040py micropython --board-spec boards.micropython.WEACTSTUDIO:BOARD_FLASH_4M ...

(the file-path form works from anywhere, no setup needed, for this single-file board; the dotted
module form needs `PYTHONPATH=.` - same as `python -m`/`-c` already puts the current directory on
`sys.path` for free - but is what a board organized as its own multi-file package, with relative
imports of its own, would need instead).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry (see docs/records/0049's "Contributing upstream
instead" section for what landing this in `boards.BOARDS` for real `--board` support would look
like instead - a different, further step, not what this file does).

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

Onboard LED: GPIO25 (`weactstudio_common.h`'s `PICO_DEFAULT_LED_PIN`, same wiring as a plain
Pico) - reuses the built-in `LEDMock`, nothing board-specific to model. BOOTSEL: wired identically
on every RP2040 board that boots from QSPI flash (docs/records/0050), reuses the built-in
`BootselButton`. USR button (GPIO23, `Pin.PULL_UP`, exposed as `board.key` by MicroPython's own
`modules/board.py`): the one genuinely board-specific pin here, but still no new device class
needed - `KeyMock(gpio=23, active_high=False)` below is already the generic "button on a GPIO
pin, released state resolved through whichever pull resistor firmware configured" device
(`external/key_mock.py`).

Confirmed **not** the same board as "YD-RP2040" (VCC-GND Studio) - a different vendor's board,
with an onboard NeoPixel this board does not have (confirmed absent from upstream WEACTSTUDIO's
own `pins.csv`/`modules/board.py`) and its USR button on a different pin (GPIO24, not GPIO23).
Deliberately out of scope here, not just left out by oversight.
"""

from rp2040py.boards import BoardSpec, FlashLayout
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.key_mock import KeyMock
from rp2040py.external.led_mock import LEDMock
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec, FirmwareSpec
from rp2040py.utils.firmware_retrieve import flash_layout as _flash_layout
from rp2040py.utils.firmware_retrieve import retrieve as _retrieve

__all__ = ("BOARD", "BOARD_FLASH_2M", "BOARD_FLASH_4M", "BOARD_FLASH_8M", "BOARD_FLASH_16M")

_FS_START = "0x100000"
_FS_BLOCKSIZE = 4096
_USR_BUTTON_GPIO = 23

_EXTRAS = (lambda: LEDMock(gpio=25), BootselButton, lambda: KeyMock(gpio=_USR_BUTTON_GPIO, active_high=False))

# Real download URLs from https://micropython.org/download/WEACTSTUDIO/ - only the tag this file
# was built/verified against (v1.28.0) is listed; add more the same way scripts/fetch_firmware.py
# does for the officially-supported boards, if an older one is ever needed.
_MICROPYTHON = FirmwareSpec(
    boards={
        "flash_2m": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260406-v1.28.0.uf2"},
            layout={"fs_start": _FS_START, "fs_blockcount": 256, "fs_blocksize": _FS_BLOCKSIZE},
        ),
        "flash_4m": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260406-v1.28.0.uf2"},
            layout={"fs_start": _FS_START, "fs_blockcount": 768, "fs_blocksize": _FS_BLOCKSIZE},
        ),
        "flash_8m": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260406-v1.28.0.uf2"},
            layout={"fs_start": _FS_START, "fs_blockcount": 1792, "fs_blocksize": _FS_BLOCKSIZE},
        ),
        "flash_16m": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260406-v1.28.0.uf2"},
            layout={"fs_start": _FS_START, "fs_blockcount": 3840, "fs_blocksize": _FS_BLOCKSIZE},
        ),
    }
)


def _board(variant: str) -> BoardSpec:
    # Resolved eagerly at import time (same pattern as tests/pico_spec.py) - each of the four
    # variants below pays for this once per process, cached under ~/.cache/rp2040py after the
    # first real download, same as any other retrieve() call.
    image = _retrieve(_MICROPYTHON, None, variant)
    return BoardSpec(extras=_EXTRAS, image=image, layout=FlashLayout(**_flash_layout(_MICROPYTHON, variant)))


BOARD_FLASH_2M = _board("flash_2m")
BOARD_FLASH_4M = _board("flash_4m")
BOARD_FLASH_8M = _board("flash_8m")
BOARD_FLASH_16M = _board("flash_16m")

# The default MicroPython WEACTSTUDIO build (no BOARD_VARIANT given) targets 16 MiB.
BOARD = BOARD_FLASH_16M
