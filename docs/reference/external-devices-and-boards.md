<!-- Reference (living how-to). See docs/records/0049-external-device-authoring-docs.md for the
     design history/decisions behind everything below - this file is the how-to, that record is
     the "why". -->

# Writing your own external device or board

Two separate things, not one - pick the one you actually need:

1. **A custom device on an existing board** (`pico`/`pico_w`) - already fully solved, zero
   library changes needed. See "Writing an `ExternalDevice`" below.
2. **A custom board** - your own combination of devices, and optionally your own firmware image
   and flash layout - via `boards.BoardSpec`. See "Writing a `BoardSpec`" below.

Both build on the same extension point: `ExternalDevice`, a `Protocol` with a single method,
`attach(rp2040) -> None`, and `attach_external_devices(rp2040, *devices)`, which calls `attach()`
on each device in turn. Import both from `rp2040py.external`:

```python
from rp2040py.external import ExternalDevice, attach_external_devices
```

(`rp2040py.external.device` still works and is where they are defined - the package-level names
exist so the extension point does not read like `rp2040py.device`, which is the unrelated
*host-side* API: `MicroPythonDevice` and friends.)

## Table of Contents

- [The attach-timing rule](#the-attach-timing-rule)
- [Writing an `ExternalDevice`](#writing-an-externaldevice)
- [Writing a `BoardSpec`](#writing-a-boardspec)
  - [Scenario A: your own device mix, existing firmware](#scenario-a-your-own-device-mix-existing-firmware)
  - [Scenario B: a fully custom board (your own firmware)](#scenario-b-a-fully-custom-board-your-own-firmware)
  - [Resolving a `BoardSpec`](#resolving-a-boardspec)
  - [Using a `BoardSpec`](#using-a-boardspec)
  - [Ready-made examples in this repo](#ready-made-examples-in-this-repo)
  - [Seeing what a display device drew](#seeing-what-a-display-device-drew)
- [Caveats worth knowing](#caveats-worth-knowing)

## The attach-timing rule

Devices may only be attached **before** `Simulator.start_execution()` (or `astart()`/
`start_async()`, which call it internally). What's wired to a board is fixed at power-on by
design, not hot-pluggable - `GPIOPin`'s listener set is iterated unsynchronized on the engine-room
thread once execution starts (see [record 0029](../records/0029-cyw43-board-composition.md)'s
"attach()/attach_external_devices() timing" section). Attaching a device after boot is a race, not
a supported operation - always attach first, boot second. (What an *already-attached* device does
afterward - if its own ongoing work needs to talk back to the engine room, e.g. real I/O - is a
separate question, covered by [record 0030](../records/0030-external-device-concurrency.md).)

## Writing an `ExternalDevice`

The whole contract is `attach(rp2040) -> None`: wire up whatever GPIO/SPI/peripheral surface your
device needs, using the `rp2040` object handed to you. `external/led_mock.py`'s `LEDMock` is the
minimal end of that spectrum - one GPIO pin, a state flag, a toggle counter - kept deliberately
tiny as a validation vehicle for this exact mechanism. Its own `attach()`, trimmed of comments:

```python
def attach(self, rp2040: "RP2040") -> None:
    def _on_change(new_state: "GPIOPinState", _old_state: "GPIOPinState") -> None:
        new_on = new_state == GPIOPinState.HIGH
        if new_on != self.on:
            self.on = new_on
            self.toggle_count += 1

    self._unsubscribe = rp2040.gpio[self.gpio].add_listener(_on_change)
```

`GPIOPin.add_listener()` is the same primitive `Ws2812` (which decodes a single-wire pulse-width
protocol from nothing but edge timestamps) and `Epd2in9G` (a full SPI e-paper display driver, the
richer end of the same spectrum) and `Cyw43439` build on - `rp2040.gpio[n]` for a plain GPIO pin,
or the relevant peripheral object (`rp2040.spi[n]`, ...) for anything else your device needs to
watch or drive.

Attaching your own device to an existing board (scenario 1 above) needs no new library code -
`demo/eink_run.py` is a complete worked example:

```python
from rp2040py.device import MicroPythonDevice
from rp2040py.external.device import attach_external_devices
from my_devices import MyDevice

device = MicroPythonDevice(board=board)  # see "Writing a BoardSpec" for `board`
attach_external_devices(device.mcu, MyDevice())  # before device.start_async()/astart()
device.start_async().result()
```

## Writing a `BoardSpec`

`boards.BoardSpec` is what `--board {pico,pico_w}` resolves to internally - a plain, public,
frozen dataclass, not a closed registry:

```python
@dataclass(frozen=True)
class BoardSpec:
    mcu: type[RP2040] = RP2040
    extras: tuple[ExternalDeviceFactory, ...] = ()
    layout: FlashLayout | None = None  # resolved, or an explicit override
    image: str | Path | None = None  # resolved, or an explicit override - never set by an author
    firmware: dict[str, BoardFirmwareSpec] | None = None  # how to resolve those two
```

`boards.BOARDS["pico"]` is an ordinary instance of it, nothing magic - its `firmware` comes from
this project's own `firmware_specs.json`, and a board file's comes from whoever wrote it:

```python
BOARDS = {
    "pico": BoardSpec(extras=(lambda: LEDMock(gpio=25), BootselButton), firmware={...}),
    "pico_w": BoardSpec(extras=(lambda: LEDMock(gpio=25), BootselButton, Cyw43439), firmware={...}),
}
```

`extras` is a tuple of **zero-arg factories**, not shared instances - each one is called fresh
per board construction, so two independently-built boards never end up sharing one device's
mutable state (GPIO listeners, etc.).

`firmware` is keyed by **firmware family** - `"micropython"`, `"circuitpython"`, `"kaluma"` - so
one spec describes one *board*, for as many firmwares as run on it. Nothing is downloaded when
your board file is imported: it declares data, and the CLI/SDK resolves it when something actually
boots (see "Resolving a `BoardSpec`" below, and
[record 0059](../records/0059-boardspec-firmware-resolution.md)).

### Scenario A: your own device mix, existing firmware

Start from `pico`'s own `extras` and add your device to it:

```python
import dataclasses
from rp2040py.boards import BOARDS, resolve_firmware
from my_devices import MyDevice

my_board = dataclasses.replace(BOARDS["pico"], extras=(*BOARDS["pico"].extras, MyDevice))
my_board = resolve_firmware(my_board, "micropython", "1.28.0")  # or omit the tag for the default
```

`BOARDS["pico"]` already carries `pico`'s own `firmware` for every family this project ships, so
replacing `extras` keeps all of it - resolving your board is then the same one call any other spec
gets. `resolve_board_spec(board, firmware_spec, tag=None)` is still there as the board-name
shortcut `--board` uses (`BOARDS[board]` plus that family's resolved image/layout, in one call),
but it always returns `BOARDS[board]`'s *original* `extras`, so it is the wrong tool once you have
your own.

### Scenario B: a fully custom board (your own firmware)

A board this project doesn't ship can't have its flash layout guessed or reused from `pico`/
`pico_w` - every real board's firmware places its filesystem at a different offset, sized against
that board's own compiled binary (see
[record 0035](../records/0035-board-aware-fs-flash-offset.md)). You supply it directly, having
derived it the same way this project derives `pico`'s/`pico_w`'s own numbers (from the firmware's
real upstream board config - e.g. `ports/rp2/boards/<BOARD>/mpconfigboard.h` for MicroPython):

```python
from rp2040py.boards import BoardSpec
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock
from rp2040py.utils.firmware_retrieve import BoardFirmwareSpec

my_board = BoardSpec(
    extras=(lambda: LEDMock(gpio=25), BootselButton),
    firmware={
        "micropython": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "/path/to/your/firmware.uf2"},  # or an https:// URL
            layout={"fs_start": "0x180000", "fs_blockcount": 352, "fs_blocksize": 4096},
        )
    },
)
```

That is the whole file - **you never set `image` yourself**. `fw`'s values are a URL *or* a local
path (a URL is downloaded once and cached under `~/.cache/rp2040py`; a local path is used as-is),
so a board that pins a local `.uf2` works offline by construction. `layout` uses the same hex-string
convention `firmware_specs.json` does, and `prog_start` is Kaluma-only (its separate YMODEM "user
program" region) - leave it out for MicroPython/CircuitPython, which keep user code inside the
filesystem itself.

Add a second key to serve a second firmware for the same hardware - the devices, pin map and
flash geometry belong to the board, not to whichever firmware is flashed:

```python
my_board = BoardSpec(
    extras=(lambda: LEDMock(gpio=25), BootselButton),
    firmware={
        "micropython": BoardFirmwareSpec(default_tag="1.28.0", fw={...}, layout={...}),
        "circuitpython": BoardFirmwareSpec(default_tag="10.2.1", fw={...}, layout={...}),
    },
)
```

The family key stays explicit even for a one-firmware board, and is never inferred from "there is
only one entry": the family picks the flash loader and the console behavior as well as the image
(`--circuitpython` means FAT12 and a different post-boot handshake), so a `--circuitpython` run
quietly booting a lone MicroPython declaration would be a trap. Asking for a family a spec doesn't
declare is an error that names what it *does* declare.

`layout` and `image` are still writable directly, as overrides for a spec that has already been
resolved (or a one-off you want to pin by hand) - see the resolution order below.

### Resolving a `BoardSpec`

The CLI does this for you: `--board`/`--board-spec` picks the spec, the subcommand picks the
family (`micropython`, `--circuitpython`, `kaluma`), and `--image` overrides the tag. From the SDK,
call it yourself before handing the spec to a `Device` class:

```python
from rp2040py.boards import resolve_firmware

board = resolve_firmware(my_board, "micropython")  # its own default_tag
board = resolve_firmware(my_board, "micropython", "1.27.0")  # a specific tag
board = resolve_firmware(my_board, "micropython", "/tmp/other.uf2")  # a local file/URL
```

In order: an explicit `image` argument wins (a local path/URL is used as given, a tag is resolved
against `firmware[family]`); else an `image` the spec already carries; else `firmware[family]` at
its own `default_tag`. `layout` follows the same order - an explicit `spec.layout` first, else
`firmware[family].layout`. `resolve_layout(spec, family)` gives you just the layout, without
resolving (or downloading) an image at all.

### Using a `BoardSpec`

**From the CLI**, save your board as a module-level attribute anywhere importable, then point
`--board-spec` at it - a file path (no package needed) or a dotted module path, `target:attr`:

```sh
rp2040py micropython --board-spec my_board.py:BOARD --littlefs littlefs.img
rp2040py kaluma --board-spec my_board.py:BOARD
rp2040py mklittlefs --board-spec my_board.py:BOARD -o littlefs.img app.py
rp2040py run --board-spec my_board.py:BOARD --image firmware.uf2
```

`--board-spec` is mutually exclusive with `--board`, and nothing else: `--image` (a tag, a path or
a URL) and `--fetch-fw-only` work with it exactly as they do with `--board`, as long as the spec
declares a `firmware` entry for the family being run. On `mklittlefs`, `--target` picks *which* of
the spec's families to size the image against, and is only needed when it declares more than one
(see [record 0059](../records/0059-boardspec-firmware-resolution.md), which superseded 0049's
stricter table). `RP2040PY_BOARD_SPEC` (an env var, same `target:attr` syntax) works the same way
as the flag, for a persistent local setup that doesn't want it typed every invocation -
`tests/pico_spec.py` is a real, CI-verified worked example of a board-spec file.

**From the SDK**, resolve the spec (see above) and pass it straight to a `Device` class - `board`
is always keyword-only, and always the *only* board-related argument (no separate `image` kwarg, no
board-name string):

```python
from rp2040py.boards import resolve_firmware
from rp2040py.device import MicroPythonDevice

async with MicroPythonDevice(board=resolve_firmware(my_board, "micropython")) as device:
    stdout, stderr = await device.aexec("print(1 + 1)")
```

A `Device` class never resolves on your behalf - it asserts that the spec it was handed already
carries an `image`, so nothing downloads firmware as a side effect of constructing a device.

### Ready-made examples in this repo

Worked `--board-spec` targets live under [`boards/`](../../boards/), outside `src/rp2040py` (they
are examples, not part of the installed package). Every number in every one of these is derived
from that board's own upstream firmware config rather than guessed (the "3g rule"), and every one
is live-boot-verified against real firmware:

| Board | Firmware | Highlight |
| --- | --- | --- |
| [weactstudio](../../boards/weactstudio/__init__.py) | MicroPython (4 flash variants) + CircuitPython | one `BoardSpec` per flash-size variant, built entirely from generic in-tree devices (`LEDMock`/`BootselButton`/`KeyMock`) - no board-specific device class needed |
| [vcc_gnd_yd_rp2040](../../boards/vcc_gnd_yd_rp2040/__init__.py) | CircuitPython only | declares **one** firmware family, because upstream only builds one for it (see below for the how-to); WS2812 RGB LED driven as CircuitPython's own status indicator, decodes real pixel frames with no guest code at all |
| [waveshare_rp2040_lcd_0_96](../../boards/waveshare_rp2040_lcd_0_96/__init__.py) | MicroPython + CircuitPython | a board whose point *is* its device: the onboard 160×80 ST7735S panel (`external/st7735s.py`), plus a `board_with(on_frame)` helper for the one thing a bare `--board-spec` target cannot do - hand the caller a way to receive the panel's frames (see below) |
| [waveshare_rp2040_zero](../../boards/waveshare_rp2040_zero.py) | MicroPython + CircuitPython | the smallest example - a single WS2812, nothing else |
| [adafruit_feather_rp2040](../../boards/adafruit_feather_rp2040.py) | MicroPython + CircuitPython | LED + WS2812 - and a worked example of a marketing claim (switchable NeoPixel power) contradicted by firmware source |
| [adafruit_itsybitsy_rp2040](../../boards/adafruit_itsybitsy_rp2040.py) | MicroPython + CircuitPython | LED + WS2812 (real switchable power pin this time) + a second BOOT button sourced from the vendor's own schematic |
| [adafruit_qtpy_rp2040](../../boards/adafruit_qtpy_rp2040.py) | MicroPython + CircuitPython | WS2812 + BOOT button - the same vendor-schematic pattern as ItsyBitsy, no plain LED |
| [garatronic_pybstick26_rp2040](../../boards/garatronic_pybstick26_rp2040.py) | MicroPython only | plain LED, smallest board with no CircuitPython twin |
| [machdyne_werkzeug](../../boards/machdyne_werkzeug.py) | MicroPython only | two plain LEDs - the first board whose LED is genuinely active-low (`LEDMock` gained `active_low` for it) |
| [nullbits_bit_c_pro](../../boards/nullbits_bit_c_pro.py) | MicroPython + CircuitPython | RGB LED as three separate active-low GPIO LEDs, not a `Ws2812` - CircuitPython drives all three as a PWM status indicator from boot |
| [pimoroni_picolipo](../../boards/pimoroni_picolipo.py) | MicroPython (2 flash variants) + CircuitPython | LiPo-charging board, `BOARD`/`BOARD_16MB` for its two flash sizes |
| [pimoroni_tiny2040](../../boards/pimoroni_tiny2040.py) | MicroPython (2 flash variants) + CircuitPython | RGB LED as three separate active-low GPIO LEDs, not a `Ws2812` (same shape as `nullbits_bit_c_pro`); `BOARD`/`BOARD_8MB` for its two flash sizes |
| [waveshare_rp2040_plus](../../boards/waveshare_rp2040_plus.py) | MicroPython (2 flash variants) + CircuitPython | plain LED + `BootselButton` only, no `pins.csv` at all upstream (uses the pico-sdk board header's own pin defaults directly); `BOARD`/`BOARD_16MB` for its two flash sizes |
| [seeed_xiao_rp2040](../../boards/seeed_xiao_rp2040.py) | MicroPython + CircuitPython | the only example with **both** kinds of RGB LED at once - a WS2812 (power pin GPIO11) *and* three separate active-low GPIO LEDs; its polarity is sourced from the vendor's own wiki, because neither firmware port states it for all three pins |
| [sparkfun_promicro](../../boards/sparkfun_promicro.py) | MicroPython + CircuitPython | WS2812 only, on GPIO25 - the pin a plain Pico puts its LED on, where upstream says outright there is no plain LED; the largest flash of any example (16 MiB, 15 MiB filesystem) |

Named after the firmware's own board id, case-normalized (`weactstudio` for MicroPython's
`ports/rp2/boards/WEACTSTUDIO`) - which is what keeps every number in a board file checkable
against a real upstream source. Where two firmwares disagree on the id, pick one and cite both in
the docstring.

**A directory (`boards/<name>/__init__.py`) is not required.** Only `weactstudio`,
`vcc_gnd_yd_rp2040` and `waveshare_rp2040_lcd_0_96` use one, predating the convention below; every
board added since is a single flat file (`boards/<name>.py`) per [record 0059](../records/0059-boardspec-firmware-resolution.md)'s
own text ("`my_board.py` - a single file is still fine"). A directory is for a board that needs a
device genuinely unique to it and not meant to be shared (`boards/<slug>/devices/` - see step 4 of
"Adding a new `ExternalDevice`" above) or otherwise needs more than one file; most boards need
neither, since their devices already live in `rp2040py.external`.

Every board here is loadable either as a file path or, with `PYTHONPATH=.`, as a dotted module -
the same either way, whether the board is a file or a package:

```sh
rp2040py micropython --board-spec boards/waveshare_rp2040_lcd_0_96/__init__.py:BOARD
rp2040py micropython --circuitpython --board-spec boards/waveshare_rp2040_lcd_0_96/__init__.py:BOARD
rp2040py micropython --board-spec boards/pimoroni_picolipo.py:BOARD_16MB
PYTHONPATH=. rp2040py micropython --board-spec boards.weactstudio:BOARD_FLASH_4M
```

### Seeing what a display device drew

A display device hands you raw pixels, never a picture: `Epd2in9G.on_frame` fires with a packed
2bpp e-paper buffer, `St7735s.on_frame` with a raw RGB565 one, and `Ws2812.on_pixels` with one
`(r, g, b)` tuple per LED in the frame it just latched. Nothing in `src/rp2040py` decodes
them - that is the caller's job, and deliberately so, since it keeps an image library out of the
package's dependencies ([record 0046](../records/0046-epd2in9g-external-device.md)). Two runnable
viewers in [`demo/`](../../demo/) do that decoding, and are how the screenshots in
[record 0056](../records/0056-st7735s-waveshare-lcd-board.md) were produced:

```sh
uv run --with pillow python demo/lcd_run.py --screenshot out        # ST7735S, MicroPython + demo/mp_lcd_demo.py
uv run --with pillow python demo/lcd_run.py --circuitpython --tkinter   # ST7735S, CircuitPython paints by itself
uv run --with pillow python demo/eink_run.py --screenshot out       # Waveshare 2.9" e-paper
```

[demo/README.md](../../demo/README.md) shows what each of those commands produces, with the
frames checked in under [`demo/screenshots/`](../../demo/screenshots/) at the panels' real pixel
sizes.

The shape is the same in both, and is what to copy for your own device:

1. Build the board with a callback closed over - `board_with(on_frame)` in the
   `waveshare_rp2040_lcd_0_96` board file, or `attach_external_devices(mcu, Epd2in9G(on_frame=...))`
   for a device you wire up yourself.
2. Hand frames to the drawing thread through a `queue.Queue`. `on_frame` fires on the device's own
   engine-room thread, and a GUI toolkit's widgets may only be touched from the thread that made
   them.
3. Decode in the consumer: unpack each pixel into RGB and write a PNG (Pillow), or blit it into a
   window. `--tkinter` shows frames live; `--screenshot PREFIX` writes `PREFIX_000.png`, ....

Two practical notes both runners now encode, learned the hard way:

- **Bound the run.** A guest script can stall, and CircuitPython's display *never* stops - it
  auto-refreshes at 60 fps from `board_init()`, so "run until it finishes" has no meaning there.
  `demo/lcd_run.py` defaults to 5 frames when dumping PNGs, and both runners take `--timeout`.
- **Offline images.** Firmware resolution goes through `retrieve()`, which checks
  `~/.cache/rp2040py` before downloading anything. Dropping a `.uf2` there under the exact filename
  its download URL ends with makes every path - CLI, SDK, these runners - work with no network; so
  does declaring a local path in the board file's own `fw` map. Importing a board file downloads
  nothing either way.

## Caveats worth knowing

- **`LEDMock` on `pico_w` is a placeholder, not hardware-accurate.** On a real Pico W the onboard
  LED is wired to the CYW43439 chip itself, not any RP2040 GPIO - `pico_w`'s `LEDMock(gpio=25)`
  entry exists purely to exercise the `ExternalDevice`/`attach_external_devices()` plumbing
  identically regardless of board, not to model real wiring.
- **A `BoardSpec` cannot hand you the devices it attached.** `extras` is a tuple of zero-arg
  factories, and `attach_external_devices()` returns nothing, so a device with a callback (a
  display's `on_frame`, say) can be *booted* through `--board-spec` but cannot deliver anything
  back to the caller. From the SDK, build the board with the callback already closed over - the
  `board_with(on_frame)` helper in `boards/waveshare_rp2040_lcd_0_96/` is the pattern.
  From the CLI there is no answer today; see [record 0056](../records/0056-st7735s-waveshare-lcd-board.md).
- **A state machine never runs more than one instruction per CPU instruction.** `RPPIO` paces its
  machines by `SM_CLKDIV` and `[delay]` in system clocks ([record
  0063](../records/0063-pio-clkdiv-and-delay-cycles.md)), so pulse-width protocols - WS2812,
  DHT11/22, servo PWM, IR codes, one-wire - decode at their real timings; what it cannot do is run
  *faster* than the CPU dispatches instructions, which is what a divider of 1 would need. The
  ceiling is deliberate (`clock.tick()` must run between PIO steps, [record
  0043](../records/0043-pio-dma-first-batch-race.md)), and for the same reason PIO does not run
  through a CPU idle jump. Anything asking for a divider above ~1.4 - which is every pulse-width
  driver - is exact.
- **`ExternalDevice`'s surface is attach-only.** There's no `detach()`, no reset hook, no shutdown
  participation - fine for in-tree use (every implementation is reviewed here), but worth knowing
  if you're relying on a custom device to clean up after itself. See
  [record 0049](../records/0049-external-device-authoring-docs.md) for the open question this
  leaves.
