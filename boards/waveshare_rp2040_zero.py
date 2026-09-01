"""BoardSpec definition for the **Waveshare RP2040-Zero** (https://www.waveshare.com/product/rp2040-zero.htm)
- a tiny (18x23.5mm) castellated-module RP2040 board, electrically a Pico-class board with the same
2 MiB flash geometry as a plain Pico but with no plain GPIO25 LED at all: the only onboard indicator
is a single **WS2812 RGB LED on GPIO16**. Built as a worked `--board-spec` example (docs/records/0066
flagged this board first to pick up - the smallest candidate on that survey, one `Ws2812` and
nothing else); load it with e.g.:

    rp2040py micropython --board-spec boards/waveshare_rp2040_zero.py:BOARD -c "<probe>"
    rp2040py micropython --circuitpython --board-spec boards/waveshare_rp2040_zero.py:BOARD -c "<probe>"
    PYTHONPATH=. rp2040py micropython --board-spec boards.waveshare_rp2040_zero:BOARD ...

Nothing is downloaded when this module is imported: `firmware` is data, and
`rp2040py.boards.resolve_firmware()` turns it into a concrete image only when something actually
boots the board (docs/records/0059).

Lives outside `src/rp2040py` on purpose - a `--board-spec` target, not part of the installed
package or the built-in `--board` registry. It clears neither item 2 (a `firmware_specs.json`
entry) nor item 5 (a named maintainer) of 0059's promotion checklist.

File named `waveshare_rp2040_zero.py` after both firmwares' own board id - MicroPython's
`ports/rp2/boards/WAVESHARE_RP2040_ZERO`, case-normalized, and CircuitPython's own
`waveshare_rp2040_zero` (they agree here, unlike `boards/weactstudio/`).

Every number below is derived from a local checkout of both upstream ports at their current tags,
not guessed (docs/records/0027's "3g rule"):

- MicroPython, `ports/rp2/boards/WAVESHARE_RP2040_ZERO/`: `board.json` ("RGB LED" feature, MCU
  `rp2040`), `pins.csv` (`NEOPIXEL,GPIO16` - the only entry in the file), `mpconfigboard.h`
  (`MICROPY_HW_BOARD_NAME "Waveshare RP2040-Zero"`, USB VID `0x2E8A`/PID `0x101F`,
  `MICROPY_HW_FLASH_STORAGE_BYTES (1408 * 1024)` - no `MICROPY_HW_LED_PIN` at all, confirming no
  plain LED), and the pico-sdk board header `lib/pico-sdk/src/boards/include/boards/
  waveshare_rp2040_zero.h` (`PICO_DEFAULT_WS2812_PIN 16`, `PICO_FLASH_SIZE_BYTES (2 * 1024 *
  1024)`, no board-specific `link.ld`/firmware-size override).
- CircuitPython, `ports/raspberrypi/boards/waveshare_rp2040_zero/`: `mpconfigboard.h`
  (`MICROPY_HW_NEOPIXEL (&pin_GPIO16)`, agreeing with MicroPython's pin), `mpconfigboard.mk` (same
  USB VID/PID `0x2E8A`/`0x101F`, `EXTERNAL_FLASH_DEVICES = "W25Q16JVxQ"` - 16 Mbit = 2 MiB,
  agreeing with the pico-sdk header's `PICO_FLASH_SIZE_BYTES`), and `pins.c` (only `NEOPIXEL` ->
  GPIO16 beyond the plain `GPx`/UART/analog entries - no `LED`, no `BUTTON`). No board-specific
  `link.ld` (confirmed absent from the directory listing), so `ports/raspberrypi/link-rp2040.ld`'s
  default `firmware_size = 1020K` applies, same derivation `boards/vcc_gnd_yd_rp2040/` and
  `boards/weactstudio/` already document.

Flash geometry is **identical to a plain Pico**, not merely similar: `PICO_FLASH_SIZE_BYTES` (2
MiB) and `MICROPY_HW_FLASH_STORAGE_BYTES` (1408 KiB) both match `RPI_PICO`'s own values byte for
byte, giving the same `fs_start = 2 MiB - 1408 KiB = 0xa0000`, `fs_blockcount = 1408 KiB / 4 KiB =
352` this project already uses for `"pico"` in `firmware_specs.json` (docs/records/0035's
derivation). CircuitPython's own start is the generic `firmware_size + CIRCUITPY_INTERNAL_NVM_SIZE
= 1020 KiB + 4 KiB = 0x100000`, `fs_blockcount = 512` following this project's existing
CircuitPython entries (0035: only the *start* has to be right, since the emulated flash buffer is
16 MiB) - same values `boards/vcc_gnd_yd_rp2040/` and `boards/weactstudio/` use.

Onboard extras:

- The RGB LED: `Ws2812(gpio=16)` (`rp2040py.external.ws2812`) - a real WS2812-class part per both
  ports' `NEOPIXEL` naming, so `Ws2812`'s default `color_order="GRB"` applies. Live-boot-verified
  (`tests/ws2812_boot_decode.py`'s pattern, run against this board file) that CircuitPython drives
  this LED as its own status indicator from boot, same as `vcc_gnd_yd_rp2040` - 11 frames decoded
  before any guest code ran in one measured boot - so `board_with(on_pixels)` below sees pixels
  with no guest code at all. MicroPython's build declares no such default and needs guest code to
  write to `NEOPIXEL`/GPIO16 itself.
- BOOTSEL: `BootselButton`, wired identically on every RP2040 board that boots from QSPI flash
  (docs/records/0050/0051).
- RESET: `ResetButton` (`rp2040py.external.reset_button`), on the strength of the wiki citation
  this file already carried - Waveshare's own wiki (http://www.waveshare.com/wiki/RP2040-Zero)
  documents a BOOT and a RESET pad, and RESET pulls RUN. **That citation could not be re-read on
  2026-08-20** (waveshare.com returns HTTP 403 to this project's fetches, and waveshare.net refuses
  the connection), so it rests on the session that added this board having read it, not on a fresh
  check. Modelled since docs/records/0089's Phase 4 (which closes docs/records/0057). Also not
  modelled: USB-C, and the RP2040's own RTC (not board-specific). No plain GPIO25 LED exists on
  this board at all - confirmed absent from every source cited above, not merely left out.
"""

