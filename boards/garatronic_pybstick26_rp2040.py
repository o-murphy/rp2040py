"""BoardSpec definition for the **Garatronic/McHobby PYBStick26 RP2040**
(https://shop.mchobby.be/product.php?id_product=2331) - a Pico-class RP2040 board with a smaller
1 MiB flash part (vs. a Pico's 2 MiB) and a single plain LED on **GPIO23**. Built as a worked
`--board-spec` example, picked up off [0066](../docs/records/0066-board-support-expansion.md)'s
survey as the smallest remaining candidate needing zero new devices - MicroPython-only, one device,
no schematic or second firmware family to cross-check; load it with e.g.:

    rp2040py micropython --board-spec boards/garatronic_pybstick26_rp2040.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.garatronic_pybstick26_rp2040:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

**MicroPython-only, deliberately - no CircuitPython port exists for this board at all**
(`ports/raspberrypi/boards/garatronic_pybstick26_rp2040/` 404s upstream, confirmed via `gh api`).
Unlike `boards/vcc_gnd_yd_rp2040/` (the reverse case - CircuitPython-only), there is no generic
image to fall back on here either; this board simply has one firmware family.

File named `garatronic_pybstick26_rp2040.py` after MicroPython's own board id
(`ports/rp2/boards/GARATRONIC_PYBSTICK26_RP2040`), case-normalized.

Every number below is derived from a local checkout of the upstream MicroPython port at its current
tag (docs/records/0027's "3g rule"):

- `pins.csv` (`LED,GPIO23` - the file's only entry) and `mpconfigboard.h`
  (`MICROPY_HW_BOARD_NAME "GARATRONIC_PYBSTICK26_RP2040"`, `MICROPY_HW_FLASH_STORAGE_BYTES (384 *
  1024)` - no USB VID/PID override, so this board uses the generic rp2-port default `0x2E8A`/
  `0x0005`).
- The pico-sdk board header `lib/pico-sdk/src/boards/include/boards/garatronic_pybstick26_rp2040.h`:
  `PICO_DEFAULT_LED_PIN 23` (agreeing with `pins.csv`), `PICO_FLASH_SIZE_BYTES (1 * 1024 * 1024)` -
  **a smaller flash part than a plain Pico's 2 MiB** - and an explicit `// no
  PICO_DEFAULT_WS2812_PIN` comment. Worth citing plainly: `board.json`'s own `features` list
  includes `"RGB LED"`, which every other source here contradicts - one plain GPIO LED, no
  addressable/multi-pin RGB anywhere in the header, `pins.csv`, or `mpconfigboard.h`. Per the 3g
  rule, the board's own build configuration (checked in two independent places) outweighs a loose
  marketing/feature tag - the same lesson `boards/adafruit_feather_rp2040/`'s NeoPixel-power-pin
  claim already drew from the opposite direction (there, the tag turned out to be false; here, it
  is likewise not supported by any electrical fact in either source).

Flash geometry: `fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES = 1 MiB - 384
KiB = 0xa0000`, `fs_blockcount = 384 KiB / 4 KiB = 96` (docs/records/0035's derivation) - a
different chip's worth of flash than a plain Pico, but coincidentally the same `fs_start` hex value
(`0xa0000`) as `boards/waveshare_rp2040_zero/`'s, since both storage splits happen to leave that
remainder; `fs_blockcount` is what actually differs (96 here vs. 352 there).

Onboard extras:

- The LED: `LEDMock(gpio=23)` (`rp2040py.external.led_mock`) - a plain LED, not a NeoPixel (see the
  `board.json` discrepancy above).
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- Not modelled: the **RESET** button. Not documented in either source cited above - McHobby's own
  product page shows the board has one, but with no schematic or firmware-config statement of its
  net (unlike `boards/vcc_gnd_yd_rp2040/`'s VCC-GND Studio schematic), the same "pulls RUN, not a
  GPIO" gap every other board file here documents applies regardless
  (docs/records/0057). Also not modelled: USB, and the RP2040's own RTC (not board-specific).
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "LED_GPIO")

LED_GPIO = 23

_EXTRAS = (lambda: LEDMock(gpio=LED_GPIO), BootselButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug GARATRONIC_PYBSTICK26_RP2040
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20260406-v1.28.0.uf2",
            "1.27.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20251209-v1.27.0.uf2",
            "1.26.1": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20250911-v1.26.1.uf2",
            "1.26.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20250809-v1.26.0.uf2",
            "1.25.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20250415-v1.25.0.uf2",
            "1.24.1": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20241129-v1.24.1.uf2",
            "1.24.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20241025-v1.24.0.uf2",
            "1.23.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20240602-v1.23.0.uf2",
            "1.22.2": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20240222-v1.22.2.uf2",
            "1.22.1": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20240105-v1.22.1.uf2",
            "1.22.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20231227-v1.22.0.uf2",
            "1.21.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20231005-v1.21.0.uf2",
            "1.20.0": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20230426-v1.20.0.uf2",
            "1.19.1": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20220618-v1.19.1.uf2",
            "1.18": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20220117-v1.18.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/GARATRONIC_PYBSTICK26_RP2040-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        },
        layout={"fs_start": "0xa0000", "fs_blockcount": 96, "fs_blocksize": 4096},
    ),
}

BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
