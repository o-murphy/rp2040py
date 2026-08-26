"""BoardSpec definition for the **SparkFun Pro Micro RP2040**
(https://www.sparkfun.com/products/18288) - a Pro-Micro-footprint RP2040 board with **no plain LED
at all** and a single **WS2812 RGB NeoPixel on GPIO25**, on the **largest flash part any board in
this project has so far** (16 MiB, 15 MiB of it filesystem under MicroPython). Built as a worked
`--board-spec` example, picked up off [0066](../docs/records/0066-board-support-expansion.md)'s
survey; load it with e.g.:

    rp2040py micropython --board-spec boards/sparkfun_promicro.py:BOARD -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/sparkfun_promicro.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.sparkfun_promicro:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `sparkfun_promicro.py` after **MicroPython's** own board id
(`ports/rp2/boards/SPARKFUN_PROMICRO`, case-normalized), which is also the pico-sdk board name
(`sparkfun_promicro.h`). The two firmware families disagree here, the same situation
`boards/weactstudio/` and `boards/seeed_xiao_rp2040.py` document: CircuitPython calls the same
board `sparkfun_pro_micro_rp2040`. Both ids are cited below next to the numbers each contributed.

Every number below is derived from a local checkout of both upstream ports at their current tags,
not guessed (docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/SPARKFUN_PROMICRO/`: `board.json` (features `"RGB LED"`/
  `"JST-SH"`/`"USB-C"`, mcu `rp2040`, product `"Pro Micro - RP2040"`, vendor `"SparkFun"`),
  `mpconfigboard.h` (`MICROPY_HW_BOARD_NAME "SparkFun Pro Micro RP2040"`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (15 * 1024 * 1024)`, USB VID `0x1B4F`/PID `0x0026`, UART1
  TX/RX/CTS/RTS 8/9/10/11, and its own closing comment
  `// NeoPixel data GPIO25, power not toggleable` - which is what rules out the power-enable pin
  `boards/seeed_xiao_rp2040.py`/`boards/adafruit_qtpy_rp2040.py` both have). **This board ships no
  `pins.csv` at all** - the directory holds only `board.json`, `mpconfigboard.cmake` and
  `mpconfigboard.h` - the first such board in this project; every pin below therefore comes from
  the pico-sdk header and CircuitPython's `pins.c`, not from a MicroPython pin table.
  `mpconfigboard.cmake` is a bare comment with **no `set(PICO_BOARD ...)`**, so
  `ports/rp2/CMakeLists.txt`'s own fallback applies (`if(NOT PICO_BOARD)` ->
  `string(TOLOWER ${MICROPY_BOARD} PICO_BOARD)`, lines 58-60), resolving the board header to
  `sparkfun_promicro` - derived, not assumed from the name matching.
- The pico-sdk board header `lib/pico-sdk/src/boards/include/boards/sparkfun_promicro.h`:
  `PICO_DEFAULT_WS2812_PIN 25` and, immediately above it, a *commented-out*
  `PICO_DEFAULT_LED_PIN 25` block under the header's own
  `// The PRO Micro doesn't have a plain LED, but a WS2812` - the absence of a plain LED is stated
  upstream, not merely inferred from a missing `#define`. Also `PICO_DEFAULT_I2C_SDA_PIN 16`/
  `_SCL_PIN 17` (`// Default I2C - for the onboard qwiic connector`), SPI0 SCK/TX/RX/CSN
  22/23/20/21, and `PICO_FLASH_SIZE_BYTES (16 * 1024 * 1024)` under `// board has 16M onboard
  flash`.
- CircuitPython, `ports/raspberrypi/boards/sparkfun_pro_micro_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_BOARD_NAME "SparkFun Pro Micro RP2040"`, `MICROPY_HW_NEOPIXEL (&pin_GPIO25)` -
  agreeing with the pico-sdk header's WS2812 pin - and no `CIRCUITPY_STATUS_LED_POWER`, agreeing
  with MicroPython's "power not toggleable" comment), `mpconfigboard.mk` (USB VID `0x1B4F`/PID
  `0x0026`, byte-identical to MicroPython's; `EXTERNAL_FLASH_DEVICES = "W25Q128JVxM"`, 128 Mbit =
  16 MiB, agreeing with the pico-sdk header), `pins.c` (`NEOPIXEL` -> GPIO25, `STEMMA_I2C` aliased
  onto the board I2C object for the Qwiic connector, and **no `LED` and no `BUTTON` entry at all** -
  independently confirming both the missing plain LED and the missing GPIO pushbutton), and
  `board.c` (no board-specific init, only the `MP_WEAK supervisor/shared/board.c` defaults). No
  board-specific `link.ld` (confirmed absent from the directory listing), so
  `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies, same derivation
  every other board file in this project already documents.

Flash geometry, MicroPython (`fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES`,
docs/records/0035's derivation): `16 MiB - 15 MiB = 0x100000`, `fs_blockcount = 15 MiB / 4 KiB =
3840` - numerically identical to `boards/pimoroni_picolipo.py`'s `BOARD_16MB` and
`boards/waveshare_rp2040_plus.py`'s `BOARD_16MB`, which reach the same split from the same 16 MiB
part reserving the same 1 MiB for firmware code. Unlike those two, this board has **one** flash
variant, not two. CircuitPython's own start is the generic
`firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 1020 KiB + 4 KiB = 0x100000`, `fs_blockcount = 512`
following this project's existing CircuitPython convention (0035: only the *start* has to be right,
since the emulated flash buffer is 16 MiB).

Onboard extras:

- The NeoPixel: `Ws2812(gpio=25)` (`rp2040py.external.ws2812`) - a real WS2812-class part per both
  ports' naming, so `Ws2812`'s default `color_order="GRB"` applies. GPIO25 is the *same pin* a
  plain Pico puts its ordinary LED on, which is exactly why the pico-sdk header spells out that
  this board has no plain LED there - modelling GPIO25 as an `LEDMock` here would be wrong.
  Live-boot-verified (`tests/ws2812_boot_decode.py`'s pattern, run against this board file) that
  CircuitPython drives it as its own status indicator from boot, same as
  `vcc_gnd_yd_rp2040`/`waveshare_rp2040_zero` - 11 frames decoded before any guest code ran in one
  measured boot - so `board_with(on_pixels)` below sees pixels with no guest code at all.
  MicroPython's build declares no such default and needs guest code to write to GPIO25 itself
  (0 frames at boot, measured).
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051). SparkFun's board carries BOOT and RESET buttons, but neither firmware
  port declares a GPIO pushbutton (no `pins.csv` at all, and no `BUTTON` in CircuitPython's
  `pins.c`), so BOOT is the QSPI-SS pad `BootselButton` already models and nothing else is added.
- RESET: `ResetButton` (`rp2040py.external.reset_button`). SparkFun's own product page lists it
  twice - "boot button, reset button" in the overview and a bare "Buttons: Boot, Reset" in the
  spec table - and presence is the only per-board fact needed, since RUN's net has no pin number
  and no configurable pull (docs/records/0057's addendum). Modelled since docs/records/0089's
  Phase 4.
- Not modelled: the Qwiic/STEMMA JST-SH
  connector (a bare I2C breakout - no fixed onboard chip behind it, so there is nothing to
  emulate), USB-C, and the RP2040's own RTC (not board-specific).
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.reset_button import ResetButton
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "RGB_GPIO", "board_with")

RGB_GPIO = 25

_EXTRAS = (lambda: Ws2812(gpio=RGB_GPIO), BootselButton, ResetButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug SPARKFUN_PROMICRO
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug sparkfun_pro_micro_rp2040
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20260406-v1.28.0.uf2",
            "1.27.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20251209-v1.27.0.uf2",
            "1.26.1": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20250911-v1.26.1.uf2",
            "1.26.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20250809-v1.26.0.uf2",
            "1.25.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20250415-v1.25.0.uf2",
            "1.24.1": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20241129-v1.24.1.uf2",
            "1.24.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20241025-v1.24.0.uf2",
            "1.23.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20240602-v1.23.0.uf2",
            "1.22.2": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20240222-v1.22.2.uf2",
            "1.22.1": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20240105-v1.22.1.uf2",
            "1.22.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20231227-v1.22.0.uf2",
            "1.21.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20231005-v1.21.0.uf2",
            "1.20.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20230426-v1.20.0.uf2",
            "1.19.1": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20220618-v1.19.1.uf2",
            "1.18": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20220117-v1.18.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
            "1.29.0": "https://micropython.org/resources/firmware/SPARKFUN_PROMICRO-20260824-v1.29.0.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 3840, "fs_blocksize": 4096},
    ),
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.1.1.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-10.3.0-alpha.4.uf2",
            "6.2.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-6.2.0-beta.4.uf2",
            "6.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-6.2.0-rc.0.uf2",
            "6.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-6.2.0.uf2",
            "6.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-6.3.0-rc.0.uf2",
            "6.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-6.3.0.uf2",
            "7.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-alpha.1.uf2",
            "7.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-alpha.2.uf2",
            "7.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-alpha.3.uf2",
            "7.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-alpha.4.uf2",
            "7.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-alpha.5.uf2",
            "7.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-alpha.6.uf2",
            "7.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-beta.0.uf2",
            "7.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-rc.0.uf2",
            "7.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-rc.1.uf2",
            "7.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-rc.2.uf2",
            "7.0.0-rc.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0-rc.3.uf2",
            "7.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.0.0.uf2",
            "7.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.0-beta.0.uf2",
            "7.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.0-beta.1.uf2",
            "7.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.0-beta.2.uf2",
            "7.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.0-beta.3.uf2",
            "7.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.0-rc.0.uf2",
            "7.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.0-rc.1.uf2",
            "7.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.0.uf2",
            "7.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.1.1.uf2",
            "7.2.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.0-alpha.0.uf2",
            "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.0-alpha.1.uf2",
            "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.0-alpha.2.uf2",
            "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.0-rc.0.uf2",
            "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.0-rc.2.uf2",
            "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.0.uf2",
            "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.1.uf2",
            "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.2.uf2",
            "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.3.uf2",
            "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.4.uf2",
            "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.2.5.uf2",
            "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0-alpha.0.uf2",
            "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0-beta.0.uf2",
            "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0-beta.1.uf2",
            "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0-beta.2.uf2",
            "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0-rc.0.uf2",
            "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0-rc.1.uf2",
            "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0-rc.2.uf2",
            "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.0.uf2",
            "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.1.uf2",
            "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.2.uf2",
            "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-7.3.3.uf2",
            "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-alpha.0.uf2",
            "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-alpha.1.uf2",
            "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-beta.0.uf2",
            "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-beta.1.uf2",
            "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-beta.2.uf2",
            "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-beta.3.uf2",
            "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-beta.4.uf2",
            "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-beta.5.uf2",
            "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-beta.6.uf2",
            "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-rc.0.uf2",
            "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-rc.1.uf2",
            "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0-rc.2.uf2",
            "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.0.uf2",
            "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.2.uf2",
            "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.3.uf2",
            "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.4.uf2",
            "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.0.5.uf2",
            "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.1.0-beta.0.uf2",
            "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.1.0-beta.1.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/sparkfun_pro_micro_rp2040/en_US/adafruit-circuitpython-sparkfun_pro_micro_rp2040-en_US-9.2.9.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the NeoPixel's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/vcc_gnd_yd_rp2040/`, `boards/waveshare_rp2040_zero.py` and the Adafruit boards).
    Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(extras=(lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[1:]), firmware=FIRMWARE)


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