from collections.abc import Callable

from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.reset_button import ResetButton
from rp2040py.external.ws2812 import Ws2812
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

__all__ = ("BOARD", "FIRMWARE", "RGB_GPIO", "board_with")

RGB_GPIO = 16

_EXTRAS = (lambda: Ws2812(gpio=RGB_GPIO), BootselButton, ResetButton)

# Full version history from
#   uv run scripts/fetch_firmware.py list --family micropython --slug WAVESHARE_RP2040_ZERO
#   uv run scripts/fetch_firmware.py list --family circuitpython --slug waveshare_rp2040_zero
# Re-run the same way to pick up new releases. A value may equally be a local `.uf2` path, which is
# what makes a hand-written board file able to be fully offline.
FIRMWARE = {
    "micropython": BoardFirmwareSpec(
        default_tag="1.28.0",
        fw={
            "1.28.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260406-v1.28.0.uf2",
            "1.29.0-preview.718.g2e3304a128": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260816-v1.29.0-preview.718.g2e3304a128.uf2",
            "1.29.0-preview.707.g1827631282": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260813-v1.29.0-preview.707.g1827631282.uf2",
            "1.29.0-preview.697.g2aa39667b4": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260812-v1.29.0-preview.697.g2aa39667b4.uf2",
            "1.29.0-preview.678.g5f2181f938": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260807-v1.29.0-preview.678.g5f2181f938.uf2",
            "1.29.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260824-v1.29.0.uf2",
            "1.30.0-preview.24.g8162451850": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260831-v1.30.0-preview.24.g8162451850.uf2",
            "1.30.0-preview.8.gf668077be2": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260827-v1.30.0-preview.8.gf668077be2.uf2",
            "1.29.0-preview.731.g1c3c201149": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_ZERO-20260818-v1.29.0-preview.731.g1c3c201149.uf2",
        },
        layout={"fs_start": "0xa0000", "fs_blockcount": 352, "fs_blocksize": 4096},
    ),
    "circuitpython": BoardFirmwareSpec(
        default_tag="10.2.1",
        fw={
            "10.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-alpha.2.uf2",
            "10.0.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-alpha.3.uf2",
            "10.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-alpha.4.uf2",
            "10.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-alpha.5.uf2",
            "10.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-alpha.6.uf2",
            "10.0.0-alpha.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-alpha.7.uf2",
            "10.0.0-alpha.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-alpha.8.uf2",
            "10.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-beta.0.uf2",
            "10.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-beta.1.uf2",
            "10.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-beta.2.uf2",
            "10.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-beta.3.uf2",
            "10.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0-rc.0.uf2",
            "10.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.0.uf2",
            "10.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.1.uf2",
            "10.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.2.uf2",
            "10.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.0.3.uf2",
            "10.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.1.0-beta.0.uf2",
            "10.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.1.0-beta.1.uf2",
            "10.1.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.1.0-rc.1.uf2",
            "10.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.1.1.uf2",
            "10.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.1.3.uf2",
            "10.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.1.4.uf2",
            "10.2.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.2.0-alpha.1.uf2",
            "10.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.2.0-rc.0.uf2",
            "10.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.2.0.uf2",
            "10.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.2.1.uf2",
            "10.3.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.3.0-alpha.1.uf2",
            "10.3.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.3.0-alpha.2.uf2",
            "10.3.0-alpha.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.3.0-alpha.3.uf2",
            "10.3.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.3.0-alpha.4.uf2",
            "7.2.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.0-alpha.2.uf2",
            "7.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.0-rc.0.uf2",
            "7.2.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.0-rc.2.uf2",
            "7.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.0.uf2",
            "7.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.1.uf2",
            "7.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.2.uf2",
            "7.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.3.uf2",
            "7.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.4.uf2",
            "7.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.2.5.uf2",
            "7.3.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0-alpha.0.uf2",
            "7.3.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0-beta.0.uf2",
            "7.3.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0-beta.1.uf2",
            "7.3.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0-beta.2.uf2",
            "7.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0-rc.0.uf2",
            "7.3.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0-rc.1.uf2",
            "7.3.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0-rc.2.uf2",
            "7.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.0.uf2",
            "7.3.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.1.uf2",
            "7.3.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.2.uf2",
            "7.3.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-7.3.3.uf2",
            "8.0.0-alpha.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-alpha.0.uf2",
            "8.0.0-alpha.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-alpha.1.uf2",
            "8.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-beta.0.uf2",
            "8.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-beta.1.uf2",
            "8.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-beta.2.uf2",
            "8.0.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-beta.3.uf2",
            "8.0.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-beta.4.uf2",
            "8.0.0-beta.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-beta.5.uf2",
            "8.0.0-beta.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-beta.6.uf2",
            "8.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-rc.0.uf2",
            "8.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-rc.1.uf2",
            "8.0.0-rc.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0-rc.2.uf2",
            "8.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.0.uf2",
            "8.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.2.uf2",
            "8.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.3.uf2",
            "8.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.4.uf2",
            "8.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.0.5.uf2",
            "8.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.1.0-beta.0.uf2",
            "8.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.1.0-beta.1.uf2",
            "8.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.1.0-beta.2.uf2",
            "8.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.1.0-rc.0.uf2",
            "8.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.1.0.uf2",
            "8.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.0-beta.0.uf2",
            "8.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.0-beta.1.uf2",
            "8.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.0-rc.0.uf2",
            "8.2.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.0-rc.1.uf2",
            "8.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.0.uf2",
            "8.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.1.uf2",
            "8.2.10": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.10.uf2",
            "8.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.2.uf2",
            "8.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.3.uf2",
            "8.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.4.uf2",
            "8.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.5.uf2",
            "8.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.6.uf2",
            "8.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.7.uf2",
            "8.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.8.uf2",
            "8.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-8.2.9.uf2",
            "9.0.0-alpha.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-alpha.2.uf2",
            "9.0.0-alpha.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-alpha.4.uf2",
            "9.0.0-alpha.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-alpha.5.uf2",
            "9.0.0-alpha.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-alpha.6.uf2",
            "9.0.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-beta.0.uf2",
            "9.0.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-beta.1.uf2",
            "9.0.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-beta.2.uf2",
            "9.0.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-rc.0.uf2",
            "9.0.0-rc.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0-rc.1.uf2",
            "9.0.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.0.uf2",
            "9.0.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.1.uf2",
            "9.0.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.2.uf2",
            "9.0.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.3.uf2",
            "9.0.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.4.uf2",
            "9.0.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.0.5.uf2",
            "9.1.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.0-beta.0.uf2",
            "9.1.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.0-beta.1.uf2",
            "9.1.0-beta.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.0-beta.2.uf2",
            "9.1.0-beta.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.0-beta.3.uf2",
            "9.1.0-beta.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.0-beta.4.uf2",
            "9.1.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.0-rc.0.uf2",
            "9.1.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.0.uf2",
            "9.1.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.1.uf2",
            "9.1.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.2.uf2",
            "9.1.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.3.uf2",
            "9.1.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.1.4.uf2",
            "9.2.0-alpha.2350": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.0-alpha.2350.uf2",
            "9.2.0-alpha.2351": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.0-alpha.2351.uf2",
            "9.2.0-beta.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.0-beta.0.uf2",
            "9.2.0-beta.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.0-beta.1.uf2",
            "9.2.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.0-rc.0.uf2",
            "9.2.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.0.uf2",
            "9.2.1": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.1.uf2",
            "9.2.2": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.2.uf2",
            "9.2.3": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.3.uf2",
            "9.2.4": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.4.uf2",
            "9.2.5": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.5.uf2",
            "9.2.6": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.6.uf2",
            "9.2.7": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.7.uf2",
            "9.2.8": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.8.uf2",
            "9.2.9": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-9.2.9.uf2",
            "10.3.0-rc.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.3.0-rc.0.uf2",
            "10.3.0": "https://adafruit-circuit-python.s3.amazonaws.com/bin/waveshare_rp2040_zero/en_US/adafruit-circuitpython-waveshare_rp2040_zero-en_US-10.3.0.uf2",
        },
        layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
    ),
}


def board_with(on_pixels: "Callable[[list[tuple[int, ...]]], None]") -> BoardSpec:
    """The same board, with the RGB LED's `on_pixels` wired up - the one piece a plain
    `--board-spec` target cannot carry, since `BoardSpec.extras` holds zero-arg factories and
    nothing hands the constructed device back to the caller (same pattern as
    `boards/vcc_gnd_yd_rp2040/`). Resolve it before booting -
    `rp2040py.boards.resolve_firmware(board_with(...), "circuitpython")`."""
    return BoardSpec(extras=(lambda: Ws2812(gpio=RGB_GPIO, on_pixels=on_pixels), *_EXTRAS[1:]), firmware=FIRMWARE)


BOARD = BoardSpec(extras=_EXTRAS, firmware=FIRMWARE)
