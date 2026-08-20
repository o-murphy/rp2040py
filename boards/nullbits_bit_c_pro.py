"""BoardSpec definition for the **nullbits Bit-C PRO** (https://nullbits.co/bit-c-pro) - a
Pico-class RP2040 board with a bigger 4 MiB flash part (vs. a Pico's 2 MiB) and an **RGB LED as
three separate active-low GPIO LEDs** - not a WS2812/NeoPixel, despite `board.json`'s own "RGB LED"
feature tag: red on **GPIO16**, green on **GPIO17**, blue on **GPIO18**. Built as a worked
`--board-spec` example, picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --board-spec boards/nullbits_bit_c_pro.py:BOARD -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/nullbits_bit_c_pro.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.nullbits_bit_c_pro:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `nullbits_bit_c_pro.py` after both firmwares' own board id - MicroPython's
`ports/rp2/boards/NULLBITS_BIT_C_PRO`, case-normalized, and CircuitPython's own
`nullbits_bit_c_pro` (they agree here, unlike `boards/weactstudio/`).

Every number below is derived from a local checkout of both upstream ports at their current tags
(docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/NULLBITS_BIT_C_PRO/`: `pins.csv` (`LED,GPIO16` / `LED_RED,GPIO16`
  / `LED_GREEN,GPIO17` / `LED_BLUE,GPIO18`), `mpconfigboard.h`
  (`MICROPY_HW_BOARD_NAME "nullbits Bit-C PRO"`, its own comment `// RGB LED, active low` /
  `// Red LED 16` / `// Green LED 17` / `// Blue LED 18`, and
  `MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - (1 * 512 * 1024))` - a formula, not a
  literal, resolved below). The pico-sdk board header
  `lib/pico-sdk/src/boards/include/boards/nullbits_bit_c_pro.h`: `BIT_C_PRO_LED_R_PIN 16`/
  `_G_PIN 17`/`_B_PIN 18`, `PICO_DEFAULT_LED_PIN_INVERTED 1` (confirming active-low), an explicit
  `// no PICO_DEFAULT_WS2812_PIN` comment, and `PICO_FLASH_SIZE_BYTES (4 * 1024 * 1024)` (the
  header's own comment: `// Bit-C PRO has 4MB SPI flash`).
- CircuitPython, `ports/raspberrypi/boards/nullbits_bit_c_pro/`: `mpconfigboard.h`
  (`CIRCUITPY_RGB_STATUS_INVERTED_PWM`, `CIRCUITPY_RGB_STATUS_R/_G/_B` -> GPIO16/17/18, agreeing
  with MicroPython on every pin and the active-low polarity), `mpconfigboard.mk`
  (`EXTERNAL_FLASH_DEVICES = "GD25Q32C"` - 32 Mbit = 4 MiB, agreeing with the pico-sdk header), and
  `pins.c` (`LED_RED`/`LED_GREEN`/`LED_BLUE` -> GPIO16/17/18, `LED` -> GPIO18 (blue), no
  `NEOPIXEL` entry at all - confirming no addressable LED). No board-specific `link.ld` (confirmed
  absent from the directory listing), so `ports/raspberrypi/link-rp2040.ld`'s default
  `firmware_size = 1020K` applies, same derivation every other board file in this project already
  documents.

Flash geometry, MicroPython: `MICROPY_HW_FLASH_STORAGE_BYTES = PICO_FLASH_SIZE_BYTES - 512 KiB = 4
MiB - 512 KiB = 3584 KiB`, so `fs_start = 4 MiB - 3584 KiB = 0x80000`, `fs_blockcount = 3584 KiB /
4 KiB = 896` (docs/records/0035's derivation) - a genuinely different split from every other board
in this project so far, since this is the first one whose `MICROPY_HW_FLASH_STORAGE_BYTES` is
itself a formula off `PICO_FLASH_SIZE_BYTES` rather than a fixed literal (still resolves to a fixed
number for this one physical flash chip). CircuitPython's own start is the generic
`firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 0x100000`, `fs_blockcount = 512` following this
project's existing CircuitPython convention.

Onboard extras:

- The RGB LED, as three plain active-low LEDs (not a single `Ws2812` - see above):
  `LEDMock(gpio=16, active_low=True)` (red), `LEDMock(gpio=17, active_low=True)` (green),
  `LEDMock(gpio=18, active_low=True)` (blue). `active_low` was added to `LEDMock` for
  `boards/machdyne_werkzeug/` (docs/records/0074); this board needed it on all three LEDs at
  once, confirmed active-low by both firmware ports independently rather than assumed from that
  precedent alone. Live-boot-verified that CircuitPython drives all three as its own status
  indicator from boot (`CIRCUITPY_RGB_STATUS_INVERTED_PWM` - a PWM-driven status LED, the
  non-NeoPixel equivalent of the pattern `vcc_gnd_yd_rp2040`/`waveshare_rp2040_zero`'s WS2812
  status LEDs already showed): GPIO16/17 (red/green) toggled tens of thousands of times during one
  measured boot, GPIO18 (blue) 8 times - so each `LEDMock.on`/`.toggle_count` reflects real
  activity with no guest code at all. MicroPython's build declares no such default.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- Not modelled: any **RESET** button. A `ResetButton` exists since docs/records/0089's Phase 4, so
  the question is now only whether this board has one - and nothing sourceable says it does.
  Re-checked 2026-08-20: nullbits' own product page text mentions no buttons at all (only the UF2
  bootloader), and its linked pinout is an image this project cannot read as a citation. Left off
  rather than inferred from the fact that most RP2040 boards have one. Also not modelled: USB-C, and the RP2040's own RTC (not
  board-specific).
"""

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "LED_BLUE_GPIO", "LED_GREEN_GPIO", "LED_RED_GPIO")

