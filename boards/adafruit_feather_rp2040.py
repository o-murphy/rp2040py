"""BoardSpec definition for the **Adafruit Feather RP2040** (https://www.adafruit.com/product/4884)
- a Feather-form-factor RP2040 board, electrically a Pico-class board with a bigger flash part (8
MiB vs. a Pico's 2 MiB) and two onboard indicators: a plain red LED on **GPIO13** and a **WS2812
RGB NeoPixel on GPIO16**. Built as a worked `--board-spec` example, the second board picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey (after
[0068](../docs/records/0068-waveshare-rp2040-zero-board.md)'s Waveshare RP2040-Zero); load it
with e.g.:

    rp2040py micropython --board-spec boards/adafruit_feather_rp2040.py:BOARD -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/adafruit_feather_rp2040.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.adafruit_feather_rp2040:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `adafruit_feather_rp2040.py` after both firmwares' own board id - MicroPython's
`ports/rp2/boards/ADAFRUIT_FEATHER_RP2040`, case-normalized, and CircuitPython's own
`adafruit_feather_rp2040` (they agree here, unlike `boards/weactstudio/`).

Every number below is derived from a local checkout of both upstream ports at their current tags,
not guessed (docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/ADAFRUIT_FEATHER_RP2040/`: `pins.csv` (`LED,GPIO13` - the file's
  only entry), `mpconfigboard.h` (`MICROPY_HW_BOARD_NAME "Adafruit Feather RP2040"`, USB VID
  `0x239A`/PID `0x80F2`, `MICROPY_HW_FLASH_STORAGE_BYTES (7 * 1024 * 1024)`, and its own comments
  `// NeoPixel GPIO16, power not toggleable` / `// Red user LED GPIO13`), and the pico-sdk board
  header `lib/pico-sdk/src/boards/include/boards/adafruit_feather_rp2040.h`
  (`PICO_DEFAULT_LED_PIN 13`, `PICO_DEFAULT_WS2812_PIN 16`, `PICO_FLASH_SIZE_BYTES (8 * 1024 *
  1024)`, no board-specific `link.ld`/firmware-size override).
- CircuitPython, `ports/raspberrypi/boards/adafruit_feather_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO16)`, agreeing with MicroPython's pin), `mpconfigboard.mk` (same
  USB VID/PID, `EXTERNAL_FLASH_DEVICES = "GD25Q64C,W25Q64JVxQ"` - both 64 Mbit = 8 MiB, agreeing
  with the pico-sdk header), and `pins.c` (`LED`/`D13` -> GPIO13, `NEOPIXEL` -> GPIO16, `BUTTON`/
  `BOOT` -> GPIO4 - a *second*, non-BOOTSEL button this board file does not model, see below). No
  board-specific `link.ld` (confirmed absent from the directory listing), so
  `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies, same derivation
  `boards/vcc_gnd_yd_rp2040/`, `boards/weactstudio/` and `boards/waveshare_rp2040_zero/` already
  document.

**Adafruit's own product page (adafruit.com/product/4884) claims "RGB NeoPixel with power pin on
GPIO so you can depower it for low power usages" - this is contradicted by both firmware sources
above and is not modelled here.** Neither `mpconfigboard.h` (MicroPython's own comment states
"power not toggleable" in so many words) nor the pico-sdk board header defines a
`PICO_DEFAULT_WS2812_POWER_PIN` for this board at all - contrast with
`adafruit_itsybitsy_rp2040.h`, a sibling board that *does* define one (`PICO_DEFAULT_WS2812_POWER_PIN
16`). CircuitPython's `pins.c`/`mpconfigboard.h` agree: no `NEOPIXEL_POWER` pin exported. Per the 3g
rule, firmware's own build configuration - checked twice, independently, across both ports -
outweighs marketing copy; this may describe a later hardware revision, a sibling product, or simply
be wrong, but it is not what either upstream port was built against.

Flash geometry, MicroPython: `fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES = 8
MiB - 7 MiB = 0x100000`, `fs_blockcount = 7 MiB / 4 KiB = 1792` (docs/records/0035's derivation,
same shape `boards/weactstudio/` already uses per flash variant). CircuitPython's own start is the
generic `firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 1020 KiB + 4 KiB = 0x100000` (same numeric
value as the MicroPython start, coincidentally - the two derivations are unrelated), `fs_blockcount
= 512` following this project's existing CircuitPython convention (0035: only the *start* has to be
right, since the emulated flash buffer is 16 MiB).

Onboard extras:

- The LED: `LEDMock(gpio=13)` (`rp2040py.external.led_mock`) - a plain LED per both ports'
  `LED`/`PICO_DEFAULT_LED_PIN` naming, not a NeoPixel.
- The RGB LED: `Ws2812(gpio=16)` (`rp2040py.external.ws2812`) - a real WS2812-class part per both
  ports' `NEOPIXEL` naming, so `Ws2812`'s default `color_order="GRB"` applies. Live-boot-verified
  (`tests/ws2812_boot_decode.py`'s pattern, run against this board file) that CircuitPython drives
  this LED as its own status indicator from boot, same as `vcc_gnd_yd_rp2040` and
  `waveshare_rp2040_zero` - 11 frames decoded before any guest code ran in one measured boot - so
  `board_with(on_pixels)` below sees pixels with no guest code at all. MicroPython's build declares
  no such default and needs guest code to write to `NEOPIXEL`/GPIO16 itself.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- Not modelled: the second, **non-BOOTSEL user button** CircuitPython's `pins.c` names `BUTTON`/
  `BOOT` on GPIO4 - a plain GPIO button this board genuinely has in addition to BOOTSEL, but its
  pull/polarity is not established from either port's source (neither `pins.c` nor
  `mpconfigboard.h` states a pull direction), so per the 3g rule it stays undocumented rather than
  guessed - a real gap, not an oversight, unlike `boards/vcc_gnd_yd_rp2040/`'s USRKEY, where a
  vendor schematic settled the same question.
- RESET: `ResetButton` (`rp2040py.external.reset_button`). Adafruit's own product page states it in
  the feature list - "Both Reset button and Bootloader select button for quick restarts" - which is
  the only per-board fact a RESET button needs, since RUN's net carries no configurable pull and no
  pin number (docs/records/0057's addendum). Modelled since docs/records/0089's Phase 4.
- Not modelled: the STEMMA QT
  I2C connector (electrically just `I2C(1)`, nothing board-specific to model), the LiPo charging
  circuit and its status LED (analog, not RP2040-visible), and USB-C.
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.reset_button import ResetButton
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "LED_GPIO", "RGB_GPIO", "board_with")

LED_GPIO = 13
RGB_GPIO = 16

_EXTRAS = (lambda: LEDMock(gpio=LED_GPIO), lambda: Ws2812(gpio=RGB_GPIO), BootselButton, ResetButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug ADAFRUIT_FEATHER_RP2040
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug adafruit_feather_rp2040
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260406-v1.28.0.uf2",
            "1.27.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20251209-v1.27.0.uf2",
            "1.26.1": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20250911-v1.26.1.uf2",
            "1.26.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20250809-v1.26.0.uf2",
            "1.25.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20250415-v1.25.0.uf2",
            "1.24.1": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20241129-v1.24.1.uf2",
            "1.24.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20241025-v1.24.0.uf2",
            "1.23.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20240602-v1.23.0.uf2",
            "1.22.2": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20240222-v1.22.2.uf2",
            "1.22.1": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20240105-v1.22.1.uf2",
            "1.22.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20231227-v1.22.0.uf2",
            "1.21.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20231005-v1.21.0.uf2",
            "1.20.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20230426-v1.20.0.uf2",
            "1.19.1": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20220618-v1.19.1.uf2",
            "1.18": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20220117-v1.18.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
            "1.29.0": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260824-v1.29.0.uf2",
            "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260831-v1.30.0-preview.24.g8162451850.uf2",
            "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260827-v1.30.0-preview.8.gf668077be2.uf2",
            "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/ADAFRUIT_FEATHER_RP2040-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 1792, "fs_blocksize": 4096},
    ),
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.1.1.uf2",
            "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.1.2.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.3.0-alpha.4.uf2",
            "6.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.2.0-beta.0.uf2",
            "6.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.2.0-beta.1.uf2",
            "6.2.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.2.0-beta.2.uf2",
            "6.2.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.2.0-beta.3.uf2",
            "6.2.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.2.0-beta.4.uf2",
            "6.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.2.0-rc.0.uf2",
            "6.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.2.0.uf2",
            "6.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.3.0-rc.0.uf2",
            "6.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-6.3.0.uf2",
            "7.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-alpha.1.uf2",
            "7.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-alpha.2.uf2",
            "7.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-alpha.3.uf2",
            "7.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-alpha.4.uf2",
            "7.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-alpha.5.uf2",
            "7.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-alpha.6.uf2",
            "7.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-beta.0.uf2",
            "7.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-rc.0.uf2",
            "7.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-rc.1.uf2",
            "7.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-rc.2.uf2",
            "7.0.0-rc.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0-rc.3.uf2",
            "7.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.0.0.uf2",
            "7.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.0-beta.0.uf2",
            "7.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.0-beta.1.uf2",
            "7.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.0-beta.2.uf2",
            "7.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.0-beta.3.uf2",
            "7.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.0-rc.0.uf2",
            "7.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.0-rc.1.uf2",
            "7.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.0.uf2",
            "7.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.1.1.uf2",
            "7.2.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.0-alpha.0.uf2",
            "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.0-alpha.1.uf2",
            "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.0-alpha.2.uf2",
            "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.0-rc.0.uf2",
            "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.0-rc.2.uf2",
            "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.0.uf2",
            "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.1.uf2",
            "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.2.uf2",
            "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.3.uf2",
            "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.4.uf2",
            "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.2.5.uf2",
            "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0-alpha.0.uf2",
            "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0-beta.0.uf2",
            "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0-beta.1.uf2",
            "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0-beta.2.uf2",
            "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0-rc.0.uf2",
            "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0-rc.1.uf2",
            "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0-rc.2.uf2",
            "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.0.uf2",
            "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.1.uf2",
            "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.2.uf2",
            "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-7.3.3.uf2",
            "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-alpha.0.uf2",
            "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-alpha.1.uf2",
            "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-beta.0.uf2",
            "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-beta.1.uf2",
            "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-beta.2.uf2",
            "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-beta.3.uf2",
            "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-beta.4.uf2",
            "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-beta.5.uf2",
            "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-beta.6.uf2",
            "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-rc.0.uf2",
            "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-rc.1.uf2",
            "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0-rc.2.uf2",
            "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.0.uf2",
            "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.2.uf2",
            "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.3.uf2",
            "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.4.uf2",
            "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.0.5.uf2",
            "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.1.0-beta.0.uf2",
            "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.1.0-beta.1.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-9.2.9.uf2",
            "10.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.3.0-rc.0.uf2",
            "10.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_feather_rp2040/en_US/adafruit-circuitpython-adafruit_feather_rp2040-en_US-10.3.0.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/vcc_gnd_yd_rp2040/` and `boards/waveshare_rp2040_zero/`). Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(
        extras=(_EXTRAS[0], lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[2:]), firmware=FIRMWARE
    )


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
