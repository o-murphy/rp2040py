# 0064. A read-only state server (WebSocket / Socket.IO), and the web visualizer it enables

- Status: **Deferred — documented, nothing implemented, not planned for the near term
  (2026-08-17).** Written down so the shape is decided before anyone starts, not as a commitment
  to start.
- Conceived: 2026-08-17
- Related: [0060](0060-external-io-bridges.md) (external I/O bridges - this is the *read-only,
  in-browser* half of that record's web-bridge idea, split out because the constraints differ),
  [0030](0030-external-device-concurrency.md) (anything inbound must go through
  `schedule_threadsafe()`), [0046](0046-epd2in9g-external-device.md) / [0056](0056-st7735s-waveshare-lcd-board.md) /
  [0062](0062-yd-rp2040-board-and-ws2812.md) (devices that already emit exactly the frames and
  pixels a viewer would draw), [0049](0049-external-device-authoring-docs.md) (whose "`ExternalDevice`
  is attach-only" open question this turns from a nicety into a blocker)

## The idea

An optional server - a plain WebSocket, or **Socket.IO** behind an optional dependency extra -
that exposes **read access to the emulator's current state**: GPIO levels, peripheral state, which
`ExternalDevice`s are attached and what each of them currently shows. A browser page subscribes and
draws it, so you can watch what is connected and what it is doing, rather than inferring it from a
serial log.

Reachable from the **CLI** (a flag on `micropython`/`kaluma`/`run`), from the **SDK** (start it
against a `Simulator` you already own), or both. Both is the likely answer, for the same reason
`--tcp-port` and `SocketInteractiveRepl` both exist.

## Why this is not just "0060, again"

[0060](0060-external-io-bridges.md) covers two bridges with one foot outside the emulator, and its
hard finding is a *wall-clock* ceiling: the emulator runs ~20-30x slower than real time and in
bursts, so anything that has to meet real-world timing (a WS2812 strip on real pins, a servo)
cannot be driven from it. **None of that applies to a viewer.** A browser showing "GPIO25 is high,
the panel shows this picture, the RGB LED is this colour" is not participating in a protocol - it
is watching. Late is fine; approximate is fine; dropped intermediate states are fine.

What 0060 already decided and this inherits unchanged:

- **Coalesced snapshots at wall-clock rate, never one message per event.** PWM and PIO produce
  kHz-to-MHz edge rates; a socket write per edge would dominate the emulator's own runtime. The
  server samples/merges and emits at a human rate (~30 Hz ceiling, probably less).
- **Anything inbound goes through `schedule_threadsafe()`** ([0030]) - the engine room owns its own
  state, and a socket callback runs on someone else's thread.
- **Optional dependency, imported inside the start call**, the way the `[fs]` extra already works.
  A viewer must not become a hard dependency of an emulator that is usually run headless.

## Read-only first, and probably read-only for a long time

Pressing an emulated button from the browser is the obvious next ask, and deliberately out of scope
here: it turns a viewer into a *controller*, which needs an authority story (who may press what,
what happens when two tabs disagree), a consistency story (an input applied mid-batch), and a
security story (a socket that can drive the device under test). Read-only has none of those - it
needs only a serialisation.

## The real obstacle: nothing can describe itself

The emulator can already report GPIO levels and peripheral registers. What it cannot do is answer
"what is attached, and what does it look like": `ExternalDevice` is an attach-only `Protocol` with
a single method ([0049]'s standing open question), so an `St7735s` and a `KeyMock` are
indistinguishable to anything outside them. A viewer needs, per device, at least:

- a stable **kind** (`led`, `button`, `ws2812`, `st7735s`, `epd2in9g`, ...) and an instance id;
- which **pins** it occupies, so the wiring can be drawn;
- its current **presentation state** - on/off, colour, framebuffer - which today is delivered by
  device-specific callbacks (`on_frame`, `on_pixels`) or read off ad-hoc attributes (`LEDMock.on`).

That is a genuine extension to the device protocol, and it is the part to design first: a small
optional interface (a `describe()` returning a plain dict, say) that a device may implement and a
viewer may fall back on when it does not. Getting it wrong means every device grows viewer-specific
code, which is exactly what `on_frame`'s "raw bytes, never a picture" boundary
([0046](0046-epd2in9g-external-device.md)) was drawn to avoid.

## Visualization: a viewer is feasible, an editor is not (yet)

Worth separating, because they are wildly different amounts of work:

- **A viewer** - render the board, its devices and their live state, wired as the `BoardSpec` says.
  Very feasible. [`@wokwi/elements`](https://github.com/wokwi/wokwi-elements) is MIT-licensed and
  already ships web components for exactly this cast: LEDs, push buttons, seven-segment displays,
  NeoPixels, LCD/OLED panels. Either build on those directly or copy the shape. The devices this
  project already has line up almost one-to-one - `LEDMock` -> `wokwi-led`, `KeyMock` ->
  `wokwi-pushbutton`, `Ws2812` -> `wokwi-neopixel`, `St7735s`/`Epd2in9G` -> a canvas fed by
  `on_frame`'s existing raw buffers.
- **An editor/constructor** - drag components onto a canvas, wire them to pins, generate a
  `BoardSpec`. Much harder, and it needs the description problem above solved *plus* a story for
  round-tripping into Python. Explicitly out of scope; note that a `BoardSpec` is already an
  ordinary, inspectable frozen dataclass ([0059](0059-boardspec-firmware-resolution.md)), so
  generating one is not the hard part - deciding what a UI may express is.

## Open questions

- **Transport: raw WebSocket or Socket.IO?** Socket.IO buys reconnection, rooms and namespaces at
  the cost of a dependency on both ends and a non-standard framing; a raw WebSocket keeps the
  browser side dependency-free. The record's instinct is a raw WebSocket, with Socket.IO left as
  the thing to reach for only if the viewer grows multi-client features that justify it.
- **Schema and versioning.** A viewer and an emulator will be updated independently; the message
  format needs a version field from day one, and a documented "unknown device kind" fallback.
- **Where the sampling loop lives.** An `asyncio` task on the engine-room loop is the natural fit
  ([0026](0026-main-thread-asyncio.md)), but it must never be what keeps `execute()` from finishing.
- **Bind address and default.** Localhost-only, off unless asked for, no exceptions - the same
  posture `--tcp-port` takes.
- **Does the viewer ship in this repo?** A Python package that serves a bundled JS app is a very
  different maintenance commitment from a documented protocol plus a separate demo. Leaning toward
  protocol-first, with a minimal single-file page in `demo/` as proof.
