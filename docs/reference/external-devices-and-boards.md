<!-- Reference (living how-to). See docs/records/0049-external-device-authoring-docs.md for the
     design history/decisions behind everything below - this file is the how-to, that record is
     the "why". -->

# Writing your own external device or board

Two separate things, not one - pick the one you actually need:

1. **A custom device on an existing board** (`pico`/`pico_w`) - already fully solved, zero
   library changes needed. See "Writing an `ExternalDevice`" below.
2. **A custom board** - your own combination of devices, and optionally your own firmware image
   and flash layout - via `boards.BoardSpec`. See "Writing a `BoardSpec`" below.

Both build on the same extension point: `rp2040py.external.device.ExternalDevice`, a `Protocol`
with a single method, `attach(rp2040) -> None`. `attach_external_devices(rp2040, *devices)` calls
`attach()` on each device in turn.

## Table of Contents

- [The attach-timing rule](#the-attach-timing-rule)
- [Writing an `ExternalDevice`](#writing-an-externaldevice)
- [Writing a `BoardSpec`](#writing-a-boardspec)
  - [Scenario A: your own device mix, existing firmware](#scenario-a-your-own-device-mix-existing-firmware)
  - [Scenario B: a fully custom board (your own firmware)](#scenario-b-a-fully-custom-board-your-own-firmware)
  - [Using a `BoardSpec`](#using-a-boardspec)
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

`GPIOPin.add_listener()` is the same primitive `Epd2in9G` (a full SPI e-paper display driver, the
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
    layout: FlashLayout | None = None  # None where no filesystem concept applies
    image: str | Path | None = None  # an already-resolved local file path
```

`boards.BOARDS["pico"]` is an ordinary instance of it, nothing magic:

```python
BOARDS = {
    "pico": BoardSpec(extras=(lambda: LEDMock(gpio=25), BootselButton)),
    "pico_w": BoardSpec(extras=(lambda: LEDMock(gpio=25), BootselButton, Cyw43439)),
}
```

`extras` is a tuple of **zero-arg factories**, not shared instances - each one is called fresh
per board construction, so two independently-built boards never end up sharing one device's
mutable state (GPIO listeners, etc.).

### Scenario A: your own device mix, existing firmware

Start from `pico`'s own `extras` and add your device to it:

```python
import dataclasses
from rp2040py.boards import BOARDS, resolve_board_spec
from rp2040py.utils.firmware_retrieve import MICROPYTHON
from my_devices import MyDevice

my_board = dataclasses.replace(BOARDS["pico"], extras=(*BOARDS["pico"].extras, MyDevice))
resolved = resolve_board_spec("pico", MICROPYTHON, "1.28.0")  # or None for the default tag
my_board = dataclasses.replace(my_board, image=resolved.image, layout=resolved.layout)
```

`resolve_board_spec(board, firmware_spec, tag=None)` is the same shortcut `--board` itself uses
internally - `BOARDS[board]`'s `mcu`/`extras` plus that firmware family's resolved image/flash
layout, combined into one ready-to-run `BoardSpec`. Reusing its `layout`/`image` (rather than
`BOARDS["pico"]`'s bare `mcu`/`extras`, which carry neither) is what makes the two-step
`dataclasses.replace()` above necessary - `resolve_board_spec()` itself always returns a spec with
`BOARDS[board]`'s *original* `extras`, not yours.

### Scenario B: a fully custom board (your own firmware)

A board this project doesn't ship can't have its flash layout guessed or reused from `pico`/
`pico_w` - every real board's firmware places its filesystem at a different offset, sized against
that board's own compiled binary (see
[record 0035](../records/0035-board-aware-fs-flash-offset.md)). You supply it directly, having
derived it the same way this project derives `pico`'s/`pico_w`'s own numbers (from the firmware's
real upstream board config - e.g. `ports/rp2/boards/<BOARD>/mpconfigboard.h` for MicroPython):

```python
from rp2040py.boards import BoardSpec, FlashLayout
from rp2040py.external.bootsel_button import BootselButton
from rp2040py.external.led_mock import LEDMock

my_board = BoardSpec(
    extras=(lambda: LEDMock(gpio=25), BootselButton),
    layout=FlashLayout(fs_start=0x180000, fs_blockcount=352, fs_blocksize=4096),
    image="/path/to/your/firmware.uf2",  # a local file - never a version tag or URL
)
```

`FlashLayout.prog_start` is Kaluma-only (its separate YMODEM "user program" region) - leave it
`None` for MicroPython/CircuitPython, which keep user code inside the filesystem itself.

### Using a `BoardSpec`

**From the CLI**, save your board as a module-level attribute anywhere importable, then point
`--board-spec` at it - a file path (no package needed) or a dotted module path, `target:attr`:

```sh
rp2040py micropython --board-spec my_board.py:BOARD --littlefs littlefs.img
rp2040py kaluma --board-spec my_board.py:BOARD
rp2040py mklittlefs --board-spec my_board.py:BOARD -o littlefs.img app.py
rp2040py run --board-spec my_board.py:BOARD --image firmware.uf2
```

`--board-spec` is mutually exclusive with `--board` (and, where relevant, `--image`/
`--fetch-fw-only`/`--target` - see [record 0049](../records/0049-external-device-authoring-docs.md)'s
flag-compatibility table for exactly which flag conflicts with it on which subcommand).
`RP2040PY_BOARD_SPEC` (an env var, same `target:attr` syntax) works the same way as the flag, for
a persistent local setup that doesn't want it typed every invocation - `tests/pico_spec.py` is a
real, CI-verified worked example of a board-spec file, built by calling `resolve_board_spec()` at
import time against an env-var-selected MicroPython tag.

**From the SDK**, pass the `BoardSpec` straight to a `Device` class - `board` is always
keyword-only, and always the *only* board-related argument (no separate `image` kwarg, no
board-name string):

```python
from rp2040py.device import MicroPythonDevice

async with MicroPythonDevice(board=my_board) as device:
    stdout, stderr = await device.aexec("print(1 + 1)")
```

## Caveats worth knowing

- **`LEDMock` on `pico_w` is a placeholder, not hardware-accurate.** On a real Pico W the onboard
  LED is wired to the CYW43439 chip itself, not any RP2040 GPIO - `pico_w`'s `LEDMock(gpio=25)`
  entry exists purely to exercise the `ExternalDevice`/`attach_external_devices()` plumbing
  identically regardless of board, not to model real wiring.
- **`ExternalDevice`'s surface is attach-only.** There's no `detach()`, no reset hook, no shutdown
  participation - fine for in-tree use (every implementation is reviewed here), but worth knowing
  if you're relying on a custom device to clean up after itself. See
  [record 0049](../records/0049-external-device-authoring-docs.md) for the open question this
  leaves.
