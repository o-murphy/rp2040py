"""BoardSpec definitions for the **Pimoroni Tiny 2040** (https://shop.pimoroni.com/products/tiny-2040)
- a postage-stamp RP2040 board with an **RGB LED as three separate active-low GPIO LEDs** (not a
WS2812/NeoPixel, despite `board.json`'s own "RGB LED" feature tag) in two flash-size variants:
**2 MiB** (the default, unnamed variant upstream) and **8 MiB** (`FLASH_8M`). Built as a worked
`--board-spec` example, picked up off [0066](../docs/records/0066-board-support-expansion.md)'s
survey; load it with e.g.:

    rp2040py micropython --board-spec boards/pimoroni_tiny2040.py:BOARD -c "<probe>"
    rp2040py micropython --board-spec boards/pimoroni_tiny2040.py:BOARD_8MB -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/pimoroni_tiny2040.py:BOARD
    PYTHONPATH=. rp2040py micropython --board-spec boards.pimoroni_tiny2040:BOARD_8MB ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

A single flat file, not a directory - `BOARD`/`BOARD_8MB` are the two variants, the same shape
`boards/pimoroni_picolipo.py`'s `BOARD`/`BOARD_16MB` already established. Every device this board
uses (`LEDMock`, `BootselButton`) already lives in `rp2040py.external`, so nothing here needs a
package.

Every number below is derived from a local checkout of both upstream ports at their current tags
(docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/PIMORONI_TINY2040/`: `board.json` (`"variants": {"FLASH_8M": "8
  MiB Flash"}` - confirms the unnamed/default variant is the *2 MiB* one, not the 8 MiB one the
  product page leads with), `mpconfigboard.h` (USB VID `0x16D0`/PID `0x08C7`, shared by both
  variants; `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 * 1024 * 1024))` - a
  formula, not a literal, resolved per-variant below), `mpconfigvariant.cmake` (the *default*
  build: `set(PICO_BOARD "pimoroni_tiny2040_2mb")`, board name override `"Pimoroni Tiny 2040
  2MB"`), `mpconfigvariant_FLASH_8M.cmake` (`set(PICO_BOARD "pimoroni_tiny2040")`, name
  `"Pimoroni Tiny 2040 8MB"`), `pins.csv` (`LED_RED,GPIO18` / `LED_GREEN,GPIO19` /
  `LED_BLUE,GPIO20` / `LED,GPIO19` (green aliased as the generic LED) / `BOOT,GPIO23` - `BOOT`
  here is `USER_SW`, a real GPIO pushbutton, not the QSPI-SS BOOTSEL pad `BootselButton` already
  models). The pico-sdk board headers `lib/pico-sdk/src/boards/include/boards/pimoroni_tiny2040{,_2mb}.h`:
  `TINY2040_LED_R_PIN 18`/`_G_PIN 19`/`_B_PIN 20`, `TINY2040_USER_SW_PIN 23`,
  `PICO_DEFAULT_LED_PIN_INVERTED 1` (confirming active-low, identical in both headers), an
  explicit `// no PICO_DEFAULT_WS2812_PIN` comment, and `PICO_FLASH_SIZE_BYTES` `8`/`2` MiB
  respectively - otherwise byte-for-byte identical between the two variants.
- CircuitPython, `ports/raspberrypi/boards/pimoroni_tiny2040{,_2mb}/`: `pins.c` agrees on every
  pin (`LED_R`/`LED_G`/`LED_B` -> GPIO18/19/20, `USER_SW`/`BUTTON` -> GPIO23, no `NEOPIXEL` entry).
  `mpconfigboard.h`: `CIRCUITPY_RGB_STATUS_INVERTED_PWM` + `CIRCUITPY_RGB_STATUS_R/_G/_B` ->
  GPIO18/19/20 in both variants - the same "PWM-driven active-low RGB status LED" shape
  `nullbits_bit_c_pro` already showed, confirming active-low independently of the pico-sdk
  headers. `mpconfigboard.mk`: USB identity differs by variant - the 8 MiB build keeps Pimoroni's
  own `0x16D0`/`0x08C7` (matching MicroPython's), the 2 MiB build instead uses the Raspberry Pi
  Foundation's community VID `0x2E8A`/PID `0x1016` (a genuinely distinct USB device identity, not
  a copy-paste - unlike `pimoroni_picolipo` where both variants share one VID and differ only in
  PID). `EXTERNAL_FLASH_DEVICES` `"W25Q64JVxQ"` (64 Mbit = 8 MiB) / `"W25Q16JVxQ"` (16 Mbit = 2
  MiB), agreeing with the pico-sdk headers' flash sizes. No board-specific `link.ld` for either
  variant, so `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies, same
  derivation every other board file in this project already documents.

**`USER_SW` (GPIO23) is not modelled**, the same gap `pimoroni_picolipo`'s `USER_SW` already
documents and for the same reason: Pimoroni's own product page calls it "Switch for basic input
(doubles up as DFU select on boot)", but neither firmware port's source states a pull direction or
confirms the DFU-select claim's actual electrical mechanism, and a search of Pimoroni's own C++ SDK
(`pimoroni-pico`) for `TINY2040_USER_SW_PIN` usage turned up nothing that settles it either. Per the
3g rule, this stays a stated gap rather than a guess.

Flash geometry, MicroPython (`fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES`,
0035's derivation): both variants reserve exactly 1 MiB for firmware code
(`MICROPY_HW_FLASH_STORAGE_BYTES = PICO_FLASH_SIZE_BYTES - 1 MiB`, true for both), so
`fs_start = 0x100000` for both - only `fs_blockcount` changes with the flash chip's real size
(same shape `boards/pimoroni_picolipo.py`'s own docstring documents for its two variants):

    variant   PICO_FLASH_SIZE_BYTES   MICROPY_HW_FLASH_STORAGE_BYTES   fs_blockcount (/4096)
    2 MiB     2 MiB   (0x200000)      1 MiB  (0x100000)                256
    8 MiB     8 MiB   (0x800000)      7 MiB  (0x700000)                1792

CircuitPython's own start is the generic `firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 0x100000`
for both variants, `fs_blockcount = 512` following this project's existing CircuitPython
convention (0035: only the *start* has to be right, since the emulated flash buffer is 16 MiB).

Onboard extras (identical between variants - only flash size and firmware differ):

- The RGB LED, as three plain active-low LEDs (not a single `Ws2812` - see above):
  `LEDMock(gpio=18, active_low=True)` (red), `LEDMock(gpio=19, active_low=True)` (green),
  `LEDMock(gpio=20, active_low=True)` (blue) - same `active_low` shape `nullbits_bit_c_pro` uses,
  confirmed active-low by both firmware ports independently.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051) - the real BOOTSEL mechanism, independent of `USER_SW`.
- RESET: `ResetButton` (`rp2040py.external.reset_button`). `pins.csv` lists no RESET net, and it
  never would - RUN is not a GPIO - so the source for this one is Pimoroni's own product page:
  "We've also managed to fit in a programmable RGB LED, a reset button and some clever circuitry".
  That the firmware config is silent is not evidence of absence here, which is worth stating
  because this file previously read as if it were. Modelled since docs/records/0089's Phase 4
  (which closes docs/records/0057).
- Not modelled: `USER_SW`/GPIO23 (see above), USB-C, and the
  RP2040's own RTC (not board-specific).
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.reset_button import ResetButton
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "BOARD_8MB", "LED_B_GPIO", "LED_G_GPIO", "LED_R_GPIO")

LED_R_GPIO = 18
LED_G_GPIO = 19
LED_B_GPIO = 20

_EXTRAS = (
    lambda: LEDMock(gpio=LED_R_GPIO, active_low=True),
    lambda: LEDMock(gpio=LED_G_GPIO, active_low=True),
    lambda: LEDMock(gpio=LED_B_GPIO, active_low=True),
    BootselButton,
    ResetButton,
)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug PIMORONI_TINY2040 --page PIMORONI_TINY2040
#   uv run scripts/fetch_firmware.py list --family micropython --slug PIMORONI_TINY2040-FLASH_8M --page PIMORONI_TINY2040
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug pimoroni_tiny2040_2mb
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug pimoroni_tiny2040
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
_MICROPYTHON_2MB: dict[str, str] = {
    "1.28.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20260406-v1.28.0.uf2",
    "1.27.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20251209-v1.27.0.uf2",
    "1.26.1": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20250911-v1.26.1.uf2",
    "1.26.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20250809-v1.26.0.uf2",
    "1.25.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20250415-v1.25.0.uf2",
    "1.24.1": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20241129-v1.24.1.uf2",
    "1.24.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20241025-v1.24.0.uf2",
    "1.23.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20240602-v1.23.0.uf2",
    "1.22.2": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20240222-v1.22.2.uf2",
    "1.22.1": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20240105-v1.22.1.uf2",
    "1.22.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20231227-v1.22.0.uf2",
    "1.21.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20231005-v1.21.0.uf2",
    "1.20.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20230426-v1.20.0.uf2",
    "1.19.1": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20220618-v1.19.1.uf2",
    "1.18": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20220117-v1.18.uf2",
    "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
    "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20260813-v1.29.0-preview.707.g1827631282.uf2",
    "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
    "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
    "1.29.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-20260824-v1.29.0.uf2",
}

_MICROPYTHON_8MB: dict[str, str] = {
    "1.28.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20260406-v1.28.0.uf2",
    "1.27.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20251209-v1.27.0.uf2",
    "1.26.1": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20250911-v1.26.1.uf2",
    "1.26.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20250809-v1.26.0.uf2",
    "1.25.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20250415-v1.25.0.uf2",
    "1.24.1": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20241129-v1.24.1.uf2",
    "1.24.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20241025-v1.24.0.uf2",
    "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
    "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20260813-v1.29.0-preview.707.g1827631282.uf2",
    "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
    "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
    "1.29.0": "https://micropython.org/resources/firmware/PIMORONI_TINY2040-FLASH_8M-20260824-v1.29.0.uf2",
}

_CIRCUITPYTHON_2MB: dict[str, str] = {
    "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-alpha.2.uf2",
    "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-alpha.3.uf2",
    "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-alpha.4.uf2",
    "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-alpha.5.uf2",
    "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-alpha.6.uf2",
    "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-alpha.7.uf2",
    "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-alpha.8.uf2",
    "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-beta.0.uf2",
    "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-beta.1.uf2",
    "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-beta.2.uf2",
    "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-beta.3.uf2",
    "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0-rc.0.uf2",
    "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.0.uf2",
    "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.1.uf2",
    "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.2.uf2",
    "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.0.3.uf2",
    "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.1.0-beta.0.uf2",
    "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.1.0-beta.1.uf2",
    "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.1.0-rc.1.uf2",
    "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.1.1.uf2",
    "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.1.3.uf2",
    "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.1.4.uf2",
    "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.2.0-alpha.1.uf2",
    "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.2.0-rc.0.uf2",
    "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.2.0.uf2",
    "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.2.1.uf2",
    "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.3.0-alpha.1.uf2",
    "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.3.0-alpha.2.uf2",
    "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.3.0-alpha.3.uf2",
    "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-10.3.0-alpha.4.uf2",
    "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.0-alpha.1.uf2",
    "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.0-alpha.2.uf2",
    "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.0-rc.0.uf2",
    "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.0-rc.2.uf2",
    "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.0.uf2",
    "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.1.uf2",
    "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.2.uf2",
    "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.3.uf2",
    "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.4.uf2",
    "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.2.5.uf2",
    "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0-alpha.0.uf2",
    "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0-beta.0.uf2",
    "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0-beta.1.uf2",
    "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0-beta.2.uf2",
    "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0-rc.0.uf2",
    "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0-rc.1.uf2",
    "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0-rc.2.uf2",
    "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.0.uf2",
    "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.1.uf2",
    "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.2.uf2",
    "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-7.3.3.uf2",
    "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-alpha.0.uf2",
    "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-alpha.1.uf2",
    "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-beta.0.uf2",
    "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-beta.1.uf2",
    "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-beta.2.uf2",
    "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-beta.3.uf2",
    "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-beta.4.uf2",
    "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-beta.5.uf2",
    "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-beta.6.uf2",
    "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-rc.0.uf2",
    "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-rc.1.uf2",
    "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0-rc.2.uf2",
    "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.0.uf2",
    "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.2.uf2",
    "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.3.uf2",
    "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.4.uf2",
    "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.0.5.uf2",
    "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.1.0-beta.0.uf2",
    "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.1.0-beta.1.uf2",
    "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.1.0-beta.2.uf2",
    "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.1.0-rc.0.uf2",
    "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.1.0.uf2",
    "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.0-beta.0.uf2",
    "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.0-beta.1.uf2",
    "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.0-rc.0.uf2",
    "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.0-rc.1.uf2",
    "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.0.uf2",
    "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.1.uf2",
    "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.10.uf2",
    "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.2.uf2",
    "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.3.uf2",
    "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.4.uf2",
    "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.5.uf2",
    "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.6.uf2",
    "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.7.uf2",
    "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.8.uf2",
    "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-8.2.9.uf2",
    "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-alpha.2.uf2",
    "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-alpha.4.uf2",
    "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-alpha.5.uf2",
    "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-alpha.6.uf2",
    "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-beta.0.uf2",
    "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-beta.1.uf2",
    "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-beta.2.uf2",
    "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-rc.0.uf2",
    "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0-rc.1.uf2",
    "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.0.uf2",
    "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.1.uf2",
    "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.2.uf2",
    "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.3.uf2",
    "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.4.uf2",
    "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.0.5.uf2",
    "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.0-beta.0.uf2",
    "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.0-beta.1.uf2",
    "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.0-beta.2.uf2",
    "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.0-beta.3.uf2",
    "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.0-beta.4.uf2",
    "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.0-rc.0.uf2",
    "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.0.uf2",
    "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.1.uf2",
    "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.2.uf2",
    "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.3.uf2",
    "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.1.4.uf2",
    "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.0-alpha.2350.uf2",
    "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.0-alpha.2351.uf2",
    "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.0-beta.0.uf2",
    "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.0-beta.1.uf2",
    "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.0-rc.0.uf2",
    "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.0.uf2",
    "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.1.uf2",
    "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.2.uf2",
    "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.3.uf2",
    "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.4.uf2",
    "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.5.uf2",
    "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.6.uf2",
    "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.7.uf2",
    "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.8.uf2",
    "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040_2mb/en_US/adafruit-circuitpython-pimoroni_tiny2040_2mb-en_US-9.2.9.uf2",
}

_CIRCUITPYTHON_8MB: dict[str, str] = {
    "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-alpha.2.uf2",
    "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-alpha.3.uf2",
    "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-alpha.4.uf2",
    "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-alpha.5.uf2",
    "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-alpha.6.uf2",
    "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-alpha.7.uf2",
    "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-alpha.8.uf2",
    "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-beta.0.uf2",
    "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-beta.1.uf2",
    "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-beta.2.uf2",
    "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-beta.3.uf2",
    "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0-rc.0.uf2",
    "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.0.uf2",
    "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.1.uf2",
    "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.2.uf2",
    "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.0.3.uf2",
    "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.1.0-beta.0.uf2",
    "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.1.0-beta.1.uf2",
    "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.1.0-rc.1.uf2",
    "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.1.1.uf2",
    "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.1.2.uf2",
    "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.1.3.uf2",
    "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.1.4.uf2",
    "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.2.0-alpha.1.uf2",
    "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.2.0-rc.0.uf2",
    "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.2.0.uf2",
    "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.2.1.uf2",
    "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.3.0-alpha.1.uf2",
    "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.3.0-alpha.2.uf2",
    "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.3.0-alpha.3.uf2",
    "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-10.3.0-alpha.4.uf2",
    "6.2.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-6.2.0-beta.3.uf2",
    "6.2.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-6.2.0-beta.4.uf2",
    "6.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-6.2.0-rc.0.uf2",
    "6.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-6.2.0.uf2",
    "6.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-6.3.0-rc.0.uf2",
    "6.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-6.3.0.uf2",
    "7.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-alpha.1.uf2",
    "7.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-alpha.2.uf2",
    "7.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-alpha.3.uf2",
    "7.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-alpha.4.uf2",
    "7.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-alpha.5.uf2",
    "7.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-alpha.6.uf2",
    "7.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-beta.0.uf2",
    "7.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-rc.0.uf2",
    "7.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-rc.1.uf2",
    "7.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-rc.2.uf2",
    "7.0.0-rc.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0-rc.3.uf2",
    "7.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.0.0.uf2",
    "7.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.0-beta.0.uf2",
    "7.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.0-beta.1.uf2",
    "7.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.0-beta.2.uf2",
    "7.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.0-beta.3.uf2",
    "7.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.0-rc.0.uf2",
    "7.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.0-rc.1.uf2",
    "7.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.0.uf2",
    "7.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.1.1.uf2",
    "7.2.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.0-alpha.0.uf2",
    "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.0-alpha.1.uf2",
    "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.0-alpha.2.uf2",
    "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.0-rc.0.uf2",
    "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.0-rc.2.uf2",
    "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.0.uf2",
    "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.1.uf2",
    "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.2.uf2",
    "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.3.uf2",
    "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.4.uf2",
    "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.2.5.uf2",
    "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0-alpha.0.uf2",
    "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0-beta.0.uf2",
    "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0-beta.1.uf2",
    "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0-beta.2.uf2",
    "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0-rc.0.uf2",
    "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0-rc.1.uf2",
    "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0-rc.2.uf2",
    "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.0.uf2",
    "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.1.uf2",
    "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.2.uf2",
    "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-7.3.3.uf2",
    "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-alpha.0.uf2",
    "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-alpha.1.uf2",
    "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-beta.0.uf2",
    "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-beta.1.uf2",
    "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-beta.2.uf2",
    "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-beta.3.uf2",
    "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-beta.4.uf2",
    "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-beta.5.uf2",
    "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-beta.6.uf2",
    "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-rc.0.uf2",
    "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-rc.1.uf2",
    "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0-rc.2.uf2",
    "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.0.uf2",
    "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.2.uf2",
    "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.3.uf2",
    "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.4.uf2",
    "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.0.5.uf2",
    "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.1.0-beta.0.uf2",
    "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.1.0-beta.1.uf2",
    "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.1.0-beta.2.uf2",
    "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.1.0-rc.0.uf2",
    "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.1.0.uf2",
    "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.0-beta.0.uf2",
    "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.0-beta.1.uf2",
    "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.0-rc.0.uf2",
    "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.0-rc.1.uf2",
    "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.0.uf2",
    "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.1.uf2",
    "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.10.uf2",
    "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.2.uf2",
    "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.3.uf2",
    "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.4.uf2",
    "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.5.uf2",
    "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.6.uf2",
    "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.7.uf2",
    "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.8.uf2",
    "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-8.2.9.uf2",
    "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-alpha.2.uf2",
    "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-alpha.4.uf2",
    "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-alpha.5.uf2",
    "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-alpha.6.uf2",
    "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-beta.0.uf2",
    "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-beta.1.uf2",
    "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-beta.2.uf2",
    "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-rc.0.uf2",
    "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0-rc.1.uf2",
    "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.0.uf2",
    "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.1.uf2",
    "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.2.uf2",
    "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.3.uf2",
    "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.4.uf2",
    "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.0.5.uf2",
    "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.0-beta.0.uf2",
    "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.0-beta.1.uf2",
    "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.0-beta.2.uf2",
    "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.0-beta.3.uf2",
    "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.0-beta.4.uf2",
    "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.0-rc.0.uf2",
    "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.0.uf2",
    "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.1.uf2",
    "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.2.uf2",
    "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.3.uf2",
    "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.1.4.uf2",
    "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.0-alpha.2350.uf2",
    "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.0-alpha.2351.uf2",
    "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.0-beta.0.uf2",
    "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.0-beta.1.uf2",
    "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.0-rc.0.uf2",
    "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.0.uf2",
    "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.1.uf2",
    "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.2.uf2",
    "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.3.uf2",
    "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.4.uf2",
    "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.5.uf2",
    "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.6.uf2",
    "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.7.uf2",
    "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.8.uf2",
    "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/pimoroni_tiny2040/en_US/adafruit-circuitpython-pimoroni_tiny2040-en_US-9.2.9.uf2",
}

BOARD = BoardSpec(
    extras=_EXTRAS,
    firmware={
        "micropython": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw=_MICROPYTHON_2MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 256, "fs_blocksize": 4096},
        ),
        "circuitpython": BoardFirmwareSpec(
            default_tag="10.2.1",
            fw=_CIRCUITPYTHON_2MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
        ),
    },
)

BOARD_8MB = BoardSpec(
    extras=_EXTRAS,
    firmware={
        "micropython": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw=_MICROPYTHON_8MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 1792, "fs_blocksize": 4096},
        ),
        "circuitpython": BoardFirmwareSpec(
            default_tag="10.2.1",
            fw=_CIRCUITPYTHON_8MB,
            layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
        ),
    },
)
