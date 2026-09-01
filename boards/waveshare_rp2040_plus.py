"""BoardSpec definitions for the **Waveshare RP2040-Plus** (https://www.waveshare.com/rp2040-plus.htm)
- a Pico-class RP2040 board with onboard LiPo battery charging, in two flash-size variants: **4 MiB**
(default) and **16 MiB**. Built as a worked `--board-spec` example, picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --board-spec boards/waveshare_rp2040_plus.py:BOARD -c "<probe>"
    rp2040py micropython --board-spec boards/waveshare_rp2040_plus.py:BOARD_16MB -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/waveshare_rp2040_plus.py:BOARD
    PYTHONPATH=. rp2040py micropython --board-spec boards.waveshare_rp2040_plus:BOARD_16MB ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

A single flat file, not a directory - `BOARD`/`BOARD_16MB` are the two variants, the same shape
`boards/pimoroni_picolipo.py`'s `BOARD`/`BOARD_16MB` already established. Every device this board
uses (`LEDMock`, `BootselButton`) already lives in `rp2040py.external`, so nothing here needs a
package.

Every number below is derived from a local checkout of both upstream ports at their current tags
(docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/WAVESHARE_RP2040_PLUS/`: `mpconfigvariant.cmake` (the *default*
  build: `set(PICO_BOARD "waveshare_rp2040_plus_4mb")`) and `mpconfigvariant_FLASH_16M.cmake`
  (`set(PICO_BOARD "waveshare_rp2040_plus_16mb")`), `mpconfigboard.h` (USB VID `0x2E8A`/PID
  `0x1020`, shared by both variants - unlike `pimoroni_picolipo`/`pimoroni_tiny2040`, this board
  doesn't distinguish its two flash variants by USB identity at all;
  `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 * 1024 * 1024))` - the same formula
  shape those two boards already used). No `pins.csv` exists for this board at all - it uses the
  pico-sdk's own board-header pin defaults directly. The pico-sdk board headers
  `lib/pico-sdk/src/boards/include/boards/waveshare_rp2040_plus_{4,16}mb.h`: `PICO_DEFAULT_LED_PIN
  25` (no inversion macro - active-high), an explicit `// no PICO_DEFAULT_WS2812_PIN` comment,
  `PICO_SMPS_MODE_PIN 23` (an output-only pin firmware drives high to force the power supply into
  low-ripple PWM mode - not a discrete component), and `PICO_FLASH_SIZE_BYTES` 4/16 MiB
  respectively - otherwise byte-for-byte identical between the two variants.
- CircuitPython, `ports/raspberrypi/boards/waveshare_rp2040_plus_{4,16}mb/`: `pins.c` agrees on
  every pin (`LED` -> GPIO25, `SMPS_MODE` -> GPIO23, `VBUS_SENSE` -> GPIO24, `VOLTAGE_MONITOR`/`A3`
  -> GPIO29 - a battery-voltage ADC divider, matching `board.json`'s "Battery Charging" feature tag
  - no `NEOPIXEL` entry, no `USER_SW`/`BUTTON` entry at all - this board has no pushbutton beyond
  BOOTSEL, unlike `pimoroni_picolipo`/`pimoroni_tiny2040`). `mpconfigboard.h` declares no
  `CIRCUITPY_RGB_STATUS_*` macros (unlike `nullbits_bit_c_pro`/`pimoroni_tiny2040`) - CircuitPython
  doesn't drive this LED as its own status indicator. `mpconfigboard.mk`: USB VID/PID
  `0x2E8A`/`0x1020` for *both* variants (confirming the MicroPython-side observation above),
  `EXTERNAL_FLASH_DEVICES` `"W25Q32JVxQ"` (32 Mbit = 4 MiB) / `"W25Q128JVxQ"` (128 Mbit = 16 MiB),
  agreeing with the pico-sdk headers' flash sizes. No board-specific `link.ld` for either variant
  (confirmed absent from both directory listings), so `ports/raspberrypi/link-rp2040.ld`'s default
  `firmware_size = 1020K` applies, same derivation every other board file in this project already
  documents.

Flash geometry, MicroPython (`fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES`,
0035's derivation): both variants reserve exactly 1 MiB for firmware code, so `fs_start = 0x100000`
for both - only `fs_blockcount` changes with the flash chip's real size (numerically identical to
`pimoroni_picolipo`'s own table, since both boards use the same 4 MiB/16 MiB flash chips with the
same 1 MiB firmware reservation):

    variant   PICO_FLASH_SIZE_BYTES   MICROPY_HW_FLASH_STORAGE_BYTES   fs_blockcount (/4096)
    4 MiB     4 MiB   (0x400000)      3 MiB  (0x300000)                768
    16 MiB    16 MiB  (0x1000000)     15 MiB (0xf00000)                3840

CircuitPython's own start is the generic `firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 0x100000`
for both variants, `fs_blockcount = 512` following this project's existing CircuitPython convention
(0035: only the *start* has to be right, since the emulated flash buffer is 16 MiB).

Onboard extras (identical between variants - only flash size and firmware differ):

- The LED: `LEDMock(gpio=25)` (`rp2040py.external.led_mock`) - a plain LED, active-high (no
  `PICO_DEFAULT_LED_PIN_INVERTED` in either pico-sdk header), not a NeoPixel.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051) - this board's only pushbutton, real or emulated; there is no separate
  `USER_SW`.
- Not modelled: `SMPS_MODE`/GPIO23 (an output pin controlling the onboard switching regulator's
  ripple mode - not a discrete component with observable behavior to emulate), `VBUS_SENSE`/GPIO24
  and `VOLTAGE_MONITOR`/GPIO29 (real ADC inputs this board exposes for USB-power and battery-voltage
  sensing respectively, but nothing board-specific to model beyond the RP2040's own ADC), the
  RESET/power button, the onboard LiPo charge circuit and USB-C, and the RP2040's own RTC. On that
  button specifically: a `ResetButton` exists since docs/records/0089's Phase 4, so the blocker is
  no longer the emulator - it is that nothing sourceable says which control this board actually has.
  waveshare.com returns HTTP 403 to this project's fetches (re-checked 2026-08-20) and this file's
  own "RESET/power" phrasing is ambiguous between the two. It matters: `boards/pimoroni_picolipo.py`
  turned out to have a *power* button, which reboots by cutting power - a different reset cause
  (`HAD_POR`, not `HAD_RUN`). Guessing here would produce a board that lies about why it rebooted.
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "BOARD_16MB", "LED_GPIO")

LED_GPIO = 25

_EXTRAS = (lambda: LEDMock(gpio=LED_GPIO), BootselButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug WAVESHARE_RP2040_PLUS --page WAVESHARE_RP2040_PLUS
#   uv run scripts/fetch_firmware.py list --family micropython --slug WAVESHARE_RP2040_PLUS-FLASH_16M --page WAVESHARE_RP2040_PLUS
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug waveshare_rp2040_plus_4mb
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug waveshare_rp2040_plus_16mb
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
_MICROPYTHON_4MB: dict[str, str] = {
    "1.28.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260406-v1.28.0.uf2",
    "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
    "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260813-v1.29.0-preview.707.g1827631282.uf2",
    "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
    "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
    "1.29.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260824-v1.29.0.uf2",
    "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260831-v1.30.0-preview.24.g8162451850.uf2",
    "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260827-v1.30.0-preview.8.gf668077be2.uf2",
    "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
}

_MICROPYTHON_16MB: dict[str, str] = {
    "1.28.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260406-v1.28.0.uf2",
    "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
    "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260813-v1.29.0-preview.707.g1827631282.uf2",
    "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
    "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
    "1.29.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260824-v1.29.0.uf2",
    "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260831-v1.30.0-preview.24.g8162451850.uf2",
    "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260827-v1.30.0-preview.8.gf668077be2.uf2",
    "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_PLUS-FLASH_16M-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
}

_CIRCUITPYTHON_4MB: dict[str, str] = {
    "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-alpha.2.uf2",
    "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-alpha.3.uf2",
    "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-alpha.4.uf2",
    "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-alpha.5.uf2",
    "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-alpha.6.uf2",
    "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-alpha.7.uf2",
    "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-alpha.8.uf2",
    "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-beta.0.uf2",
    "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-beta.1.uf2",
    "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-beta.2.uf2",
    "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-beta.3.uf2",
    "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0-rc.0.uf2",
    "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.0.uf2",
    "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.1.uf2",
    "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.2.uf2",
    "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.0.3.uf2",
    "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.1.0-beta.0.uf2",
    "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.1.0-beta.1.uf2",
    "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.1.0-rc.1.uf2",
    "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.1.1.uf2",
    "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.1.2.uf2",
    "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.1.3.uf2",
    "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.1.4.uf2",
    "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.2.0-alpha.1.uf2",
    "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.2.0-rc.0.uf2",
    "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.2.0.uf2",
    "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.2.1.uf2",
    "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.3.0-alpha.1.uf2",
    "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.3.0-alpha.2.uf2",
    "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.3.0-alpha.3.uf2",
    "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.3.0-alpha.4.uf2",
    "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-alpha.2.uf2",
    "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-alpha.4.uf2",
    "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-alpha.5.uf2",
    "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-alpha.6.uf2",
    "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-beta.0.uf2",
    "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-beta.1.uf2",
    "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-beta.2.uf2",
    "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-rc.0.uf2",
    "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0-rc.1.uf2",
    "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.0.uf2",
    "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.1.uf2",
    "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.2.uf2",
    "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.3.uf2",
    "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.4.uf2",
    "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.0.5.uf2",
    "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.0-beta.0.uf2",
    "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.0-beta.1.uf2",
    "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.0-beta.2.uf2",
    "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.0-beta.3.uf2",
    "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.0-beta.4.uf2",
    "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.0-rc.0.uf2",
    "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.0.uf2",
    "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.1.uf2",
    "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.2.uf2",
    "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.3.uf2",
    "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.1.4.uf2",
    "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.0-alpha.2350.uf2",
    "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.0-alpha.2351.uf2",
    "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.0-beta.0.uf2",
    "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.0-beta.1.uf2",
    "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.0-rc.0.uf2",
    "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.0.uf2",
    "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.1.uf2",
    "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.2.uf2",
    "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.3.uf2",
    "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.4.uf2",
    "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.5.uf2",
    "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.6.uf2",
    "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.7.uf2",
    "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.8.uf2",
    "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-9.2.9.uf2",
    "10.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.3.0-rc.0.uf2",
    "10.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_4mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_4mb-en_US-10.3.0.uf2",
}

_CIRCUITPYTHON_16MB: dict[str, str] = {
    "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-alpha.2.uf2",
    "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-alpha.3.uf2",
    "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-alpha.4.uf2",
    "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-alpha.5.uf2",
    "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-alpha.6.uf2",
    "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-alpha.7.uf2",
    "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-alpha.8.uf2",
    "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-beta.0.uf2",
    "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-beta.1.uf2",
    "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-beta.2.uf2",
    "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-beta.3.uf2",
    "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0-rc.0.uf2",
    "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.0.uf2",
    "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.1.uf2",
    "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.2.uf2",
    "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.0.3.uf2",
    "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.1.0-beta.0.uf2",
    "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.1.0-beta.1.uf2",
    "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.1.0-rc.1.uf2",
    "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.1.1.uf2",
    "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.1.3.uf2",
    "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.1.4.uf2",
    "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.2.0-alpha.1.uf2",
    "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.2.0-rc.0.uf2",
    "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.2.0.uf2",
    "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.2.1.uf2",
    "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.3.0-alpha.1.uf2",
    "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.3.0-alpha.2.uf2",
    "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.3.0-alpha.3.uf2",
    "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.3.0-alpha.4.uf2",
    "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-alpha.2.uf2",
    "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-alpha.4.uf2",
    "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-alpha.5.uf2",
    "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-alpha.6.uf2",
    "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-beta.0.uf2",
    "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-beta.1.uf2",
    "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-beta.2.uf2",
    "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-rc.0.uf2",
    "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0-rc.1.uf2",
    "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.0.uf2",
    "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.1.uf2",
    "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.2.uf2",
    "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.3.uf2",
    "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.4.uf2",
    "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.0.5.uf2",
    "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.0-beta.0.uf2",
    "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.0-beta.1.uf2",
    "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.0-beta.2.uf2",
    "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.0-beta.3.uf2",
    "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.0-beta.4.uf2",
    "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.0-rc.0.uf2",
    "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.0.uf2",
    "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.1.uf2",
    "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.2.uf2",
    "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.3.uf2",
    "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.1.4.uf2",
    "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.0-alpha.2350.uf2",
    "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.0-alpha.2351.uf2",
    "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.0-beta.0.uf2",
    "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.0-beta.1.uf2",
    "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.0-rc.0.uf2",
    "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.0.uf2",
    "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.1.uf2",
    "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.2.uf2",
    "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.3.uf2",
    "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.4.uf2",
    "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.5.uf2",
    "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.6.uf2",
    "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.7.uf2",
    "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.8.uf2",
    "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-9.2.9.uf2",
    "10.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.3.0-rc.0.uf2",
    "10.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_plus_16mb/en_US/adafruit-circuitpython-waveshare_rp2040_plus_16mb-en_US-10.3.0.uf2",
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