LED_RED_GPIO = 16
LED_GREEN_GPIO = 17
LED_BLUE_GPIO = 18

_EXTRAS = (
    lambda: LEDMock(gpio=LED_RED_GPIO, active_low=True),
    lambda: LEDMock(gpio=LED_GREEN_GPIO, active_low=True),
    lambda: LEDMock(gpio=LED_BLUE_GPIO, active_low=True),
    BootselButton,
)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug NULLBITS_BIT_C_PRO
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug nullbits_bit_c_pro
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20260406-v1.28.0.uf2",
            "1.27.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20251209-v1.27.0.uf2",
            "1.26.1": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20250911-v1.26.1.uf2",
            "1.26.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20250809-v1.26.0.uf2",
            "1.25.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20250415-v1.25.0.uf2",
            "1.24.1": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20241129-v1.24.1.uf2",
            "1.24.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20241025-v1.24.0.uf2",
            "1.23.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20240602-v1.23.0.uf2",
            "1.22.2": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20240222-v1.22.2.uf2",
            "1.22.1": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20240105-v1.22.1.uf2",
            "1.22.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20231227-v1.22.0.uf2",
            "1.21.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20231005-v1.21.0.uf2",
            "1.20.0": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20230426-v1.20.0.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/NULLBITS_BIT_C_PRO-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        },
        layout={"fs_start": "0x80000", "fs_blockcount": 896, "fs_blocksize": 4096},
    ),
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.1.1.uf2",
            "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.1.2.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-10.3.0-alpha.4.uf2",
            "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.0-rc.0.uf2",
            "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.0-rc.1.uf2",
            "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.0-rc.2.uf2",
            "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.0.uf2",
            "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.2.uf2",
            "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.3.uf2",
            "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.4.uf2",
            "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.0.5.uf2",
            "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.1.0-beta.0.uf2",
            "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.1.0-beta.1.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/nullbits_bit_c_pro/en_US/adafruit-circuitpython-nullbits_bit_c_pro-en_US-9.2.9.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}

BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
