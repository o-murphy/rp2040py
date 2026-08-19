"""BoardSpec definition for the **0xCB Gemini**
(https://circuitpython.org/board/0xcb_gemini/) - a split-mechanical-keyboard RP2040 mainboard whose
only onboard indicator is a single **WS2812 RGB LED on GPIO16**, on a **16 MiB** flash part. Built
as a worked `--board-spec` example, picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --circuitpython --board-spec "boards/0xcb_gemini.py:BOARD" -c "<probe>"

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `0xcb_gemini.py` after CircuitPython's own board id - the only firmware family that has
one. **Only the `--board-spec path:ATTR` file form works for this board, not the dotted
`--board-spec module.path:ATTR` form** (`PYTHONPATH=. rp2040py ... --board-spec boards.0xcb_gemini:BOARD`
would need `import boards.0xcb_gemini`, and `0xcb_gemini` is not a legal Python identifier - it
starts with a digit). Every other board file in this project happens to have an identifier-safe
name, so this is the first one where that fallback genuinely doesn't exist, not merely undocumented.

**Only CircuitPython is declared, deliberately** - the same situation
`boards/vcc_gnd_yd_rp2040/` and `boards/waveshare_rp2040_one.py` document at length. MicroPython
ships no `ports/rp2/boards/` port for this board (confirmed absent from the current
`ports/rp2/boards/` listing at `micropython/micropython`'s default branch - no `0XCB`/`GEMINI`
entry among its 45 board directories). The generic `RPI_PICO` build does run on it - electrically
it is a Pico - but with a *plain Pico's* flash geometry, which is wrong for this 16 MiB part. A
`firmware` key means "this firmware is built *for* this board", not "this firmware runs here"
(docs/records/0062), so rather than assert something upstream never built, run MicroPython
explicitly:

    rp2040py micropython --board-spec "boards/0xcb_gemini.py:BOARD" --image path/to/RPI_PICO-...uf2

which resolves nothing from this file and boots the plain Pico image on this board's devices
(`--image` alongside `--board-spec` is itself a 0059 capability). **`--image` must be a local `.uf2`
path here, not a bare version tag**: a tag is resolved *against the spec's own declared families*,
so a bare tag on a CircuitPython-only spec fails with "This BoardSpec declares no 'micropython'
firmware, so there is nothing to resolve the firmware tag '...' against" (docs/records/0081 verified
this same fallback for `waveshare_rp2040_one.py`).

Every number below is derived from the upstream CircuitPython port
(`ports/raspberrypi/boards/0xcb_gemini/`) at its current tag, not guessed (docs/records/0027's
"3g rule"):

- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "0xCB Gemini"`, `MICROPY_HW_NEOPIXEL (&pin_GPIO16)` -
  i.e. the RGB LED is CircuitPython's own *status* indicator, driven during boot with no user code
  involved. Also `DEFAULT_I2C_BUS_SDA/SCL` GPIO2/3, `DEFAULT_SPI_BUS_SCK/MOSI/MISO` GPIO6/7/4, and
  `DEFAULT_UART_BUS_TX/RX` GPIO0/1. No `MICROPY_HW_LED_STATUS`/plain-LED macro of any kind.
- `mpconfigboard.mk`: USB VID `0x1209`/PID `0xCB65` (`0x1209` is the shared pid.codes
  open-hardware VID, not vendor-specific), `"Gemini"`/`"0xCB"`,
  `EXTERNAL_FLASH_DEVICES = "W25Q128JVxQ"` (128 Mbit = **16 MiB** - the same part size as
  `boards/sparkfun_promicro.py`'s `W25Q128JVxM`, different package suffix, same capacity).
- `pins.c`: `GP0`-`GP29` all broken out (`A0`-`A3` aliased onto `GP26`-`GP29`, `TX`/`RX` onto
  `GP0`/`GP1`, `SDA`/`SCL` onto `GP2`/`GP3`, `SDI`/`CS`/`SCK`/`SDO` onto `GP4`/`GP5`/`GP6`/`GP7`,
  and `NEOPIXEL` onto `GP16`) - and **no `LED` and no `BUTTON` entry at all**, confirming there is
  no plain LED and no GPIO pushbutton on this board. Unlike `waveshare_rp2040_one.py`, all three
  bus objects *are* exposed: `board_i2c_obj`, `board_spi_obj` and `board_uart_obj` all appear in
  the table, agreeing with the `DEFAULT_*_BUS_*` macros above. A `VBUS_SENSE` pin is also declared
  on `GP19`, with the table's own comment citing
  `https://docs.keeb.supply/0xcb-gemini/guide/#split-capability` - a voltage-divider sense pin used
  by this board's split-keyboard-half detection, not a fixed onboard chip; not modelled (see below).
- `board.c` declares no board-specific init at all, only the `MP_WEAK supervisor/shared/board.c`
  defaults, and `pico-sdk-configboard.h` is the empty stub CircuitPython requires every board to
  carry. No board-specific `link.ld` (confirmed absent from the directory listing: only `board.c`,
  `mpconfigboard.h`, `mpconfigboard.mk`, `pico-sdk-configboard.h` and `pins.c` exist), so
  `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies, same derivation
  every other CircuitPython board file here documents.

Flash geometry: `fs_start = firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 1020 KiB + 4 KiB =
0x100000`, `fs_blockcount = 512` following this project's existing CircuitPython convention
(docs/records/0035: only the *start* has to be right, since the emulated flash buffer is 16 MiB
regardless of the real part's size) - the same values every other CircuitPython board file here
uses, `waveshare_rp2040_one.py`/`sparkfun_promicro.py` included.

Onboard extras:

- The RGB LED: `Ws2812(gpio=16)` (`rp2040py.external.ws2812`) - a real WS2812-class part per the
  port's `NEOPIXEL`/`MICROPY_HW_NEOPIXEL` naming, so `Ws2812`'s default `color_order="GRB"`
  applies. The same GPIO16 as `boards/waveshare_rp2040_zero.py`/`boards/waveshare_rp2040_one.py`,
  two unrelated vendors' boards that happen to agree on the pin, each read from its own source
  rather than one assumed from the other.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- Not modelled: any **RESET/BOOT pushbutton** beyond BOOTSEL - `pins.c` declares no `BUTTON` of any
  kind, and a RESET control pulls RUN rather than a GPIO (docs/records/0057, the same gap every
  other board file here documents). Also not modelled: the `VBUS_SENSE` ADC pin (a voltage-divider
  sense input, no fixed chip behind it - see `pins.c` above), the split-keyboard half-to-half link
  hardware itself (out of scope - a second physical PCB, not a chip on *this* board), and the
  RP2040's own RTC (not board-specific).
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "RGB_GPIO", "board_with")

RGB_GPIO = 16

_EXTRAS = (lambda: Ws2812(gpio=RGB_GPIO), BootselButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug 0xcb_gemini
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.1.1.uf2",
            "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.1.2.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-10.3.0-alpha.4.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/0xcb_gemini/en_US/adafruit-circuitpython-0xcb_gemini-en_US-9.2.9.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/vcc_gnd_yd_rp2040/` and `boards/waveshare_rp2040_one.py`). Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(extras=(lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[1:]), firmware=FIRMWARE)


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
