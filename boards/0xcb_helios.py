"""BoardSpec definition for the **0xCB Helios**
(https://circuitpython.org/board/0xcb_helios/) - a split-mechanical-keyboard RP2040 mainboard with
**both** a plain status LED (GPIO17) and a separate **WS2812 RGB LED (GPIO25)**, on a **16 MiB**
flash part. Built as a worked `--board-spec` example, picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey - the entry that survey flagged as
"not fully confirmed" (its RGB pin looked WS2812-compatible but had no cited source at the time);
that gap is closed below via the pico-sdk board header, not left open.

    rp2040py micropython --circuitpython --board-spec "boards/0xcb_helios.py:BOARD" -c "<probe>"

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `0xcb_helios.py` after CircuitPython's own board id - the only firmware family that has
one. **Only the `--board-spec path:ATTR` file form works for this board, not the dotted
`--board-spec module.path:ATTR` form** - same gap `boards/0xcb_gemini.py` documents
(docs/records/0083): `0xcb_helios` is not a legal Python identifier (starts with a digit), so
`import boards.0xcb_helios` cannot work at all.

**Only CircuitPython is declared, deliberately** - the same situation `boards/0xcb_gemini.py` and
`boards/vcc_gnd_yd_rp2040/` document at length. MicroPython ships no `ports/rp2/boards/` port for
this board (confirmed absent from the current `ports/rp2/boards/` listing at
`micropython/micropython`'s default branch - no `0XCB`/`HELIOS` entry among its 45 board
directories). The generic `RPI_PICO` build does run on it - electrically it is a Pico - but with a
*plain Pico's* flash geometry, which is wrong for this 16 MiB part. A `firmware` key means "this
firmware is built *for* this board", not "this firmware runs here" (docs/records/0062), so rather
than assert something upstream never built, run MicroPython explicitly:

    rp2040py micropython --board-spec "boards/0xcb_helios.py:BOARD" --image path/to/RPI_PICO-...uf2

which resolves nothing from this file and boots the plain Pico image on this board's devices
(`--image` alongside `--board-spec` is itself a 0059 capability). **`--image` must be a local `.uf2`
path here, not a bare version tag** - the same CircuitPython-only-spec limit
docs/records/0081/0083 both verified.

Every number below is derived from the upstream CircuitPython port
(`ports/raspberrypi/boards/0xcb_helios/`) and, for the one fact CircuitPython's own source leaves
ambiguous, the pico-sdk board header - not guessed (docs/records/0027's "3g rule"):

- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "0xCB Helios"`, `MICROPY_HW_LED_STATUS (&pin_GPIO17)` -
  a *plain* status-LED macro, not `MICROPY_HW_NEOPIXEL`. Also `DEFAULT_I2C_BUS_SDA/SCL` GPIO2/3,
  `DEFAULT_SPI_BUS_SCK/MOSI/MISO` GPIO22/23/20, `DEFAULT_UART_BUS_TX/RX` GPIO0/1.
- `pins.c`: `LED` -> GPIO17 (agreeing with `MICROPY_HW_LED_STATUS`) and, separately, `RGB` ->
  GPIO25 - a *second* LED with no `MICROPY_HW_NEOPIXEL`/`NEOPIXEL` binding anywhere in this port,
  so CircuitPython's own source alone does not say what protocol GPIO25 speaks. No `BUTTON` entry
  at all. All three bus objects are exposed (`board_i2c_obj`/`board_spi_obj`/`board_uart_obj`),
  agreeing with the `DEFAULT_*_BUS_*` macros. A `VBUS_SENSE` pin on GPIO19 (same split-keyboard
  sense pin `boards/0xcb_gemini.py` documents, no cited comment on this port's copy of the table).
- **The pico-sdk board header closes the "not fully confirmed" gap 0066 flagged**:
  `lib/pico-sdk/src/boards/include/boards/0xcb_helios.h` declares both
  `PICO_DEFAULT_LED_PIN 17` *and* `PICO_DEFAULT_WS2812_PIN 25` explicitly, under its own
  `// User LED and level shifted PIN` comment - i.e. GPIO25 genuinely is a WS2812-class part, not
  merely GPIO-shaped and RGB-colored. (0xCB Gemini has no equivalent pico-sdk header at all -
  an asymmetry between these two boards from the same vendor, noted here rather than investigated
  further; out of scope for this board file.) No `PICO_DEFAULT_LED_PIN_INVERTED` anywhere in the
  header, so GPIO17's plain LED is assumed active-high by the same absence-based convention
  `boards/machdyne_werkzeug.py`/`boards/nullbits_bit_c_pro.py` document explicitly for their own
  inverted cases.
- `mpconfigboard.mk`: USB VID `0x1209`/PID `0xCB74`, `"Helios"`/`"0xCB"`,
  `EXTERNAL_FLASH_DEVICES = "W25Q128JVxQ"` (128 Mbit = **16 MiB**, agreeing with the pico-sdk
  header's own `PICO_FLASH_SIZE_BYTES (16 * 1024 * 1024)` - same capacity as
  `boards/0xcb_gemini.py`/`boards/sparkfun_promicro.py`).
- `board.c` declares no board-specific init at all, only the `MP_WEAK supervisor/shared/board.c`
  defaults, and `pico-sdk-configboard.h` is the empty stub CircuitPython requires every board to
  carry. No board-specific `link.ld` (directory holds only `board.c`, `mpconfigboard.h`,
  `mpconfigboard.mk`, `pico-sdk-configboard.h` and `pins.c`), so `ports/raspberrypi/link-rp2040.ld`'s
  default `firmware_size = 1020K` applies, same derivation every other CircuitPython board file
  here documents.

Flash geometry: `fs_start = firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 1020 KiB + 4 KiB =
0x100000`, `fs_blockcount = 512` following this project's existing CircuitPython convention
(docs/records/0035: only the *start* has to be right, since the emulated flash buffer is 16 MiB
regardless of the real part's size) - the same values `boards/0xcb_gemini.py`/
`boards/waveshare_rp2040_one.py`/`boards/sparkfun_promicro.py` all use.

Onboard extras:

- The status LED: `LEDMock(gpio=17)` (`rp2040py.external.led_mock`) - a plain LED per
  `MICROPY_HW_LED_STATUS`/`pins.c`'s `LED` entry/the pico-sdk header's `PICO_DEFAULT_LED_PIN`, all
  three agreeing. Not a `Ws2812` - that class of part is the *separate* GPIO25 pin below.
  **Live-boot-verified that CircuitPython drives this as its own boot/status indicator with no
  guest code involved** (below) - unlike the RGB LED, which is not driven at boot.
- The RGB LED: `Ws2812(gpio=25)` (`rp2040py.external.ws2812`) - confirmed WS2812-class by the
  pico-sdk header's `PICO_DEFAULT_WS2812_PIN 25` (see above), so `Ws2812`'s default
  `color_order="GRB"` applies. **Not** CircuitPython's status indicator here (no
  `MICROPY_HW_NEOPIXEL` binds it) - guest code must drive it itself, confirmed by live boot below
  seeing zero frames until guest code runs, the same shape MicroPython's build has for
  `boards/sparkfun_promicro.py`'s NeoPixel.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- Not modelled: any **RESET/BOOT pushbutton** beyond BOOTSEL - `pins.c` declares no `BUTTON` of any
  kind, and firmware config can never declare a RESET one anyway (RUN is not a GPIO). A
  `ResetButton` exists since docs/records/0089's Phase 4, so this is a sourcing gap, not a
  capability one - see `boards/0xcb_gemini.py`, whose vendor documentation could not be reached on
  2026-08-20 either. Also not modelled: the `VBUS_SENSE` ADC pin (a voltage-divider
  sense input, no fixed chip behind it), the split-keyboard half-to-half link hardware itself (a
  second physical PCB, out of scope), and the RP2040's own RTC (not board-specific).
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "LED_GPIO", "RGB_GPIO", "board_with")

LED_GPIO = 17
RGB_GPIO = 25

_EXTRAS = (lambda: LEDMock(gpio=LED_GPIO), lambda: Ws2812(gpio=RGB_GPIO), BootselButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug 0xcb_helios
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.1.1.uf2",
            "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.1.2.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.3.0-alpha.4.uf2",
            "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.0-rc.0.uf2",
            "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.0-rc.1.uf2",
            "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.0-rc.2.uf2",
            "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.0.uf2",
            "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.2.uf2",
            "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.3.uf2",
            "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.4.uf2",
            "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.0.5.uf2",
            "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.1.0-beta.0.uf2",
            "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.1.0-beta.1.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-9.2.9.uf2",
            "10.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.3.0-rc.0.uf2",
            "10.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_helios/en_US/adafruit-circuitpython-0xcb_helios-en_US-10.3.0.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/0xcb_gemini.py`/`boards/vcc_gnd_yd_rp2040/`). Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(
        extras=(_EXTRAS[0], lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[2:]), firmware=FIRMWARE
    )


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
