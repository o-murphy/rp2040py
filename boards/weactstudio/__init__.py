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

RESET: `ResetButton` (`rp2040py.external.reset_button`). It pulls the RP2040's RUN pin, which is
not a GPIO and has no pad - unlike BOOTSEL, which shorts `GPIO_QSPI_SS`, an ordinary pad an
`ExternalDevice` can drive (docs/records/0050/0051). RUN is now a real level on `RP2040` with the
reset hook `BaseDevice` installs into it (docs/records/0089's Phase 4, closing docs/records/0057),
so pressing holds the chip in reset and releasing boots it.

Confirmed **not** the same board as "YD-RP2040" (VCC-GND Studio) - a different vendor's board,
with an onboard NeoPixel this board does not have (confirmed absent from upstream WEACTSTUDIO's
own `pins.csv`/`modules/board.py`) and its USR button on a different pin (GPIO24, not GPIO23).
Deliberately out of scope here, not just left out by oversight.
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.key_mock import KeyMock
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.reset_button import ResetButton
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "BOARD_FLASH_2M", "BOARD_FLASH_4M", "BOARD_FLASH_8M", "BOARD_FLASH_16M")

_FS_START = "0x100000"
_FS_BLOCKSIZE = 4096
_USR_BUTTON_GPIO = 23

_EXTRAS = (
    lambda: LEDMock(gpio=25),
    BootselButton,
    lambda: KeyMock(gpio=_USR_BUTTON_GPIO, active_high=False),
    ResetButton,
)

# Full version history from https://micropython.org/download/WEACTSTUDIO/ (one page, all four
# variants - see scripts/fetch_firmware.py's own `--page` note for why the fetch needs it split
# per variant rather than one shared dict) and
# https://circuitpython.org/board/weact_studio_pico/, via:
#   uv run scripts/fetch_firmware.py list --family micropython --slug WEACTSTUDIO --page WEACTSTUDIO
#   uv run scripts/fetch_firmware.py list --family micropython --slug WEACTSTUDIO-FLASH_2M --page WEACTSTUDIO
#   uv run scripts/fetch_firmware.py list --family micropython --slug WEACTSTUDIO-FLASH_4M --page WEACTSTUDIO
#   uv run scripts/fetch_firmware.py list --family micropython --slug WEACTSTUDIO-FLASH_8M --page WEACTSTUDIO
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
_MICROPYTHON_FW: dict[str, dict[str, str]] = {
    "flash_2m": {
        "1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260406-v1.28.0.uf2",
        "1.27.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20251209-v1.27.0.uf2",
        "1.26.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20250911-v1.26.1.uf2",
        "1.26.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20250809-v1.26.0.uf2",
        "1.25.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20250415-v1.25.0.uf2",
        "1.24.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20241129-v1.24.1.uf2",
        "1.24.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20241025-v1.24.0.uf2",
        "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
        "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260813-v1.29.0-preview.707.g1827631282.uf2",
        "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
        "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        "1.29.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260824-v1.29.0.uf2",
        "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260831-v1.30.0-preview.24.g8162451850.uf2",
        "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260827-v1.30.0-preview.8.gf668077be2.uf2",
        "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_2M-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
    },
    "flash_4m": {
        "1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260406-v1.28.0.uf2",
        "1.27.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20251209-v1.27.0.uf2",
        "1.26.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20250911-v1.26.1.uf2",
        "1.26.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20250809-v1.26.0.uf2",
        "1.25.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20250415-v1.25.0.uf2",
        "1.24.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20241129-v1.24.1.uf2",
        "1.24.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20241025-v1.24.0.uf2",
        "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
        "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260813-v1.29.0-preview.707.g1827631282.uf2",
        "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
        "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        "1.29.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260824-v1.29.0.uf2",
        "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260831-v1.30.0-preview.24.g8162451850.uf2",
        "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260827-v1.30.0-preview.8.gf668077be2.uf2",
        "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_4M-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
    },
    "flash_8m": {
        "1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260406-v1.28.0.uf2",
        "1.27.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20251209-v1.27.0.uf2",
        "1.26.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20250911-v1.26.1.uf2",
        "1.26.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20250809-v1.26.0.uf2",
        "1.25.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20250415-v1.25.0.uf2",
        "1.24.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20241129-v1.24.1.uf2",
        "1.24.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20241025-v1.24.0.uf2",
        "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
        "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260813-v1.29.0-preview.707.g1827631282.uf2",
        "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
        "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        "1.29.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260824-v1.29.0.uf2",
        "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260831-v1.30.0-preview.24.g8162451850.uf2",
        "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260827-v1.30.0-preview.8.gf668077be2.uf2",
        "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/WEACTSTUDIO-FLASH_8M-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
    },
    # Bare/default variant (no BOARD_VARIANT) - 16 MiB. Note upstream's own history gap here: no
    # FLASH_2M/4M/8M assets existed before 1.24.0 (WEACTSTUDIO shipped only this one image back
    # then), so 1.20.0-1.23.0 are only available in this variant, not the other three.
    "flash_16m": {
        "1.28.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260406-v1.28.0.uf2",
        "1.27.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20251209-v1.27.0.uf2",
        "1.26.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-20250911-v1.26.1.uf2",
        "1.26.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20250809-v1.26.0.uf2",
        "1.25.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20250415-v1.25.0.uf2",
        "1.24.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-20241129-v1.24.1.uf2",
        "1.24.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20241025-v1.24.0.uf2",
        "1.23.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20240602-v1.23.0.uf2",
        "1.22.2": "https://micropython.org/resources/firmware/WEACTSTUDIO-20240222-v1.22.2.uf2",
        "1.22.1": "https://micropython.org/resources/firmware/WEACTSTUDIO-20240105-v1.22.1.uf2",
        "1.22.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20231227-v1.22.0.uf2",
        "1.21.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20231005-v1.21.0.uf2",
        "1.20.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20230426-v1.20.0.uf2",
        "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
        "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260813-v1.29.0-preview.707.g1827631282.uf2",
        "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
        "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        "1.29.0": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260824-v1.29.0.uf2",
        "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260831-v1.30.0-preview.24.g8162451850.uf2",
        "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260827-v1.30.0-preview.8.gf668077be2.uf2",
        "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/WEACTSTUDIO-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
    },
}

