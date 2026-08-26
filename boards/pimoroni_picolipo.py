"""BoardSpec definitions for the **Pimoroni Pico LiPo** (https://shop.pimoroni.com/products/pimoroni-pico-lipo)
- a Pico-class RP2040 board with onboard LiPo charging, in two flash-size variants: **4 MiB**
(default) and **16 MiB**. Built as a worked `--board-spec` example, picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --board-spec boards/pimoroni_picolipo.py:BOARD -c "<probe>"
    rp2040py micropython --board-spec boards/pimoroni_picolipo.py:BOARD_16MB -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/pimoroni_picolipo.py:BOARD
    PYTHONPATH=. rp2040py micropython --board-spec boards.pimoroni_picolipo:BOARD_16MB ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

A single flat file, not a directory - docs/records/0059 explicitly sanctions this
("`my_board.py` - a single file is still fine") for a board that needs no custom device class of
its own; every device this board uses (`LEDMock`, `BootselButton`) already lives in
`rp2040py.external`, so nothing here needs a package. `BOARD`/`BOARD_16MB` are the two variants,
the same shape `boards/weactstudio/`'s four `BOARD_FLASH_*` names already established (that board
predates this file and keeps its own directory).

Every number below is derived from a local checkout of both upstream ports at their current tags
(docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/PIMORONI_PICOLIPO/`: `mpconfigvariant.cmake`
  (`set(PICO_BOARD "pimoroni_picolipo_4mb")`, the default variant) and
  `mpconfigvariant_FLASH_16M.cmake` (`set(PICO_BOARD "pimoroni_picolipo_16mb")`), `pins.csv`
  (`LED,GPIO25` / `BOOT,GPIO23` / `VBUS_SENSE,GPIO24` / `BAT_SENSE,GPIO29`), `mpconfigboard.h`
  (USB VID `0x2E8A`/PID `0x1002`, `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 *
  1024 * 1024))` - a formula, not a literal, resolved per-variant below). The pico-sdk board
  headers `lib/pico-sdk/src/boards/include/boards/pimoroni_picolipo_{4,16}mb.h`:
  `PICOLIPO_USER_SW_PIN 23`, `PICOLIPO_VBUS_DETECT_PIN 24`, `PICOLIPO_BAT_SENSE_PIN 29`,
  `PICO_DEFAULT_LED_PIN 25` (no inversion macro - active-high, this project's default), an
  explicit `// no PICO_DEFAULT_WS2812_PIN` comment, and `PICO_FLASH_SIZE_BYTES` `4`/`16` MiB
  respectively - otherwise byte-for-byte identical between the two variants.
- CircuitPython, `ports/raspberrypi/boards/pimoroni_picolipo_{4,16}mb/`: `pins.c` agrees on every
  pin (`USER_SW` -> GPIO23, `VBUS_DETECT` -> GPIO24, `LED` -> GPIO25, `BAT_SENSE` -> GPIO29, no
  `NEOPIXEL` entry). `mpconfigboard.mk`: USB PID differs per variant (`0x1002` 4 MiB / `0x1003` 16
  MiB - two distinct USB device identities), `EXTERNAL_FLASH_DEVICES` `"W25Q32JVxQ"` (32 Mbit = 4
  MiB) / `"W25Q128JVxQ"` (128 Mbit = 16 MiB), agreeing with the pico-sdk headers' flash sizes. No
  board-specific `link.ld` for either variant, so `ports/raspberrypi/link-rp2040.ld`'s default
  `firmware_size = 1020K` applies, same derivation every other board file in this project already
  documents.

**`USER_SW` (GPIO23) is not modelled.** Its wiring is real (both firmware ports agree on the pin),
and Pimoroni's own product page names it "Switch for basic input (doubles up as DFU select on
boot)" - but neither firmware port's source states a pull direction or confirms/denies the
DFU-select claim's actual electrical mechanism (e.g. whether it is diode-coupled into the real
BOOTSEL/`GPIO_QSPI_SS` pad the way `adafruit_itsybitsy_rp2040`/`adafruit_qtpy_rp2040`'s BOOT
buttons are - docs/records/0070/0071), and no vendor schematic was found (`pimoroni/pico-lipo` is
a firmware repo, not a hardware-design one). Per the 3g rule, "probably the same shape as the
Adafruit boards" is exactly the kind of inference not to make without a real source - so this pin
is a stated gap, not modelled, rather than guessed. A real schematic would settle it, the same way
one did for `adafruit_itsybitsy_rp2040`'s BOOT button.

Flash geometry, MicroPython (`fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES`,
0035's derivation): both variants reserve exactly 1 MiB for firmware code
(`MICROPY_HW_FLASH_STORAGE_BYTES = PICO_FLASH_SIZE_BYTES - 1 MiB`, true for both), so
`fs_start = 0x100000` for both - only `fs_blockcount` changes with the flash chip's real size
(same shape `boards/weactstudio/`'s own docstring documents for its four variants):

    variant   PICO_FLASH_SIZE_BYTES   MICROPY_HW_FLASH_STORAGE_BYTES   fs_blockcount (/4096)
    4 MiB     4 MiB   (0x400000)      3 MiB  (0x300000)                768
    16 MiB    16 MiB  (0x1000000)     15 MiB (0xf00000)                3840

CircuitPython's own start is the generic `firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 0x100000`
for both variants, `fs_blockcount = 512` following this project's existing CircuitPython
convention (0035: only the *start* has to be right, since the emulated flash buffer is 16 MiB).

Onboard extras (identical between variants - only flash size and firmware differ):

- The LED: `LEDMock(gpio=25)` (`rp2040py.external.led_mock`) - a plain LED, active-high (no
  `PICO_DEFAULT_LED_PIN_INVERTED` in either pico-sdk header), not a NeoPixel.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051) - the real BOOTSEL mechanism, independent of `USER_SW`.
- **No `ResetButton`, and that is a finding rather than a gap** (checked 2026-08-20, correcting
  what this file used to say). This board has no dedicated RESET button: Pimoroni's own product
  page describes "an on/off button and a BOOTSEL button", and the way it reboots is "The power
  button can also be used as a reset button, yay! Just double press it to cut and reinstate the
  power". That is a **power cycle**, not a RUN short - a different reset cause on real silicon
  (`HAD_POR` rather than `HAD_RUN`, docs/records/0089 §1.3), which is exactly why attaching a
  `ResetButton` here would be wrong rather than merely approximate.

  The *cause* is not what is missing - `ResetCause.POWER_ON` -> `CHIP_RESET.HAD_POR` already exists
  and is what `BaseDevice.hard_reset(cause=ResetCause.POWER_ON)` records (0089's Phase 1). What a
  faithful `PowerButton` would still need is a trigger and a decision: `RP2040` exposes
  `on_run_pin_reset` and nothing equivalent for power, so an `ExternalDevice` - which only ever gets
  `attach(rp2040)` - has nothing to call; and "what a power cut destroys" is undecided. 0089's D6
  keeps SRAM across a reset deliberately, but that reasoning is about a *PSM* reset, not about the
  rail actually going away. Both are written up in docs/records/0092, which decides nothing and
  builds nothing - this board is simply the one that raised the question.
- Not modelled: `USER_SW`/GPIO23 (see above), the power button (above), the MCP73831 LiPo charger
  and its charge-status LED, the battery-voltage sense circuit on `BAT_SENSE`/GPIO29 (a real ADC
  input this board exposes, but nothing board-specific to model beyond the RP2040's own ADC), the
  STEMMA QT/Qwiic connector (electrically just `I2C(0)`), USB-C, and the RP2040's own RTC.
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "BOARD_16MB", "LED_GPIO")

LED_GPIO = 25

_EXTRAS = (lambda: LEDMock(gpio=LED_GPIO), BootselButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug PIMORONI_PICOLIPO --page PIMORONI_PICOLIPO
#   uv run scripts/fetch_firmware.py list --family micropython --slug PIMORONI_PICOLIPO-FLASH_16M --page PIMORONI_PICOLIPO
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug pimoroni_picolipo_4mb
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug pimoroni_picolipo_16mb
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
_MICROPYTHON_4MB: dict[str, str] = {
    "1.28.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20260406-v1.28.0.uf2",
    "1.27.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20251209-v1.27.0.uf2",
    "1.26.1": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20250911-v1.26.1.uf2",
    "1.26.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20250809-v1.26.0.uf2",
    "1.25.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20250415-v1.25.0.uf2",
    "1.24.1": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20241129-v1.24.1.uf2",
    "1.24.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20241025-v1.24.0.uf2",
    "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
    "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20260813-v1.29.0-preview.707.g1827631282.uf2",
    "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
    "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
    "1.29.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-20260824-v1.29.0.uf2",
}

_MICROPYTHON_16MB: dict[str, str] = {
    "1.28.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20260406-v1.28.0.uf2",
    "1.27.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20251209-v1.27.0.uf2",
    "1.26.1": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20250911-v1.26.1.uf2",
    "1.26.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20250809-v1.26.0.uf2",
    "1.25.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20250415-v1.25.0.uf2",
    "1.24.1": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20241129-v1.24.1.uf2",
    "1.24.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20241025-v1.24.0.uf2",
    "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
    "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20260813-v1.29.0-preview.707.g1827631282.uf2",
    "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
    "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
    "1.29.0": "https://micropython.org/resources/firmware/PIMORONI_PICOLIPO-FLASH_16M-20260824-v1.29.0.uf2",
}

_CIRCUITPYTHON_4MB: dict[str, str] = {
    "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-alpha.2.uf2",
    "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-alpha.3.uf2",
    "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-alpha.4.uf2",
    "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-alpha.5.uf2",
    "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-alpha.6.uf2",
    "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-alpha.7.uf2",
    "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-alpha.8.uf2",
    "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-beta.0.uf2",
    "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-beta.1.uf2",
    "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-beta.2.uf2",
    "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-beta.3.uf2",
    "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0-rc.0.uf2",
    "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.0.uf2",
    "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.1.uf2",
    "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.2.uf2",
    "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.0.3.uf2",
    "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.1.0-beta.0.uf2",
    "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.1.0-beta.1.uf2",
    "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.1.0-rc.1.uf2",
    "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.1.1.uf2",
    "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.1.2.uf2",
    "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.1.3.uf2",
    "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.1.4.uf2",
    "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.2.0-alpha.1.uf2",
    "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.2.0-rc.0.uf2",
    "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.2.0.uf2",
    "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.2.1.uf2",
    "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.3.0-alpha.1.uf2",
    "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.3.0-alpha.2.uf2",
    "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.3.0-alpha.3.uf2",
    "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-10.3.0-alpha.4.uf2",
    "6.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-6.3.0-rc.0.uf2",
    "6.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-6.3.0.uf2",
    "7.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-alpha.2.uf2",
    "7.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-alpha.3.uf2",
    "7.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-alpha.4.uf2",
    "7.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-alpha.5.uf2",
    "7.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-alpha.6.uf2",
    "7.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-beta.0.uf2",
    "7.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-rc.0.uf2",
    "7.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-rc.1.uf2",
    "7.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-rc.2.uf2",
    "7.0.0-rc.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0-rc.3.uf2",
    "7.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.0.0.uf2",
    "7.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.0-beta.0.uf2",
    "7.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.0-beta.1.uf2",
    "7.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.0-beta.2.uf2",
    "7.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.0-beta.3.uf2",
    "7.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.0-rc.0.uf2",
    "7.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.0-rc.1.uf2",
    "7.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.0.uf2",
    "7.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.1.1.uf2",
    "7.2.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.0-alpha.0.uf2",
    "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.0-alpha.1.uf2",
    "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.0-alpha.2.uf2",
    "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.0-rc.0.uf2",
    "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.0-rc.2.uf2",
    "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.0.uf2",
    "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.1.uf2",
    "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.2.uf2",
    "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.3.uf2",
    "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.4.uf2",
    "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.2.5.uf2",
    "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0-alpha.0.uf2",
    "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0-beta.0.uf2",
    "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0-beta.1.uf2",
    "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0-beta.2.uf2",
    "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0-rc.0.uf2",
    "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0-rc.1.uf2",
    "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0-rc.2.uf2",
    "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.0.uf2",
    "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.1.uf2",
    "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.2.uf2",
    "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-7.3.3.uf2",
    "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-alpha.0.uf2",
    "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-alpha.1.uf2",
    "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-beta.0.uf2",
    "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-beta.1.uf2",
    "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-beta.2.uf2",
    "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-beta.3.uf2",
    "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-beta.4.uf2",
    "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-beta.5.uf2",
    "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-beta.6.uf2",
    "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-rc.0.uf2",
    "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-rc.1.uf2",
    "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0-rc.2.uf2",
    "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.0.uf2",
    "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.2.uf2",
    "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.3.uf2",
    "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.4.uf2",
    "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.0.5.uf2",
    "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.1.0-beta.0.uf2",
    "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.1.0-beta.1.uf2",
    "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.1.0-beta.2.uf2",
    "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.1.0-rc.0.uf2",
    "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.1.0.uf2",
    "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.0-beta.0.uf2",
    "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.0-beta.1.uf2",
    "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.0-rc.0.uf2",
    "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.0-rc.1.uf2",
    "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.0.uf2",
    "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.1.uf2",
    "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.10.uf2",
    "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.2.uf2",
    "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.3.uf2",
    "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.4.uf2",
    "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.5.uf2",
    "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.6.uf2",
    "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.7.uf2",
    "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.8.uf2",
    "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-8.2.9.uf2",
    "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-alpha.2.uf2",
    "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-alpha.4.uf2",
    "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-alpha.5.uf2",
    "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-alpha.6.uf2",
    "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-beta.0.uf2",
    "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-beta.1.uf2",
    "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-beta.2.uf2",
    "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-rc.0.uf2",
    "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0-rc.1.uf2",
    "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.0.uf2",
    "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.1.uf2",
    "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.2.uf2",
    "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.3.uf2",
    "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.4.uf2",
    "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.0.5.uf2",
    "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.0-beta.0.uf2",
    "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.0-beta.1.uf2",
    "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.0-beta.2.uf2",
    "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.0-beta.3.uf2",
    "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.0-beta.4.uf2",
    "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.0-rc.0.uf2",
    "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.0.uf2",
    "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.1.uf2",
    "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.2.uf2",
    "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.3.uf2",
    "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.1.4.uf2",
    "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.0-alpha.2350.uf2",
    "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.0-alpha.2351.uf2",
    "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.0-beta.0.uf2",
    "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.0-beta.1.uf2",
    "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.0-rc.0.uf2",
    "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.0.uf2",
    "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.1.uf2",
    "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.2.uf2",
    "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.3.uf2",
    "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.4.uf2",
    "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.5.uf2",
    "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.6.uf2",
    "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.7.uf2",
    "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.8.uf2",
    "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_4mb/en_US/adafruit-circuitpython-pimoroni_picolipo_4mb-en_US-9.2.9.uf2",
}

_CIRCUITPYTHON_16MB: dict[str, str] = {
    "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-alpha.2.uf2",
    "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-alpha.3.uf2",
    "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-alpha.4.uf2",
    "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-alpha.5.uf2",
    "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-alpha.6.uf2",
    "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-alpha.7.uf2",
    "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-alpha.8.uf2",
    "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-beta.0.uf2",
    "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-beta.1.uf2",
    "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-beta.2.uf2",
    "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-beta.3.uf2",
    "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0-rc.0.uf2",
    "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.0.uf2",
    "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.1.uf2",
    "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.2.uf2",
    "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.0.3.uf2",
    "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.1.0-beta.0.uf2",
    "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.1.0-beta.1.uf2",
    "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.1.0-rc.1.uf2",
    "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.1.1.uf2",
    "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.1.3.uf2",
    "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.1.4.uf2",
    "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.2.0-alpha.1.uf2",
    "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.2.0-rc.0.uf2",
    "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.2.0.uf2",
    "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.2.1.uf2",
    "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.3.0-alpha.1.uf2",
    "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.3.0-alpha.2.uf2",
    "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.3.0-alpha.3.uf2",
    "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-10.3.0-alpha.4.uf2",
    "6.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-6.3.0-rc.0.uf2",
    "6.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-6.3.0.uf2",
    "7.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-alpha.2.uf2",
    "7.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-alpha.3.uf2",
    "7.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-alpha.4.uf2",
    "7.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-alpha.5.uf2",
    "7.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-alpha.6.uf2",
    "7.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-beta.0.uf2",
    "7.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-rc.0.uf2",
    "7.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-rc.1.uf2",
    "7.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-rc.2.uf2",
    "7.0.0-rc.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0-rc.3.uf2",
    "7.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.0.0.uf2",
    "7.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.0-beta.0.uf2",
    "7.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.0-beta.1.uf2",
    "7.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.0-beta.2.uf2",
    "7.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.0-beta.3.uf2",
    "7.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.0-rc.0.uf2",
    "7.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.0-rc.1.uf2",
    "7.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.0.uf2",
    "7.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.1.1.uf2",
    "7.2.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.0-alpha.0.uf2",
    "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.0-alpha.1.uf2",
    "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.0-alpha.2.uf2",
    "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.0-rc.0.uf2",
    "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.0-rc.2.uf2",
    "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.0.uf2",
    "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.1.uf2",
    "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.2.uf2",
    "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.3.uf2",
    "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.4.uf2",
    "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.2.5.uf2",
    "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0-alpha.0.uf2",
    "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0-beta.0.uf2",
    "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0-beta.1.uf2",
    "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0-beta.2.uf2",
    "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0-rc.0.uf2",
    "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0-rc.1.uf2",
    "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0-rc.2.uf2",
    "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.0.uf2",
    "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.1.uf2",
    "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.2.uf2",
    "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-7.3.3.uf2",
    "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-alpha.0.uf2",
    "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-alpha.1.uf2",
    "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-beta.0.uf2",
    "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-beta.1.uf2",
    "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-beta.2.uf2",
    "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-beta.3.uf2",
    "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-beta.4.uf2",
    "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-beta.5.uf2",
    "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-beta.6.uf2",
    "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-rc.0.uf2",
    "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-rc.1.uf2",
    "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0-rc.2.uf2",
    "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.0.uf2",
    "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.2.uf2",
    "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.3.uf2",
    "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.4.uf2",
    "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.0.5.uf2",
    "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.1.0-beta.0.uf2",
    "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.1.0-beta.1.uf2",
    "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.1.0-beta.2.uf2",
    "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.1.0-rc.0.uf2",
    "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.1.0.uf2",
    "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.0-beta.0.uf2",
    "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.0-beta.1.uf2",
    "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.0-rc.0.uf2",
    "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.0-rc.1.uf2",
    "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.0.uf2",
    "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.1.uf2",
    "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.10.uf2",
    "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.2.uf2",
    "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.3.uf2",
    "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.4.uf2",
    "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.5.uf2",
    "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.6.uf2",
    "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.7.uf2",
    "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.8.uf2",
    "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-8.2.9.uf2",
    "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-alpha.2.uf2",
    "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-alpha.4.uf2",
    "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-alpha.5.uf2",
    "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-alpha.6.uf2",
    "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-beta.0.uf2",
    "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-beta.1.uf2",
    "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-beta.2.uf2",
    "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-rc.0.uf2",
    "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0-rc.1.uf2",
    "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.0.uf2",
    "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.1.uf2",
    "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.2.uf2",
    "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.3.uf2",
    "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.4.uf2",
    "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.0.5.uf2",
    "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.0-beta.0.uf2",
    "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.0-beta.1.uf2",
    "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.0-beta.2.uf2",
    "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.0-beta.3.uf2",
    "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.0-beta.4.uf2",
    "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.0-rc.0.uf2",
    "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.0.uf2",
    "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.1.uf2",
    "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.2.uf2",
    "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.3.uf2",
    "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.1.4.uf2",
    "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.0-alpha.2350.uf2",
    "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.0-alpha.2351.uf2",
    "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.0-beta.0.uf2",
    "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.0-beta.1.uf2",
    "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.0-rc.0.uf2",
    "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.0.uf2",
    "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.1.uf2",
    "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.2.uf2",
    "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.3.uf2",
    "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.4.uf2",
    "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.5.uf2",
    "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.6.uf2",
    "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.7.uf2",
    "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.8.uf2",
    "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_picolipo_16mb/en_US/adafruit-circuitpython-pimoroni_picolipo_16mb-en_US-9.2.9.uf2",
}

BOARD = BoardSpec(
    extras=_EXTRAS,
    firmware={
        "micropython": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw=_MICROPYTHON_4MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 768, "fs_blocksize": 4096},
        ),
        "circuitpython": BoardFirmwareSpec(
            default_tag="10.2.1",
            fw=_CIRCUITPYTHON_4MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
        ),
    },
)

BOARD_16MB = BoardSpec(
    extras=_EXTRAS,
    firmware={
        "micropython": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw=_MICROPYTHON_16MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 3840, "fs_blocksize": 4096},
        ),
        "circuitpython": BoardFirmwareSpec(
            default_tag="10.2.1",
            fw=_CIRCUITPYTHON_16MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
        ),
    },
)
