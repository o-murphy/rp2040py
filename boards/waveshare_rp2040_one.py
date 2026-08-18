"""BoardSpec definition for the **Waveshare RP2040-One**
(https://circuitpython.org/board/waveshare_rp2040_one/) - a castellated USB-A-stick RP2040 board
whose only onboard indicator is a single **WS2812 RGB LED on GPIO16**, on a **4 MiB** flash part.
Built as a worked `--board-spec` example, picked up off
[0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --circuitpython --board-spec boards/waveshare_rp2040_one.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --circuitpython --board-spec boards.waveshare_rp2040_one:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `waveshare_rp2040_one.py` after CircuitPython's own board id - the only firmware family
that has one, so unlike `boards/seeed_xiao_rp2040.py`/`boards/sparkfun_promicro.py` there is no id
disagreement to resolve here.

**Only CircuitPython is declared, deliberately** - the same situation
`boards/vcc_gnd_yd_rp2040/` documents at length. MicroPython ships no `ports/rp2/boards/` port for
this board (confirmed absent from a local checkout at `v1.28.0`: only `WAVESHARE_RP2040_LCD_0_96`,
`WAVESHARE_RP2040_PLUS`, `WAVESHARE_RP2040_ZERO` and an RP2350 board exist there). The generic
`RPI_PICO` build does run on it - electrically it is a Pico - but with a *plain Pico's* flash
geometry, which is wrong for this 4 MiB part: the filesystem would sit where a 2 MiB board puts it
and most of the chip would go unused. A `firmware` key means "this firmware is built *for* this
board", not "this firmware runs here" (docs/records/0062), so rather than assert something upstream
never built, run MicroPython explicitly:

    rp2040py micropython --board-spec boards/waveshare_rp2040_one.py:BOARD --image path/to/RPI_PICO-...uf2

which resolves nothing from this file and boots the plain Pico image on this board's devices
(`--image` alongside `--board-spec` is itself a 0059 capability). **`--image` must be a local `.uf2`
path here, not a bare version tag**: a tag is resolved *against the spec's own declared families*,
so `--image 1.28.0` on a CircuitPython-only spec fails with "This BoardSpec declares no
'micropython' firmware, so there is nothing to resolve the firmware tag '1.28.0' against" - verified
this session, and worth stating because `boards/vcc_gnd_yd_rp2040/`'s own docstring currently shows
the tag form for exactly this fallback.

Every number below is derived from the upstream CircuitPython port at its current tag, not guessed
(docs/records/0027's "3g rule"):

- `ports/raspberrypi/boards/waveshare_rp2040_one/mpconfigboard.h`:
  `MICROPY_HW_BOARD_NAME "Waveshare RP2040-One"`, `MICROPY_HW_NEOPIXEL (&pin_GPIO16)` - i.e. the
  RGB LED is CircuitPython's own *status* indicator, which is why it is driven during boot with no
  user code involved. Also `DEFAULT_UART_BUS_TX/RX` GPIO0/1, `DEFAULT_I2C_BUS_SDA/SCL` GPIO4/5, and
  a SPI trio spelled `DEFAULT_SPI_BUS_CK`/`_MOSI`/`_MISO` GPIO6/7/8.
- `mpconfigboard.mk`: USB VID `0x2E8A`/PID `0x103A`, `"RP2040-One"`/`"Waveshare Electronics"`,
  `EXTERNAL_FLASH_DEVICES = "W25Q32JVxQ"` (32 Mbit = **4 MiB** - twice
  `boards/waveshare_rp2040_zero.py`'s part, the closest board in this project), `CIRCUITPY__EVE = 1`
  (an FT800/EVE display-driver *module* compiled into the firmware - a build option, not an onboard
  chip, so nothing here models it; `boards/vcc_gnd_yd_rp2040/` carries the same flag).
- `pins.c`: `GP0`-`GP29` all broken out (plus `A0`-`A3`/`GP26_A0`-`GP29_A3` aliases and `TX`/`RX`),
  `NEOPIXEL` -> GPIO16, and **no `LED` and no `BUTTON` entry at all** - confirming there is no plain
  LED and no GPIO pushbutton on this board. The only bus object exposed is `board_uart_obj`: despite
  the I2C/SPI defaults declared in `mpconfigboard.h` above, no `board_i2c_obj`/`board_spi_obj`
  appears in the table, so `board.I2C`/`board.SPI` simply do not exist on this board's firmware.
  (Stated as source reads it, not diagnosed - note in passing that the SPI macro is spelled
  `DEFAULT_SPI_BUS_CK` where CircuitPython's shared code elsewhere uses `DEFAULT_SPI_BUS_SCK`.)
- `board.c` declares no board-specific init at all, only the `MP_WEAK supervisor/shared/board.c`
  defaults, and `pico-sdk-configboard.h` is the empty stub CircuitPython requires every board to
  carry. No board-specific `link.ld` (confirmed absent from the directory listing), so
  `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies.

Flash geometry: `fs_start = firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 1020 KiB + 4 KiB =
0x100000`, `fs_blockcount = 512` following this project's existing CircuitPython convention
(docs/records/0035: only the *start* has to be right, since the emulated flash buffer is 16 MiB) -
the same values every other CircuitPython board file here uses. The 4 MiB part therefore changes
nothing in the layout; it only means more of the chip is genuinely there.

Onboard extras:

- The RGB LED: `Ws2812(gpio=16)` (`rp2040py.external.ws2812`) - a real WS2812-class part per the
  port's `NEOPIXEL`/`MICROPY_HW_NEOPIXEL` naming, so `Ws2812`'s default `color_order="GRB"`
  applies. **The same GPIO16 as `boards/waveshare_rp2040_zero.py`** - two different Waveshare
  products that happen to agree on the pin, each read from its own source rather than one assumed
  from the other. Live-boot-verified that CircuitPython drives it as its own status indicator from
  boot, so `board_with(on_pixels)` below sees pixels with no guest code at all.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- Not modelled: any **RESET/BOOT pushbutton** beyond BOOTSEL. CircuitPython's `pins.c` declares no
  `BUTTON` of any kind, and a RESET control pulls RUN rather than a GPIO (docs/records/0057, the
  same gap every other board file here documents). Waveshare's own wiki and product pages could not
  be read this session (both returned HTTP 403), so nothing beyond the firmware source is claimed
  about this board's physical controls. Also not modelled: the USB-A plug, and the RP2040's own RTC
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
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug waveshare_rp2040_one
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.1.1.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-10.3.0-alpha.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_one/en_US/adafruit-circuitpython-waveshare_rp2040_one-en_US-9.2.9.uf2",
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
