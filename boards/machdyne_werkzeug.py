"""BoardSpec definition for the **Machdyne Werkzeug** (https://machdyne.com/product/werkzeug-multi-tool/)
- a Pico-class RP2040 board with a smaller 1 MiB flash part (vs. a Pico's 2 MiB) and **two plain
LEDs**: green on **GPIO20** (active-low) and red on **GPIO21** (polarity not stated upstream - see
below). Built as a worked `--board-spec` example, picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --board-spec boards/machdyne_werkzeug.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.machdyne_werkzeug:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

**MicroPython-only, deliberately - no CircuitPython port exists for this board at all**
(`ports/raspberrypi/boards/machdyne_werkzeug/` 404s upstream, confirmed via `gh api`).

File named `machdyne_werkzeug.py` after MicroPython's own board id
(`ports/rp2/boards/MACHDYNE_WERKZEUG`), case-normalized.

Every number below is derived from a local checkout of the upstream MicroPython port at its current
tag (docs/records/0027's "3g rule"):

- `pins.csv`: `LED_GREEN,GPIO20` / `LED_RED,GPIO21` - the only two entries beyond the plain `GPx`/
  `PMODx`/`USBA_*` breakout aliases. `mpconfigboard.h`:
  `MICROPY_HW_BOARD_NAME "Machdyne Werkzeug"`, `MICROPY_HW_FLASH_STORAGE_BYTES (384 * 1024)` - no
  USB VID/PID override, so this board uses the generic rp2-port default (`0x2E8A`/`0x0005`).
- The pico-sdk board header `lib/pico-sdk/src/boards/include/boards/machdyne_werkzeug.h`:
  `PICO_DEFAULT_LED_PIN 20` **with `PICO_DEFAULT_LED_PIN_INVERTED 1`** - the green LED is wired
  **active-low**, the first board in this project's `boards/` where that's a real, sourced fact
  rather than the active-high default every other board's plain LED has used so far. The header
  names only the green LED (pico-sdk's "default LED" concept is singular); the red LED's polarity
  is not stated in either source checked, so it is modelled active-high (this project's default)
  rather than guessed active-low by inference from its sibling - a real, stated gap, not an
  oversight. Also confirms `PICO_FLASH_SIZE_BYTES (1 * 1024 * 1024)` - the same smaller-than-Pico
  flash part as `boards/garatronic_pybstick26_rp2040/`.

**`rp2040py.external.led_mock.LEDMock` gained an `active_low` constructor argument for this board**
(previously every LED this project modelled was active-high, so the option didn't exist) -
`.on`/`.toggle_count` report the LED's true logical state either way, not the raw pin level, so a
caller never has to know a board's wiring polarity to read it. Defaults to `False`, so every
existing board's behavior is unchanged.

Flash geometry: `fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES = 1 MiB - 384
KiB = 0xa0000`, `fs_blockcount = 384 KiB / 4 KiB = 96` (docs/records/0035's derivation) - the same
numbers as `boards/garatronic_pybstick26_rp2040/`, since both boards share the same 1 MiB/384 KiB
split.

Onboard extras:

- The green LED: `LEDMock(gpio=20, active_low=True)` - see the pico-sdk citation above.
- The red LED: `LEDMock(gpio=21)` - active-high, this project's default; real per `pins.csv`, but
  its polarity is a stated gap (see above), not a sourced fact.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- RESET: `ResetButton` (`rp2040py.external.reset_button`). Machdyne's own specifications say
  "Reset and boot select buttons"; there is still no schematic statement of the net, and there does
  not need to be - RUN's net carries no pin number and no configurable pull (docs/records/0057's
  addendum), so presence is the whole fact. Modelled since docs/records/0089's Phase 4.
- Not modelled: the USB-A host port (`USBA_POWER`/
  `USBA_DN`/`USBA_DP`/`USBA_DP_PU` in `pins.csv` - a real onboard USB-A receptacle this board
  exposes as plain GPIO, out of scope for a board-file `ExternalDevice` mix), the PMOD headers
  (plain GPIO breakouts, nothing board-specific to model), and the RP2040's own RTC.
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.reset_button import ResetButton
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "LED_GREEN_GPIO", "LED_RED_GPIO")

LED_GREEN_GPIO = 20
LED_RED_GPIO = 21

_EXTRAS = (
    lambda: LEDMock(gpio=LED_GREEN_GPIO, active_low=True),
    lambda: LEDMock(gpio=LED_RED_GPIO),
    BootselButton,
    ResetButton,
)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug MACHDYNE_WERKZEUG
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20260406-v1.28.0.uf2",
            "1.27.0": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20251209-v1.27.0.uf2",
            "1.26.1": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20250911-v1.26.1.uf2",
            "1.26.0": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20250809-v1.26.0.uf2",
            "1.25.0": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20250415-v1.25.0.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
            "1.29.0": "https://micropython.org/resources/firmware/MACHDYNE_WERKZEUG-20260824-v1.29.0.uf2",
        },
        layout={"fs_start": "0xa0000", "fs_blockcount": 96, "fs_blocksize": 4096},
    ),
}

BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
