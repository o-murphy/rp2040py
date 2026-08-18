"""BoardSpec definition for the **Waveshare RP2040-Tiny**
(https://circuitpython.org/board/waveshare_rp2040_tiny/) - a postage-stamp RP2040 module whose only
onboard indicator is a single **WS2812 RGB LED on GPIO16**, on a **2 MiB** flash part, and which
breaks out only **GPIO0-16 plus the four ADC pins**. Built as a worked `--board-spec` example,
picked up off [0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --circuitpython --board-spec boards/waveshare_rp2040_tiny.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --circuitpython --board-spec boards.waveshare_rp2040_tiny:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `waveshare_rp2040_tiny.py` after CircuitPython's own board id - the only firmware family
that has one, so there is no id disagreement to resolve here.

**Only CircuitPython is declared, deliberately** - the same situation
`boards/vcc_gnd_yd_rp2040/` and `boards/waveshare_rp2040_one.py` document. MicroPython ships no
`ports/rp2/boards/` port for this board (confirmed absent from a local checkout at `v1.28.0`). A
`firmware` key means "this firmware is built *for* this board", not "this firmware runs here"
(docs/records/0062), so rather than assert something upstream never built, run MicroPython
explicitly:

    rp2040py micropython --board-spec boards/waveshare_rp2040_tiny.py:BOARD --image path/to/RPI_PICO-...uf2

which resolves nothing from this file and boots the plain Pico image on this board's devices
(`--image` alongside `--board-spec` is itself a 0059 capability; it must be a local `.uf2` path
rather than a bare version tag here - see `boards/waveshare_rp2040_one.py` for why). **Unlike every other
CircuitPython-only board here, that fallback is not even a geometry compromise**: this board's
flash part is the same 2 MiB as a Pico's, so the generic `RPI_PICO` build's own
`fs_start = 0xa0000`/352 blocks describe this chip exactly rather than stranding most of it -
live-verified below. It is still not declared as a `firmware` family, because upstream genuinely
never built one for this board and this file does not get to claim otherwise.

Every number below is derived from the upstream CircuitPython port at its current tag, not guessed
(docs/records/0027's "3g rule"):

- `ports/raspberrypi/boards/waveshare_rp2040_tiny/mpconfigboard.h`:
  `MICROPY_HW_BOARD_NAME "Waveshare RP2040-Tiny"`, `MICROPY_HW_NEOPIXEL (&pin_GPIO16)` - i.e. the
  RGB LED is CircuitPython's own *status* indicator, which is why it is driven during boot with no
  user code involved. `DEFAULT_UART_BUS_TX/RX` GPIO0/1, and - unlike
  `boards/waveshare_rp2040_one.py` - no I2C or SPI defaults declared at all.
- `mpconfigboard.mk`: USB VID `0x2E8A`/PID `0x1084`, `"RP2040-Tiny"`/`"Waveshare Electronics"`,
  `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ"` (16 Mbit = **2 MiB**, the same part
  `boards/waveshare_rp2040_zero.py` carries), `CIRCUITPY__EVE = 1` (an FT800/EVE display-driver
  *module* compiled into the firmware - a build option, not an onboard chip, so nothing here models
  it).
- `pins.c`: `GP0`-`GP16` and `GP26`-`GP29` only (plus `A0`-`A3`/`GP26_A0`-`GP29_A3` aliases and
  `TX`/`RX`) - **GPIO17-25 are absent from the table entirely**, the narrowest pin breakout of any
  board in this project and the one real structural difference from
  `boards/waveshare_rp2040_one.py`, which exposes all of `GP0`-`GP29`. `NEOPIXEL` -> GPIO16, and
  **no `LED` and no `BUTTON` entry at all** - confirming no plain LED and no GPIO pushbutton. The
  only bus object exposed is `board_uart_obj`, consistent with the header declaring no I2C/SPI
  defaults.
- `board.c` declares no board-specific init at all, only the `MP_WEAK supervisor/shared/board.c`
  defaults, and `pico-sdk-configboard.h` is the empty stub CircuitPython requires every board to
  carry. No board-specific `link.ld` (confirmed absent from the directory listing), so
  `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies. Both files carry a
  2024 copyright by a community contributor (Bill Sideris) rather than Adafruit's own 2021 header -
  a newer, externally-contributed port than the rest of this project's CircuitPython boards.

Flash geometry: `fs_start = firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 1020 KiB + 4 KiB =
0x100000`, `fs_blockcount = 512` following this project's existing CircuitPython convention
(docs/records/0035: only the *start* has to be right, since the emulated flash buffer is 16 MiB).

Onboard extras:

- The RGB LED: `Ws2812(gpio=16)` (`rp2040py.external.ws2812`) - a real WS2812-class part per the
  port's `NEOPIXEL`/`MICROPY_HW_NEOPIXEL` naming, so `Ws2812`'s default `color_order="GRB"`
  applies. **The same GPIO16 as `boards/waveshare_rp2040_zero.py` and
  `boards/waveshare_rp2040_one.py`** - three different Waveshare products that agree on the pin,
  each read from its own source rather than any one assumed from another. Live-boot-verified that
  CircuitPython drives it as its own status indicator from boot, so `board_with(on_pixels)` below
  sees pixels with no guest code at all.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- Not modelled: any **RESET/BOOT pushbutton** beyond BOOTSEL. CircuitPython's `pins.c` declares no
  `BUTTON` of any kind, and a RESET control pulls RUN rather than a GPIO (docs/records/0057, the
  same gap every other board file here documents). Waveshare's own wiki and product pages could not
  be read this session (both returned HTTP 403), so nothing beyond the firmware source is claimed
  about this board's physical controls or its packaging. Also not modelled: the RP2040's own RTC
  (not board-specific).
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
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug waveshare_rp2040_tiny
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.1.1.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-10.3.0-alpha.4.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_tiny/en_US/adafruit-circuitpython-waveshare_rp2040_tiny-en_US-9.2.9.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/vcc_gnd_yd_rp2040/` and `boards/waveshare_rp2040_zero.py`). Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(extras=(lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[1:]), firmware=FIRMWARE)


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
