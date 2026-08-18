"""BoardSpec definition for the **YD-RP2040** by VCC-GND Studio
(https://circuitpython.org/board/vcc_gnd_yd_rp2040/) - a Pico-class RP2040 board with USB-C, a
power LED, a RESET button, a USRKEY user button on GPIO24, and a **WS2812B RGB LED on GPIO23**,
with a bigger flash part than a Pico (W25Q32/64/128 - 4/8/16 MiB). Designed in docs/records/0062;
load it with e.g.:

    rp2040py micropython --circuitpython --board-spec boards/vcc_gnd_yd_rp2040/__init__.py:BOARD
    PYTHONPATH=. rp2040py micropython --circuitpython --board-spec boards.vcc_gnd_yd_rp2040:BOARD

What makes this board worth shipping as an example is the RGB LED: it is the board's own reason
for `rp2040py.external.ws2812.Ws2812` to exist, and CircuitPython drives it *as its status
indicator* from boot, so simply booting this spec produces decoded pixels with no guest code at
all (`MICROPY_HW_NEOPIXEL` below). Pass your own `on_pixels` via `board_with()` to receive them.

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (an entry in
`firmware_specs.json`) nor item 5 (a named maintainer) of 0059's promotion checklist, and cannot
clear item 2 for MicroPython at all - see below.

**Only CircuitPython is declared, deliberately.** MicroPython ships no `ports/rp2/boards/` port for
this board. The generic `RPI_PICO` build does run on it (electrically it is a Pico), but with the
Pico's own flash geometry - `fs_start=0xa0000`, 352 blocks - so the filesystem would sit where a
2 MiB board puts it and the rest of a 4/8/16 MiB chip would go unused. A `firmware` key means "this
firmware is built *for* this board", not "this firmware runs here" (docs/records/0062), so rather
than assert something upstream never built, run MicroPython explicitly:

    rp2040py micropython --board-spec boards/vcc_gnd_yd_rp2040/__init__.py:BOARD --image 1.28.0

which resolves nothing from this file and boots the plain Pico image on this board's devices.
(`--image` alongside `--board-spec` is itself a 0059 capability.)

Every number derived from the upstream CircuitPython port (docs/records/0027's "3g rule"), and the
two button nets from VCC-GND Studio's own schematic, `YD-2040 2022 V1.1 SCH`:

- `ports/raspberrypi/boards/vcc_gnd_yd_rp2040/mpconfigboard.mk`: USB VID `0x2E8A`/PID `0x102E`,
  `"YD-RP2040"`/`"VCC-GND Studio"`, `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ,W25Q32JVxQ,W25Q128JVxQ"`,
  `CIRCUITPY__EVE = 1`. **No** `CIRCUITPY_FIRMWARE_SIZE`, and no `link.ld` of its own (confirmed
  404 upstream), so `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies;
  with `CIRCUITPY_INTERNAL_NVM_SIZE = 4 * 1024`:

      fs_start = 1020 KiB + 4 KiB = 0x100000

  the same offset as plain `pico`. `fs_blockcount = 512` follows this project's existing
  CircuitPython entries rather than the board's real remaining flash (docs/records/0035: only the
  *start* has to be right, since the emulated flash buffer is 16 MiB). Upstream builds one image
  for all three flash parts, so unlike `boards/weactstudio/` there is no variant to choose.
- `mpconfigboard.h`: `MICROPY_HW_BOARD_NAME "VCC-GND Studio YD RP2040"`, and
  `MICROPY_HW_NEOPIXEL = GPIO23` - i.e. the RGB LED is CircuitPython's *status* indicator, which is
  why it is driven during boot with no user code involved.
- `pins.c`: `RGB`/`NEOPIXEL` -> GPIO23, `BUTTON` -> GPIO24, `LED` -> GPIO25.
- Schematic, `USR-SW` net: `GPIO24 -[R13 10k]- USR (ST-1185S) -> GND`, with `C18` 100n as a
  hardware debounce. R13 is in **series**, not a pull-up to 3V3 - there is no external pull-up at
  all, so the released level comes entirely from whichever internal pull firmware configures.
  Hence `KeyMock(gpio=24, active_high=False)`, whose `release()` hands the pad back to that pull
  rather than driving it high (docs/records/0006/0049/0051 - forcing it would read the same and
  mask a firmware that forgot `PULL_UP`).
- Schematic, RGB net: an **XL-5050RGBC-WS2812B** on `RGB_CTRL` from GPIO23 - a real WS2812B, so
  `Ws2812`'s default `color_order="GRB"` applies.

Onboard extras:

- The RGB LED: `Ws2812(gpio=23)` (`rp2040py.external.ws2812`). CircuitPython drives this LED
  through the PIO, which this emulator paces by `SM_CLKDIV` and `[delay]` as of docs/records/0063 -
  so booting this board with `board_with(on_pixels)` and no guest code at all decodes the status
  LED's own frames. (Before 0063 the pulse *widths* that carry a WS2812's bits arrived collapsed
  into each other, and what a PIO-driven driver produced here decoded as garbage.)
- USRKEY: `KeyMock(gpio=24, active_high=False)` - see the schematic note above.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- The user LED on GPIO25: `LEDMock`, same wiring as a plain Pico.
- Not modelled: the **RESET** button. Its net is `3V3 -[R12 10k]- RUN`, with the switch grounding
  RUN directly for as long as it is held - so a faithful model is a really-held-low RUN pin, which
  this emulator has no concept of at all (RUN is not a GPIO). Designed, with its alternatives, in
  docs/records/0057; until then a guest's own `machine.reset()`/`microcontroller.reset()` is the
  working equivalent. Also not modelled: the PWR LED (wired to the rail, nothing RP2040-visible),
  USB-C, and the RP2040's own RTC (not board-specific).
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.key_mock import KeyMock
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "LED_GPIO", "RGB_GPIO", "USRKEY_GPIO", "board_with")

RGB_GPIO = 23
USRKEY_GPIO = 24
LED_GPIO = 25

_EXTRAS = (
    lambda: Ws2812(gpio=RGB_GPIO),
    lambda: KeyMock(gpio=USRKEY_GPIO, active_high=False),
    BootselButton,
    lambda: LEDMock(gpio=LED_GPIO),
)

# Real download URL from https://circuitpython.org/board/vcc_gnd_yd_rp2040/ - only the tag this
# file was built and verified against is listed; add more the same way scripts/fetch_firmware.py
# does for the officially-supported boards. A value may equally be a local `.uf2` path.
FIRMWARE = {
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.2.1": "https://downloads.circuitpython.org/bin/vcc_gnd_yd_rp2040/en_US/"
            "adafruit-circuitpython-vcc_gnd_yd_rp2040-en_US-10.2.1.uf2"
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller. Worth more here than on most boards:
    CircuitPython drives this LED as its status indicator from boot, so an SDK caller sees decoded
    pixels without writing any guest code. Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(extras=(lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[1:]), firmware=FIRMWARE)


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