# One image upstream, for every MicroPython flash variant - see the docstring.
_CIRCUITPYTHON = BoardFirmwareSpec(
    default_tag="10.2.1",
    fw={
        "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-alpha.2.uf2",
        "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-alpha.3.uf2",
        "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-alpha.4.uf2",
        "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-alpha.5.uf2",
        "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-alpha.6.uf2",
        "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-alpha.7.uf2",
        "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-alpha.8.uf2",
        "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-beta.0.uf2",
        "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-beta.1.uf2",
        "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-beta.2.uf2",
        "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-beta.3.uf2",
        "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0-rc.0.uf2",
        "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.0.uf2",
        "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.1.uf2",
        "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.2.uf2",
        "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.0.3.uf2",
        "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.1.0-beta.0.uf2",
        "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.1.0-beta.1.uf2",
        "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.1.0-rc.1.uf2",
        "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.1.1.uf2",
        "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.1.3.uf2",
        "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.1.4.uf2",
        "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.2.0-alpha.1.uf2",
        "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.2.0-rc.0.uf2",
        "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.2.0.uf2",
        "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.2.1.uf2",
        "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.3.0-alpha.1.uf2",
        "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.3.0-alpha.2.uf2",
        "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.3.0-alpha.3.uf2",
        "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.3.0-alpha.4.uf2",
        "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-alpha.1.uf2",
        "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-beta.0.uf2",
        "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-beta.1.uf2",
        "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-beta.2.uf2",
        "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-beta.3.uf2",
        "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-beta.4.uf2",
        "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-beta.5.uf2",
        "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-beta.6.uf2",
        "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-rc.0.uf2",
        "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-rc.1.uf2",
        "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0-rc.2.uf2",
        "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.0.uf2",
        "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.2.uf2",
        "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.3.uf2",
        "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.4.uf2",
        "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.0.5.uf2",
        "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.1.0-beta.0.uf2",
        "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.1.0-beta.1.uf2",
        "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.1.0-beta.2.uf2",
        "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.1.0-rc.0.uf2",
        "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.1.0.uf2",
        "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.0-beta.0.uf2",
        "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.0-beta.1.uf2",
        "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.0-rc.0.uf2",
        "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.0-rc.1.uf2",
        "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.0.uf2",
        "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.1.uf2",
        "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.10.uf2",
        "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.2.uf2",
        "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.3.uf2",
        "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.4.uf2",
        "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.5.uf2",
        "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.6.uf2",
        "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.7.uf2",
        "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.8.uf2",
        "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-8.2.9.uf2",
        "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-alpha.2.uf2",
        "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-alpha.4.uf2",
        "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-alpha.5.uf2",
        "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-alpha.6.uf2",
        "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-beta.0.uf2",
        "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-beta.1.uf2",
        "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-beta.2.uf2",
        "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-rc.0.uf2",
        "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0-rc.1.uf2",
        "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.0.uf2",
        "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.1.uf2",
        "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.2.uf2",
        "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.3.uf2",
        "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.4.uf2",
        "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.0.5.uf2",
        "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.0-beta.0.uf2",
        "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.0-beta.1.uf2",
        "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.0-beta.2.uf2",
        "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.0-beta.3.uf2",
        "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.0-beta.4.uf2",
        "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.0-rc.0.uf2",
        "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.0.uf2",
        "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.1.uf2",
        "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.2.uf2",
        "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.3.uf2",
        "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.1.4.uf2",
        "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.0-alpha.2350.uf2",
        "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.0-alpha.2351.uf2",
        "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.0-beta.0.uf2",
        "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.0-beta.1.uf2",
        "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.0-rc.0.uf2",
        "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.0.uf2",
        "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.1.uf2",
        "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.2.uf2",
        "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.3.uf2",
        "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.4.uf2",
        "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.5.uf2",
        "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.6.uf2",
        "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.7.uf2",
        "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.8.uf2",
        "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-9.2.9.uf2",
        "10.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.3.0-rc.0.uf2",
        "10.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/weact_studio_pico/en_US/adafruit-circuitpython-weact_studio_pico-en_US-10.3.0.uf2",
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
                fw=_MICROPYTHON_FW[variant],
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
