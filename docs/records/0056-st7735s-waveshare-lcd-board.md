# 0056. `St7735s` external device + the `WAVESHARE_RP2040_LCD_0_96` board spec

- Status: Implemented — verified (2026-08-17)
- Conceived: 2026-08-17 · Implemented: 2026-08-17
- Related: 0046 (`Epd2in9G` — the `ExternalDevice`-shaped display this copies wholesale: the
  `on_frame`-hands-raw-bytes boundary, the per-byte SPI pacing, the flat-module layout), 0049
  (`BoardSpec`/`--board-spec` board authoring, and its `WEACTSTUDIO` addendum — the community-board
  example this is the second of), 0029/0030 (`ExternalDevice` composition + attach-timing
  contract), 0035 (board-aware flash offsets — the derivation this board's `fs_start` repeats),
  0050/0051 (BOOTSEL wiring, reused unchanged here)

## Context

Asked for directly: *"було б непогано реалізувати дошку в boards/micropython з девайсами для
неї"*, pointing at MicroPython's own `WAVESHARE_RP2040_LCD_0_96` port and its v1.28.0 firmware.
The board is a Pico-class RP2040 (2 MiB flash, USB-C, battery header, MP28164 buck-boost) with a
0.96inch 160x80 65K IPS panel soldered to SPI1 — so unlike `WEACTSTUDIO` (0049's addendum), which
is "a Pico with different flash numbers", this one only means anything with a device attached.
That made it two pieces of work, not one: a new `ExternalDevice` for the panel's controller, and
the board spec that wires it.

## Where it lives: `boards/micropython/WAVESHARE_RP2040_LCD_0_96/`, not `boards/micropython/waveshare/...`

The question that opened this session was whether an e-ink/LCD demo board should live under a
*vendor* directory (`boards/micropython/waveshare/...`). Answered no, and this board is why the
answer generalizes: the existing level under `boards/micropython/` is **MicroPython's own upstream
board id** (`WEACTSTUDIO` = `ports/rp2/boards/WEACTSTUDIO`), which is exactly what makes a board
file checkable against a real upstream source. This board's upstream id already carries the vendor
in it — `WAVESHARE_RP2040_LCD_0_96` — so a `waveshare/` level would both duplicate that and break
the one-to-one mapping to `ports/rp2/boards/<BOARD>` that the derivation depends on. A HAT-plus-Pico
combination (the `demo/eink_run.py` case that prompted the question) has *no* upstream board id at
all — it boots plain `RPI_PICO` firmware — so it isn't a board in this directory's sense either;
see 0046's "Scope: demo files stay in `demo/`".

## The numbers, derived not guessed (0027's "3g rule")

| source | value |
|---|---|
| `ports/rp2/boards/WAVESHARE_RP2040_LCD_0_96/mpconfigboard.cmake` | `PICO_BOARD = waveshare_rp2040_lcd_0.96`, `MICROPY_HW_FLASH_STORAGE_BYTES = 1441792` (1408 KiB) |
| pico-sdk `src/boards/include/boards/waveshare_rp2040_lcd_0.96.h` | `PICO_FLASH_SIZE_BYTES = 2 MiB`, `WAVESHARE_LCD_SPI 1`, `DC 8`, `CS 9`, `SCLK 10`, `TX 11`, `RST 12`, `BL 25`, `PICO_SMPS_MODE_PIN 23`, and explicitly *no* `PICO_DEFAULT_LED_PIN` / *no* `PICO_DEFAULT_WS2812_PIN` |
| `ports/rp2/boards/WAVESHARE_RP2040_LCD_0_96/mpconfigboard.h` | `MICROPY_HW_SPI1_SCK 10`, `MICROPY_HW_SPI1_MOSI 11` (MicroPython's default SPI1 *is* the panel bus), `MICROPY_HW_SPI1_MISO 8` (overlaps LCD DC; the panel is write-only and the vendor driver passes `miso=None`) |

`fs_start = 0x200000 - 0x160000 = 0xa0000`, `fs_blockcount = 0x160000 / 4096 = 352`,
`fs_blocksize = 4096` (RP2040 sector-erase granularity, not firmware-specific). One flash variant
upstream, so one `BOARD` — no `FLASH_2M`/`4M`/`8M` fan-out like `WEACTSTUDIO`'s.

## The device: `src/rp2040py/external/st7735s.py`

Flat sibling module next to `epd2in9g.py`/`led_mock.py`, one class in one file — this project's
module-layout default (CLAUDE.md; 0028 is the one case that earned a package). Wire protocol taken
from Waveshare's own MicroPython sample driver
([`waveshare/Pico_code`'s `pico-lcd-0.96.py`](https://github.com/waveshare/Pico_code/blob/main/Python/Pico-LCD-0.96/pico-lcd-0.96.py),
`LCD_0inch96`): its `Init()` command stream, its `SetWindows()` CASET/RASET with the panel's `+1`
column / `+26` row GRAM offsets, and its `display()` bulk RAMWR of a 160x80 RGB565 framebuffer.

What is modelled: CASET/RASET address windows, RAMWR pixel streaming into a visible-window
framebuffer, MADCTL/COLMOD/DISPON/DISPOFF/INVON/INVOFF as recorded state, SWRESET and the RST pin,
and the per-byte SPI completion pacing 0046 already established (same DMA-pair reasoning, see
0044). Everything else in the init stream (`0xB1`-`0xB4`, `0xC0`-`0xC5`, gamma `0xE0`/`0xE1`) is
accepted and ignored — panel analog trim with nothing observable behind it.

Three deliberate non-goals, each written into the module docstring rather than left implicit:

- **`MADCTL` is recorded, not applied.** Pixels are stored in *controller address* space, which is
  what a viewer wants precisely because firmware picks one orientation at init and then addresses
  every window through it (the vendor driver: `MADCTL=0xA8` once, then plain left-to-right writes).
  Firmware that rotates by re-writing `MADCTL` between frames would be shown unrotated.
- **Only RGB565 (`COLMOD 0x05`) is decoded**; RGB444/RGB666 pixel bytes are dropped rather than
  half-decoded into a wrong picture.
- **No GRAM readback** (`RAMRD`) and no brightness: the backlight is a plain GPIO listener (below).

Frame semantics are the one place a TFT genuinely differs from 0046's e-Paper: there is no BUSY
line and no `DISPLAY_REFRESH` opcode to key a callback off, because a real panel is scanned out
continuously. So `on_frame` fires when the addressed window has been filled (the address counter
wrapping is what "a frame arrived" means), **and** on CS going high with unflushed pixels, so a
partial repaint still reaches the viewer. Raising CS also discards a half-received RGB565 pixel
rather than completing it from whatever the next transaction opens with — CS high ends the
transaction, and silently pairing bytes across transactions would invent pixels.

## Board extras, and the honest gap

`BOARD = BoardSpec(extras=(St7735s, BootselButton, lambda: LEDMock(gpio=25)), ...)`:

- `St7735s`'s constructor defaults *are* this board's pin map (that is where they came from), so
  it is usable as a bare zero-arg factory.
- `BootselButton` unchanged (0050/0051 — identical on every RP2040 booting from QSPI flash).
- The backlight (GPIO25) reuses `LEDMock`, which is honest for on/off but **not** a brightness
  model: the vendor driver dims with `PWM(Pin(25))`, and a duty cycle is not something a
  GPIO-level listener can report. Named `BACKLIGHT_GPIO` in the board file rather than "LED"
  because this board has no user LED at all.
- Not modelled at all: the MP28164 buck-boost and the battery header (no RP2040-visible interface),
  and USB-C (indistinguishable from any other USB port at this level).
- **The RESET button is deliberately not modelled**, and this is the one omission with a real
  design question behind it (raised while this landed: *"the board had also reset button"*).
  BOOTSEL works as an `ExternalDevice` because it shorts `GPIO_QSPI_SS`, an ordinary pad
  (0050/0051); RESET pulls **RUN**, which is not a GPIO and has no model here at all. The only
  live-reset path in the tree is `BaseDevice._on_watchdog_trigger()` - `mcu.reset(preserve_flash=
  True)` + `core.pc = FLASH_START_ADDRESS` + `cdc.reset()` - and its USB half is unreachable from
  an `ExternalDevice`, which is handed the `RP2040` and nothing else (no `USBCDC` back-reference
  exists on `usb_ctrl`). Building it properly means adding a reset hook on `RP2040` shaped like
  `RPWatchdog.on_watchdog_trigger`, that `BaseDevice` points at its own sequence and a
  `ResetButton` device calls (via `schedule_threadsafe()`, per 0030) - a public-API change that
  belongs in its own record, not smuggled in through a board file. Guest-side `machine.reset()`
  already reaches the same sequence today.

The gap worth naming: **`BoardSpec.extras` are zero-arg factories, and nothing hands the
constructed device back to the caller**, so a plain `--board-spec ...:BOARD` boots the panel but
cannot deliver its pixels anywhere. That is the same missing API 0049's still-open "does
`ExternalDevice` have enough surface to be public?" question covers, surfacing from a second
direction. Worked around locally, not solved globally: the board file exposes
`board_with(on_frame)`, which returns the same `BoardSpec` with the panel constructed around a
caller's callback, for SDK use (`MicroPythonDevice(board=board_with(frames.append))`). A general
fix — attached-device handles on the built MCU, or a `--device` CLI hook — stays 0049's call, not
this record's.

## Verification

- `tests/test_st7735s.py`: 14 unit tests driving the real wire (SPI1 `on_transmit` plus SIO-driven
  DC/CS/RST GPIOs, same `_drive_gpio_high()` shape as `test_led_mock.py`), covering window offsets,
  row advance by window width, off-panel address clipping, full-window vs. CS-flushed partial
  frames, the half-pixel discard, COLMOD gating, MADCTL recording, and both reset paths.
- **Live-booted against real MicroPython `v1.28.0` firmware for this exact board** (the
  `WAVESHARE_RP2040_LCD_0_96-20260406-v1.28.0.uf2` build):
  - `rp2040py mklittlefs --board-spec boards/micropython/WAVESHARE_RP2040_LCD_0_96/__init__.py:BOARD`
    produced a 1,441,792-byte image — exactly `MICROPY_HW_FLASH_STORAGE_BYTES`;
  - booting that image through the same board spec, firmware reports
    `_machine='Waveshare RP2040-LCD-0.96 with RP2040'`, `_build='WAVESHARE_RP2040_LCD_0_96'`,
    `os.statvfs('/')` returns `(4096, 4096, 352, 350, 350, ...)` and `main.py` from the littlefs
    image runs — i.e. the derived `fs_start`/`fs_blockcount` are right, not merely plausible;
  - a guest-side driver following the vendor sample command-for-command (only the backlight pin
    differs: 25 on this board vs. 13 on the Pico-LCD-0.96 HAT) drew two frames through
    `machine.SPI(1, 10_000_000)`, both decoded by `St7735s` into correct 160x80 RGB565 pictures
    (text, an hline, and three RGB rectangles, all in the right places and colors).
- `uv run pre-commit run --all-files` clean.

One environment caveat, recorded because it limits what was checked here: `micropython.org` is
unreachable from the sandbox this was built in, so the firmware was supplied as a local file and
seeded into `~/.cache/rp2040py` under the exact filename `retrieve()` derives from the URL. The
download URL in the board file is the official one from
<https://micropython.org/download/WAVESHARE_RP2040_LCD_0_96/>, but the *download path itself* was
never exercised here — the first real run on a networked machine is what proves it.

## Addendum (2026-08-17): two of the three non-goals closed, on request

Asked for the same day, right after this landed ("1. Робимо / 2. Треба зробити / 3. Ок тільки
документуємо" against the three deliberate non-goals above). So: `MADCTL` geometry and the
RGB444/RGB666 pixel formats are now implemented; backlight brightness stays documented-only.

### `MADCTL` is now applied, not merely recorded

The model no longer stores pixels in address space. Three steps, mirroring the controller:

1. **Address -> native frame memory.** MV (0x20) exchanges the CASET/RASET axes, then MX (0x40)/
   MY (0x80) mirror the resulting native column/row. The native window is 80x160 at offset
   (26, 1) — the transpose of the visible 160x80 at (1, 26) this record's table derives, because
   the module's reference orientation sets MV.
2. **Native -> glass** through one *fixed* mapping, `(x, y) = (native_height - 1 - ny, nx)`, which
   is the module's own physical orientation: its long axis is the native row axis, running
   right-to-left.
3. That fixed mapping is chosen so `MADCTL = 0xA8` — `REFERENCE_MADCTL`, what the vendor driver
   writes once in `Init()`, and the only value real firmware for this module uses — reduces to the
   identity. Substituting it: MV swaps, so `native_x = row - 26 = y` and `native_y = col - 1 = x`;
   MY mirrors `native_y` to `159 - x`; the glass mapping gives back `(159 - (159 - x), y) = (x, y)`.

Anchoring on the module's reference orientation rather than on the controller's native axes is the
one judgment call here, and it is what makes the result *usable*: a viewer wants the picture the
glass shows, and the glass is a landscape 160x80 panel whose "up" is a property of the module, not
of the ST7735S. A firmware that addresses in portrait (MV clear) now comes out rotated a quarter
turn on the output — which is what a real module does with such a write, and what the old
address-space model silently hid.

The **BGR bit (0x08) is still recorded and not applied**, deliberately, and this is not the same
kind of gap as the geometry was: whether that bit swaps red and blue *as a human sees them*
depends on how the glass's subpixels are wired, which the controller datasheet cannot say. On this
module the vendor driver sets BGR **and** its own color constants come out correct with no swap
here — verified against real firmware (the frames in "Verification" above are decoded with green
text green and red text red). Applying the bit would break that.

### RGB444 and RGB666 are decoded

`COLMOD 0x03` (12 bpp, two pixels packed into three bytes as `[R1 G1][B1 R2][G2 B2]`) and `0x06`
(18 bpp, six significant bits left-aligned in each of three bytes) are decoded and *normalized
into the same RGB565 framebuffer*, so `on_frame`'s contract stays one format no matter what
firmware clocks over the wire. 4-bit channels widen by bit replication (`0xf -> 0x1f`, not
`0x1e`), so full scale stays full scale. A pixel group left incomplete when CS rises is discarded,
the same rule the RGB565 half-pixel already followed. A `COLMOD` value this model does not know is
still dropped rather than misdecoded — the original reasoning stands for anything unimplemented.

### Verification of the addendum

`tests/test_st7735s.py` grew from 14 to 20 tests: MX mirroring the image vertically, clearing MY
mirroring it horizontally, a portrait `MADCTL` landing a quarter turn away on the glass, RGB666
normalization, RGB444 packing, an incomplete RGB444 group, and an unknown `COLMOD` still being
dropped. And the live check that matters most: re-running the real `WAVESHARE_RP2040_LCD_0_96`
v1.28.0 firmware with the vendor-shaped guest driver produces frames **byte-identical** to the
pre-change PNGs — the reference orientation really is the identity, on real firmware, not just in
the algebra above.

### Not done: backlight brightness

Left as documented-only, by the same request. Confirmed while writing this that the naive fix
would be actively misleading: `RPPWM.gpio_set()` calls `GPIOPin.check_for_updates()`, so a
`PWM(Pin(25))` backlight really does toggle the pin in the emulator, and `LEDMock` would faithfully
count PWM edges — a `toggle_count` climbing at the carrier frequency and an `on` flag flapping,
neither of which is brightness. A real model reads the PWM slice's own CC/TOP duty
(`rp2040.pwm`), which is a different device class, plus a decision about what a consumer of
`on_frame` should do with that number. Neither is in scope here.

## Follow-up: the RESET button gets its own record

The "deliberately not modelled" bullet above is now [0057](0057-run-pin-reset-hook.md) — a
design-only record (nothing implemented) covering the RUN pin, a `set_reset_hook()` on `RP2040`
versus moving `BaseDevice._on_watchdog_trigger()` into the MCU wholesale, the cost of `RP2040`
existing twice (pure-Python + `cdef class`), and the semantics still to settle (`WATCHDOG.REASON`
after a RUN reset, BOOTSEL-held-during-reset, what an attached `ExternalDevice` should be told).

## Addendum (2026-08-17): the CircuitPython twin, and what it proves

`boards/circuitpython/waveshare_rp2040_lcd_0_96/` - the *same physical board* under CircuitPython
`10.2.1`. Asked for right after the MicroPython one landed ("на базі створеної дошки можна
створити альтернативу в boards/circuitpython... думаю тепер це просто"), and it was: **no new
device code at all**, one new board file plus a `boards/circuitpython/__init__.py` package.

Naming follows the same rule from the top of this record, applied to the other firmware's
namespace: CircuitPython's own board id is lowercase (`ports/raspberrypi/boards/
waveshare_rp2040_lcd_0_96/`, and the `downloads.circuitpython.org` path), so the directory is too.
One board, two firmware families, two directories, each spelled the way its firmware spells it.

Flash layout, derived the same way 0035 audited CircuitPython's:
`CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR = CIRCUITPY_FIRMWARE_SIZE + CIRCUITPY_INTERNAL_NVM_SIZE`
(`ports/raspberrypi/mpconfigport.h`), with `firmware_size` defaulting to `1020K`
(`link-rp2040.ld`) and this board shipping **no** override - confirmed by its `mpconfigboard.mk`
carrying only USB ids/`CHIP_VARIANT`/`EXTERNAL_FLASH_DEVICES`/`CIRCUITPY__EVE`, and by
`boards/waveshare_rp2040_lcd_0_96/link.ld` being a 404 upstream. So `fs_start = 1020 KiB + 4 KiB =
0x100000`, the same as plain `pico` under CircuitPython (and unlike `pico_w`, which overrides to
1532K). `fs_blockcount` stays this project's generous `512` for the reason 0035 already gives.

### The part worth having built: it checks the MADCTL model independently

CircuitPython does not need a driver script - `board_init()` calls `display_init()`
(`board.c`), which constructs a `busdisplay` with `auto_refresh=true` at 60 fps. Booting the
`BoardSpec` therefore drives the emulated panel with **zero guest code**. And it drives it through
a *different orientation than the MicroPython vendor driver does*:

| | MicroPython (vendor driver) | CircuitPython (`board.c`) |
|---|---|---|
| `MADCTL` | `0xA8` (MY \| MV \| BGR) | `0xC8` (MY \| MX \| BGR) - no MV |
| offsets | `+1` column, `+26` row | `colstart=26`, `rowstart=1` - transposed |
| rotation | none (writes landscape directly) | displayio `rotation=90` |
| SPI | 10 MHz (this repo's demo) / 4 MHz (vendor) | 40 MHz |

Two firmwares, two memory->panel mappings, one piece of glass. The addendum above claimed that
anchoring the model on the module's physical orientation (rather than on the controller's native
axes) makes any `MADCTL` render the way the glass would show it; this is the case that could have
falsified it, since nothing about CircuitPython's choice was known when that code was written.
**It renders upright**: the CircuitPython boot console - "Board ID:waveshare_rp2040_lcd_0_96",
the UID line, the REPL prompt - comes out legible and correctly oriented, drawn progressively
through partial-window writes (which the CS-rise flush already handled).

### Verification

Against the real `adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.2.1.uf2`:

- 58 frames decoded from a plain boot, no guest script involved.
- Through the CLI's `--circuitpython --board-spec` path (the combination 0049's flag table lists
  as compatible, now actually exercised): `sys.implementation` reports
  `circuitpython (10, 2, 1)` / `_machine='Waveshare RP2040-LCD-0.96 with rp2040'` /
  `_build='waveshare_rp2040_lcd_0_96'`, `board.board_id` matches, `board.DISPLAY` is 160x80 at
  rotation 90, and `os.statvfs('/')` returns `(512, 512, 2008, 2002, 2002, ...)` with a real
  CIRCUITPY drive (`boot_out.txt`, `code.py`, `lib`, `settings.toml`) - i.e. the derived
  `fs_start` is right, since a wrong one leaves CircuitPython unable to mount or format it.

Both firmwares' screenshots above were taken with `demo/lcd_run.py` (added with this addendum,
the LCD counterpart of `demo/eink_run.py`): it builds the board through the board file's own
`board_with(on_frame)`, queues frames off the engine-room thread, and decodes RGB565 into PNGs or a
Tk window - with `demo/mp_lcd_demo.py`, a vendor-shaped guest driver, for the MicroPython side and
nothing at all for CircuitPython's. Writing it surfaced a bound-less-loop bug worth noting since
`demo/eink_run.py` had the same shape: "run until the firmware is done" is not a termination
condition for a display that auto-refreshes forever, and `exec_file_async(timeout=None)` is not one
either when a guest script stalls - both runners now take `--timeout`, and `lcd_run.py` defaults to
5 frames when dumping PNGs. The how-to lives in
[reference/external-devices-and-boards.md](../reference/external-devices-and-boards.md)'s "Seeing
what a display device drew".

The backlight caveat gets sharper here and is worth repeating: CircuitPython drives GPIO25 as a
real PWM (`board.c` passes a 50 kHz backlight frequency and `brightness = 1.0f`), so the
`LEDMock` attached to it reports edges, not brightness - see this record's first addendum.
