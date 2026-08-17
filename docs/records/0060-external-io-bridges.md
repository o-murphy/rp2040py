# 0060. External I/O bridges: an `ExternalDevice` with one foot outside the emulator

- Status: **Deferred — documented, not scheduled (2026-08-17).** Not rejected: both applications
  below are wanted, neither is started, and neither blocks anything currently in flight. Written
  now so the shared parts are decided once instead of twice.
- Conceived: 2026-08-17
- Related: 0030 (`ExternalDevice` concurrency contract - the rule every bridge here lives or dies
  by), 0029 (board composition), 0046/0056 (`Epd2in9G`/`St7735s` - the existing devices whose
  `on_frame` is the natural thing to bridge first), 0049 (`ExternalDevice`'s attach-only surface,
  still an open question), 0021 (shutdown coordination - which a bridge holding a socket will need)

## Why one record for two ideas

Two separate asks arrived together: **(4)** expose GPIO/devices over a web socket so the existing
[`@wokwi/elements`](https://github.com/wokwi/wokwi-elements) web components can render them, and
**(5)** an `RPZeroGPIO(ExternalDevice)` that maps emulated pins onto a real host's GPIO
(`gpiozero`/`lgpio`) so physical hardware can be driven by emulated firmware.

They are the same class of thing - a device whose `attach()` wires the emulator to something
*outside the process* - and they hit the same three walls. Deciding those once is most of the
work; the two applications then differ only in transport.

## The shared contract

**1. Outbound changes must be coalesced, not streamed per edge.** A GPIO listener fires on every
transition. Firmware routinely produces those at kHz rates (PWM - CircuitPython drives this
board's backlight at 50 kHz; PIO; bit-banged buses), and 0046's timing addendum measured how
sensitive wall-clock already is: ~19x real time per simulated millisecond, with per-frame costs
dominated by exactly this kind of work. A bridge that emits one message per edge will both drown
its transport and slow the simulation it is reporting on. The shape that works is a **snapshot at a
fixed rate** (30-60 Hz of *wall* time) carrying whatever changed since the last one, with frame
buffers handled the same way - `St7735s` already showed a firmware producing 58 frames in seconds.

**2. Inbound events must go through `schedule_threadsafe()`.** A button click in a browser, or a
real GPIO edge callback from `lgpio`, arrives on someone else's thread. 0030 is explicit: an
external device may not touch engine-room state from another thread. `BootselButton` gets away with
a direct `set_input_value()` only because it is pressed before `start_execution()`; every bridge
here is interactive *while running*, so this is not optional for them.

**3. Optional dependency, imported inside `attach()`.** Neither a web server nor `gpiozero` belongs
in the core install. The precedent is `littlefs-python` behind the `[fs]` extra: declare
`[web]`/`[gpio]` extras, import inside the device rather than at module scope, and fail with a
message naming the extra.

A fourth, softer rule: **say what the bridge cannot do, in its own docstring.** Both applications
below are honest and useful within limits and misleading outside them.

## Application 4: a web viewer built on `@wokwi/elements`

The elements are ordinary web components (LED, pushbutton, 7-segment, SSD1306, LCD1602, servo, …)
driven by attributes and emitting DOM events - and `@wokwi/elements` is **MIT** (verified against
`package.json`, v1.9.2 at the time of writing), so vendoring a copy for offline use is allowed.
That means the interesting work is not the UI at all; it is the protocol and the hosting:

- a small message set: periodic pin snapshots, display frames as blobs, and input events back;
- something to serve a static page plus a WebSocket. This project is deliberately dependency-shy,
  so "stdlib `asyncio` + a hand-rolled WS frame codec" versus "`websockets` behind the `[web]`
  extra" is a real decision, not a detail;
- a CLI surface (`--web [port]`) alongside the host-facing channels that already exist -
  `--pty`, `--tcp-port` - which is why this is not conceptually foreign to the CLI.

**Start with displays, not raw pins.** `Epd2in9G`/`St7735s` already emit a well-defined "frame"
event at a sane rate; wiring those to a browser is a contained first slice that proves the
transport, whereas raw GPIO immediately runs into wall 1.

## Application 5: `RPZeroGPIO` - emulated pins onto real host GPIO

Mechanically the smallest of the two: a `gpio_map` of emulated pin → host pin, `attach()`
subscribes to each emulated `GPIOPin` and drives the host pin through `gpiozero`/`lgpio`;
host-side edge callbacks come back through `schedule_threadsafe()` to `set_input_value()`.
`gpiozero`'s `MockFactory` makes the whole thing unit-testable with no hardware attached, which is
a genuine argument for building it - the same role `LEDMock` played for the `ExternalDevice`
machinery itself.

**The ceiling has to be stated first, not last.** The emulator runs roughly 20-30x slower than
real time *and in bursts* (batched execution, `_execute_batch.py`). So:

- fine: LEDs, relays, buttons, switches, anything level-based where "eventually" is correct;
- not fine: WS2812, servo PWM, bit-banged SPI/I²C at speed, anything where the *interval* between
  edges carries meaning. A bridge cannot fix this; only pretending it can would be a bug.

**The more valuable sibling: bridge transactions, not pins.** For real chips, tapping the SPI/I²C
*transaction* boundary - the way `Epd2in9G` already hooks `spi.on_transmit` - and forwarding whole
transfers to the host's `spidev`/`i2c-dev` avoids the edge-rate problem entirely and is what makes
"talk to a real sensor from emulated firmware" plausible. If only one of the two gets built, this
is the one worth building.

## What would move either off the shelf

- **4**: a concrete demo that needs it - e.g. wanting `demo/lcd_run.py`'s output in a browser
  instead of Tk, or a hosted playground. The transport decision (stdlib vs `websockets`) is the
  first thing to settle.
- **5**: a real use case with real hardware attached, plus agreement that the timing ceiling above
  is acceptable for it. Building the `spidev` transaction bridge first would also de-risk it.

Both should reuse whatever the other establishes for snapshot rate limiting and inbound
scheduling; that is the reason this record exists rather than two.
