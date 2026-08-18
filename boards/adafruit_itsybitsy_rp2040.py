"""BoardSpec definition for the **Adafruit ItsyBitsy RP2040** (https://www.adafruit.com/product/4888)
- a small-form-factor RP2040 board, electrically a Pico-class board with the same 8 MiB flash part
as `boards/adafruit_feather_rp2040/` and three onboard indicators: a plain red LED on **GPIO11**, a
**WS2812 RGB NeoPixel on GPIO17** with a genuine **power-enable pin on GPIO16**, and a second,
non-BOOTSEL **BOOT button on GPIO13**. Built as a worked `--board-spec` example, the third board
picked up off [0066](../docs/records/0066-board-support-expansion.md)'s survey; load it with
e.g.:

    rp2040py micropython --board-spec boards/adafruit_itsybitsy_rp2040.py:BOARD -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/adafruit_itsybitsy_rp2040.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.adafruit_itsybitsy_rp2040:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `adafruit_itsybitsy_rp2040.py` after both firmwares' own board id - MicroPython's
`ports/rp2/boards/ADAFRUIT_ITSYBITSY_RP2040`, case-normalized, and CircuitPython's own
`adafruit_itsybitsy_rp2040` (they agree here, unlike `boards/weactstudio/`).

Every number below is derived from a local checkout of both upstream ports at their current tags,
plus Adafruit's own published EAGLE schematic where firmware source alone left a question open
(docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/ADAFRUIT_ITSYBITSY_RP2040/`: `pins.csv` (`LED,GPIO11` - the
  file's only entry), `mpconfigboard.h` (`MICROPY_HW_BOARD_NAME "Adafruit ItsyBitsy RP2040"`, USB
  VID `0x239A`/PID `0x80FE`, `MICROPY_HW_FLASH_STORAGE_BYTES (7 * 1024 * 1024)`, and its own
  comments `// NeoPixel data GPIO17, power GPIO16` / `// Red user LED GPIO11` / `// Boot button
  GPIO13`), and the pico-sdk board header `lib/pico-sdk/src/boards/include/boards/
  adafruit_itsybitsy_rp2040.h` (`PICO_DEFAULT_LED_PIN 11`, `PICO_DEFAULT_WS2812_PIN 17`,
  `PICO_DEFAULT_WS2812_POWER_PIN 16`, `PICO_FLASH_SIZE_BYTES (8 * 1024 * 1024)`, no board-specific
  `link.ld`/firmware-size override). **Unlike `boards/adafruit_feather_rp2040/`, this board's
  NeoPixel power-enable pin claim is real** - both `PICO_DEFAULT_WS2812_POWER_PIN` and
  MicroPython's own comment agree it exists, on GPIO16.
- CircuitPython, `ports/raspberrypi/boards/adafruit_itsybitsy_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO17)`, `CIRCUITPY_STATUS_LED_POWER (&pin_GPIO16)` - the status
  LED's own power-enable pin, agreeing with MicroPython's), `mpconfigboard.mk` (same USB VID/PID,
  `EXTERNAL_FLASH_DEVICES = "W25Q64JVxQ"` - 64 Mbit = 8 MiB, agreeing with the pico-sdk header),
  and `pins.c` (`LED`/`D13` -> GPIO11, `NEOPIXEL` -> GPIO17, `NEOPIXEL_POWER` -> GPIO16, `BUTTON`
  -> GPIO13). No board-specific `link.ld` (confirmed absent from the directory listing), so
  `ports/raspberrypi/link-rp2040.ld`'s default `firmware_size = 1020K` applies, same derivation
  every other board file in this project already documents.

Flash geometry, MicroPython: `fs_start = PICO_FLASH_SIZE_BYTES - MICROPY_HW_FLASH_STORAGE_BYTES = 8
MiB - 7 MiB = 0x100000`, `fs_blockcount = 7 MiB / 4 KiB = 1792` (0035's derivation) - the same
numbers as `boards/adafruit_feather_rp2040/`, since both boards share the same 8 MiB/7 MiB split.
CircuitPython's own start is the generic `firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE = 0x100000`,
`fs_blockcount = 512` following this project's existing CircuitPython convention.

## GPIO13's BOOT button: the physical switch also engages real BOOTSEL, which this board file does not model

Adafruit's product page for this board (and for `boards/adafruit_feather_rp2040/`) claims a
NeoPixel power pin whether or not firmware source agrees - true here, false there. What the product
page does *not* mention, and firmware source alone does not either, is how GPIO13's button is wired
electrically - so this section is sourced from Adafruit's own published EAGLE schematic
(`adafruit/Adafruit-ItsyBitsy-RP2040-PCB`, `Adafruit ItsyBitsy RP2040.sch`) rather than either
firmware port:

- **`SW3`** (the physical BOOT button) connects directly to **GND** on one side and to net
  `USBBOOT` (GPIO13) on the other - a direct short to ground when pressed, no series resistor, the
  same shape `vcc_gnd_yd_rp2040`'s USRKEY button has. **No external pull-up exists on this net at
  all** - GPIO13's released level, if firmware reads it as a plain input, depends entirely on
  whichever internal pull firmware configures (docs/records/0006/0049/0051's standing rule for
  exactly this shape of net).
- **The same switch is also diode-coupled into the real BOOTSEL path.** `D3` (a small-signal diode,
  cathode on the `USBBOOT`/GPIO13 net) has its anode routed through `R11` (1k) to net `QSPI_CS` -
  the RP2040's actual `GPIO_QSPI_SS` pad, the pin `BootselButton` already models per
  docs/records/0050/0051. Pressing `SW3` pulls GPIO13 to GND *and*, through the diode/resistor,
  weakly pulls the real BOOTSEL pad toward GND too - one physical button doing double duty as both
  a plain user-readable GPIO input and (with the right timing, at power-up) a real BOOTSEL trigger.

**This board file models GPIO13 as a plain button only** (`KeyMock(gpio=13, active_high=False)`,
the direct-short-to-GND case) **and does not model the diode/resistor coupling into BOOTSEL.**
That coupling is an analog current path between two pins - `ExternalDevice`/`GPIOPin` here has no
representation for a component that routes current *between* two pins rather than driving one pin
from a state machine - and building one is out of scope for a board file. Pressing BOOTSEL on this
emulated board still requires driving `BootselButton` directly (docs/records/0050/0051's existing
mechanism); pressing the emulated GPIO13 button does not additionally trigger it, which is a real,
stated fidelity gap rather than a silent omission.

## The NeoPixel power-enable pin (GPIO16) is not modelled as gating the NeoPixel

Both firmware sources genuinely wire a power-enable pin to this NeoPixel (unlike
`adafruit_feather_rp2040`'s false marketing claim) - real hardware will not light the LED unless
firmware first drives GPIO16 high. `Ws2812` (`rp2040py.external.ws2812`) has no concept of a power
input at all: it decodes whatever waveform arrives on its data pin regardless of any other pin's
state, since it was designed to watch one data line, not to arbitrate a component's simulated power
rail. GPIO16 itself is a perfectly ordinary GPIO in this emulation (any board or guest code can
drive/read it ) - it is simply not wired to anything that would refuse to "light" without it. Live
boot confirms CircuitPython drives GPIO16 high automatically, as part of powering its own status
NeoPixel (`CIRCUITPY_STATUS_LED_POWER`), before any guest code runs.

Onboard extras:

- The LED: `LEDMock(gpio=11)` (`rp2040py.external.led_mock`) - a plain LED per both ports'
  `LED`/`PICO_DEFAULT_LED_PIN` naming, not a NeoPixel.
- The RGB LED: `Ws2812(gpio=17)` (`rp2040py.external.ws2812`) - a real WS2812-class part per both
  ports' `NEOPIXEL` naming, so `Ws2812`'s default `color_order="GRB"` applies. Live-boot-verified
  (`tests/ws2812_boot_decode.py`'s pattern, run against this board file) that CircuitPython drives
  this LED as its own status indicator from boot, same as every other WS2812 board file in this
  project - frames decoded before any guest code ran. MicroPython's build declares no such default
  and needs guest code to write to `NEOPIXEL`/GPIO17 itself.
- BOOT button: `KeyMock(gpio=13, active_high=False)` - see the schematic section above; models the
  direct-short-to-GND half only, not the BOOTSEL coupling.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051) - the *real* BOOTSEL mechanism, independent of GPIO13's own button.
- Not modelled: the **RESET** button (pulls RUN, not a GPIO - docs/records/0057, same gap every
  other board file here documents), the NeoPixel power-enable semantics described above, the Vhigh
  power pin and USB-powered/battery-powered auto-switching (analog, not RP2040-visible), broken-out
  SWD (electrically just the RP2040's own SWD pins, nothing board-specific), and USB Micro-B.
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.key_mock import KeyMock
from rp2040py.external.led_mock import LEDMock
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "BOOT_BUTTON_GPIO", "FIRMWARE", "LED_GPIO", "RGB_GPIO", "board_with")

LED_GPIO = 11
RGB_GPIO = 17
BOOT_BUTTON_GPIO = 13

_EXTRAS = (
    lambda: LEDMock(gpio=LED_GPIO),
    lambda: Ws2812(gpio=RGB_GPIO),
    lambda: KeyMock(gpio=BOOT_BUTTON_GPIO, active_high=False),
    BootselButton,
)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug ADAFRUIT_ITSYBITSY_RP2040
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug adafruit_itsybitsy_rp2040
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20260406-v1.28.0.uf2",
            "1.27.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20251209-v1.27.0.uf2",
            "1.26.1": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20250911-v1.26.1.uf2",
            "1.26.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20250809-v1.26.0.uf2",
            "1.25.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20250415-v1.25.0.uf2",
            "1.24.1": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20241129-v1.24.1.uf2",
            "1.24.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20241025-v1.24.0.uf2",
            "1.23.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20240602-v1.23.0.uf2",
            "1.22.2": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20240222-v1.22.2.uf2",
            "1.22.1": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20240105-v1.22.1.uf2",
            "1.22.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20231227-v1.22.0.uf2",
            "1.21.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20231005-v1.21.0.uf2",
            "1.20.0": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20230426-v1.20.0.uf2",
            "1.19.1": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20220618-v1.19.1.uf2",
            "1.18": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20220117-v1.18.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/ADAFRUIT_ITSYBITSY_RP2040-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 1792, "fs_blocksize": 4096},
    ),
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.1.1.uf2",
            "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.1.2.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-10.3.0-alpha.4.uf2",
            "6.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-6.2.0-rc.0.uf2",
            "6.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-6.2.0.uf2",
            "6.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-6.3.0-rc.0.uf2",
            "6.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-6.3.0.uf2",
            "7.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-alpha.1.uf2",
            "7.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-alpha.2.uf2",
            "7.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-alpha.3.uf2",
            "7.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-alpha.4.uf2",
            "7.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-alpha.5.uf2",
            "7.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-alpha.6.uf2",
            "7.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-beta.0.uf2",
            "7.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-rc.0.uf2",
            "7.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-rc.1.uf2",
            "7.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-rc.2.uf2",
            "7.0.0-rc.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0-rc.3.uf2",
            "7.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.0.0.uf2",
            "7.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.0-beta.0.uf2",
            "7.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.0-beta.1.uf2",
            "7.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.0-beta.2.uf2",
            "7.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.0-beta.3.uf2",
            "7.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.0-rc.0.uf2",
            "7.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.0-rc.1.uf2",
            "7.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.0.uf2",
            "7.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.1.1.uf2",
            "7.2.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.0-alpha.0.uf2",
            "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.0-alpha.1.uf2",
            "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.0-alpha.2.uf2",
            "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.0-rc.0.uf2",
            "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.0-rc.2.uf2",
            "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.0.uf2",
            "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.1.uf2",
            "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.2.uf2",
            "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.3.uf2",
            "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.4.uf2",
            "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.2.5.uf2",
            "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0-alpha.0.uf2",
            "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0-beta.0.uf2",
            "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0-beta.1.uf2",
            "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0-beta.2.uf2",
            "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0-rc.0.uf2",
            "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0-rc.1.uf2",
            "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0-rc.2.uf2",
            "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.0.uf2",
            "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.1.uf2",
            "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.2.uf2",
            "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-7.3.3.uf2",
            "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-alpha.0.uf2",
            "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-alpha.1.uf2",
            "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-beta.0.uf2",
            "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-beta.1.uf2",
            "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-beta.2.uf2",
            "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-beta.3.uf2",
            "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-beta.4.uf2",
            "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-beta.5.uf2",
            "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-beta.6.uf2",
            "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-rc.0.uf2",
            "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-rc.1.uf2",
            "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0-rc.2.uf2",
            "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.0.uf2",
            "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.2.uf2",
            "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.3.uf2",
            "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.4.uf2",
            "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.0.5.uf2",
            "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.1.0-beta.0.uf2",
            "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.1.0-beta.1.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_itsybitsy_rp2040/en_US/adafruit-circuitpython-adafruit_itsybitsy_rp2040-en_US-9.2.9.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/vcc_gnd_yd_rp2040/`, `boards/waveshare_rp2040_zero/` and
    `boards/adafruit_feather_rp2040/`). Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(
        extras=(_EXTRAS[0], lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[2:]), firmware=FIRMWARE
    )


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
