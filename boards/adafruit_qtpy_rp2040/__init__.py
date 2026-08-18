"""BoardSpec definition for the **Adafruit QT Py RP2040** (https://www.adafruit.com/product/4900)
- a very small RP2040 board, electrically a Pico-class board with the same 8 MiB flash part as
`boards/adafruit_feather_rp2040/`/`boards/adafruit_itsybitsy_rp2040/`, **no plain LED at all**, a
**WS2812 RGB NeoPixel on GPIO12** with a genuine **power-enable pin on GPIO11**, and a **BOOT
button on GPIO21** wired the same double-duty way `boards/adafruit_itsybitsy_rp2040/`'s is. Built
as a worked `--board-spec` example, the fourth board picked up off
[0066](../../docs/records/0066-board-support-expansion.md)'s survey; load it with e.g.:

    rp2040py micropython --board-spec boards/adafruit_qtpy_rp2040/__init__.py:BOARD -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/adafruit_qtpy_rp2040/__init__.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.adafruit_qtpy_rp2040:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

Directory named `adafruit_qtpy_rp2040` after both firmwares' own board id - MicroPython's
`ports/rp2/boards/ADAFRUIT_QTPY_RP2040`, case-normalized, and CircuitPython's own
`adafruit_qtpy_rp2040` (they agree here, unlike `boards/weactstudio/`).

Every number below is derived from a local checkout of both upstream ports at their current tags,
plus Adafruit's own published EAGLE schematic for the BOOT button (docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/ADAFRUIT_QTPY_RP2040/`: `pins.csv` (**empty** - no plain LED to
  name at all), `mpconfigboard.h` (USB VID `0x239A`/PID `0x80F8`, `MICROPY_HW_FLASH_STORAGE_BYTES
  (7 * 1024 * 1024)`, and its own comments `// NeoPixel data GPIO12, power GPIO11` / `// Boot
  button GPIO21`), and the pico-sdk board header `lib/pico-sdk/src/boards/include/boards/
  adafruit_qtpy_rp2040.h` (`// No normal LED` with `PICO_DEFAULT_LED_PIN` commented out,
  `PICO_DEFAULT_WS2812_PIN 12`, `PICO_DEFAULT_WS2812_POWER_PIN 11`, `PICO_FLASH_SIZE_BYTES (8 *
  1024 * 1024)`, no board-specific `link.ld`/firmware-size override). Adafruit's product page
  claims "8 MB SPI FLASH" in its technical-details list but also "there is 4MB" in its own prose a
  few paragraphs earlier - firmware source settles it at 8 MiB, matching the technical-details
  figure and contradicting the prose.
- CircuitPython, `ports/raspberrypi/boards/adafruit_qtpy_rp2040/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO12)`, `CIRCUITPY_STATUS_LED_POWER (&pin_GPIO11)`),
  `mpconfigboard.mk` (same USB VID/PID, `EXTERNAL_FLASH_DEVICES = "W25Q64JVxQ"` - 64 Mbit = 8 MiB,
  agreeing with the pico-sdk header), and `pins.c` (`NEOPIXEL` -> GPIO12, `NEOPIXEL_POWER` ->
  GPIO11, `BUTTON` -> GPIO21 - no `LED` entry at all, confirming no plain LED). No board-specific
  `link.ld` (confirmed absent from the directory listing), so `ports/raspberrypi/link-rp2040.ld`'s
  default `firmware_size = 1020K` applies, same derivation every other board file in this project
  already documents.

Flash geometry matches `adafruit_feather_rp2040`/`adafruit_itsybitsy_rp2040` exactly
(`fs_start = 0x100000`, `fs_blockcount = 1792` for MicroPython, `fs_blockcount = 512` for
CircuitPython) since all three boards share the same 8 MiB flash / 7 MiB storage split - confirmed
by live boot (`os.statvfs('/')` reporting exactly 1792 blocks).

## The NeoPixel power-enable pin (GPIO11) is real, same as ItsyBitsy - and not modelled as gating the NeoPixel

Both firmware sources genuinely wire a power-enable pin to this NeoPixel, same shape as
`boards/adafruit_itsybitsy_rp2040/`'s GPIO16 (and unlike `boards/adafruit_feather_rp2040/`'s false
marketing claim - see that board's own docstring). `Ws2812` (`rp2040py.external.ws2812`) has no
concept of a power input at all: it decodes whatever waveform arrives on its data pin regardless of
any other pin's state. GPIO11 is an ordinary GPIO in this emulation, not wired to anything that
would refuse to "light" without it - a stated fidelity gap, not a silent one, same as
`adafruit_itsybitsy_rp2040`'s. Live boot confirms CircuitPython drives GPIO11 high automatically,
as part of powering its own status NeoPixel (`CIRCUITPY_STATUS_LED_POWER`), before any guest code
runs (checked directly with a GPIO listener, not inferred from the macro's existence).

## GPIO21's BOOT button: the same diode-into-BOOTSEL trick as ItsyBitsy, sourced from Adafruit's own schematic

Neither firmware port's source states GPIO21's pull/polarity. Sourced from Adafruit's own published
EAGLE schematic (`adafruit/Adafruit-QT-Py-RP2040-PCB`, `Adafruit QT Py RP2040.sch`) - and it is
electrically the *same design* `boards/adafruit_itsybitsy_rp2040/`'s BOOT button uses, component-
for-component:

- **`SW2`** (the physical BOOT button) shorts directly to GND on one side and to net `USBBOOT`
  (GPIO21) on the other - no series resistor, no external pull-up, the same shape
  `vcc_gnd_yd_rp2040`'s USRKEY and `adafruit_itsybitsy_rp2040`'s BOOT button both have.
- **The same switch is diode-coupled into the real BOOTSEL path** - `D2` (cathode on the `USBBOOT`
  net) routes through `R16` (1k) to net `QSPI_CS`, the actual `GPIO_QSPI_SS` pad `BootselButton`
  already models (docs/records/0050/0051). Pressing `SW2` pulls GPIO21 low *and* weakly pulls the
  real BOOTSEL pad toward GND - one physical switch doing double duty, identical in every component
  value to `adafruit_itsybitsy_rp2040`'s `SW3`/`D3`/`R11`.

Modelled the same way as `adafruit_itsybitsy_rp2040`'s BOOT button, for the same reason: a plain
`KeyMock(gpio=21, active_high=False)` for the direct-short-to-GND half, and **not** the diode/
resistor coupling into BOOTSEL - an analog current path between two pins that `ExternalDevice`/
`GPIOPin` has no representation for. Pressing the emulated GPIO21 button does not additionally
trigger `BootselButton`; the two stay independent here, a stated gap rather than a silent one.

Onboard extras:

- The RGB LED: `Ws2812(gpio=12)` (`rp2040py.external.ws2812`) - a real WS2812-class part per both
  ports' `NEOPIXEL` naming, so `Ws2812`'s default `color_order="GRB"` applies. Live-boot-verified
  that CircuitPython drives this LED as its own status indicator from boot, same as every other
  WS2812 board file in this project - frames decoded before any guest code ran. MicroPython's build
  declares no such default and needs guest code to write to `NEOPIXEL`/GPIO12 itself.
- BOOT button: `KeyMock(gpio=21, active_high=False)` - see the schematic section above; models the
  direct-short-to-GND half only, not the BOOTSEL coupling.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051) - the *real* BOOTSEL mechanism, independent of GPIO21's own button.
- Not modelled: no plain LED exists on this board at all - confirmed absent from every source cited
  above, not merely left out. Also not modelled: the **RESET** button (pulls RUN, not a GPIO -
  docs/records/0057), the NeoPixel power-enable semantics described above, the diode/BOOTSEL
  coupling above, the STEMMA QT I2C connector (electrically just `I2C(1)`, nothing board-specific),
  and USB-C.
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.key_mock import KeyMock
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "BOOT_BUTTON_GPIO", "FIRMWARE", "RGB_GPIO", "board_with")

RGB_GPIO = 12
BOOT_BUTTON_GPIO = 21

_EXTRAS = (
    lambda: Ws2812(gpio=RGB_GPIO),
    lambda: KeyMock(gpio=BOOT_BUTTON_GPIO, active_high=False),
    BootselButton,
)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug ADAFRUIT_QTPY_RP2040
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug adafruit_qtpy_rp2040
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20260406-v1.28.0.uf2",
            "1.27.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20251209-v1.27.0.uf2",
            "1.26.1": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20250911-v1.26.1.uf2",
            "1.26.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20250809-v1.26.0.uf2",
            "1.25.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20250415-v1.25.0.uf2",
            "1.24.1": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20241129-v1.24.1.uf2",
            "1.24.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20241025-v1.24.0.uf2",
            "1.23.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20240602-v1.23.0.uf2",
            "1.22.2": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20240222-v1.22.2.uf2",
            "1.22.1": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20240105-v1.22.1.uf2",
            "1.22.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20231227-v1.22.0.uf2",
            "1.21.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20231005-v1.21.0.uf2",
            "1.20.0": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20230426-v1.20.0.uf2",
            "1.19.1": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20220618-v1.19.1.uf2",
            "1.18": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20220117-v1.18.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/ADAFRUIT_QTPY_RP2040-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 1792, "fs_blocksize": 4096},
    ),
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.1.1.uf2",
            "10.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.1.2.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-10.3.0-alpha.4.uf2",
            "6.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-6.2.0-rc.0.uf2",
            "6.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-6.2.0.uf2",
            "6.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-6.3.0-rc.0.uf2",
            "6.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-6.3.0.uf2",
            "7.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-alpha.1.uf2",
            "7.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-alpha.2.uf2",
            "7.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-alpha.3.uf2",
            "7.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-alpha.4.uf2",
            "7.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-alpha.5.uf2",
            "7.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-alpha.6.uf2",
            "7.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-beta.0.uf2",
            "7.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-rc.0.uf2",
            "7.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-rc.1.uf2",
            "7.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-rc.2.uf2",
            "7.0.0-rc.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0-rc.3.uf2",
            "7.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.0.0.uf2",
            "7.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.0-beta.0.uf2",
            "7.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.0-beta.1.uf2",
            "7.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.0-beta.2.uf2",
            "7.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.0-beta.3.uf2",
            "7.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.0-rc.0.uf2",
            "7.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.0-rc.1.uf2",
            "7.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.0.uf2",
            "7.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.1.1.uf2",
            "7.2.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.0-alpha.0.uf2",
            "7.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.0-alpha.1.uf2",
            "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.0-alpha.2.uf2",
            "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.0-rc.0.uf2",
            "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.0-rc.2.uf2",
            "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.0.uf2",
            "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.1.uf2",
            "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.2.uf2",
            "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.3.uf2",
            "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.4.uf2",
            "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.2.5.uf2",
            "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0-alpha.0.uf2",
            "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0-beta.0.uf2",
            "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0-beta.1.uf2",
            "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0-beta.2.uf2",
            "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0-rc.0.uf2",
            "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0-rc.1.uf2",
            "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0-rc.2.uf2",
            "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.0.uf2",
            "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.1.uf2",
            "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.2.uf2",
            "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-7.3.3.uf2",
            "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-alpha.0.uf2",
            "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-alpha.1.uf2",
            "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-beta.0.uf2",
            "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-beta.1.uf2",
            "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-beta.2.uf2",
            "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-beta.3.uf2",
            "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-beta.4.uf2",
            "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-beta.5.uf2",
            "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-beta.6.uf2",
            "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-rc.0.uf2",
            "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-rc.1.uf2",
            "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0-rc.2.uf2",
            "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.0.uf2",
            "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.2.uf2",
            "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.3.uf2",
            "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.4.uf2",
            "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.0.5.uf2",
            "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.1.0-beta.0.uf2",
            "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.1.0-beta.1.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/adafruit_qtpy_rp2040/en_US/adafruit-circuitpython-adafruit_qtpy_rp2040-en_US-9.2.9.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/vcc_gnd_yd_rp2040/`, `boards/waveshare_rp2040_zero/`,
    `boards/adafruit_feather_rp2040/` and `boards/adafruit_itsybitsy_rp2040/`). Resolve it before
    booting - `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(extras=(lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[1:]), firmware=FIRMWARE)


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
