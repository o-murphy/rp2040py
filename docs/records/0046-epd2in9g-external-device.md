# 0046. `epd2in9g` virtual e-paper: ported forward, promoted to a real `ExternalDevice`

- Status: Implemented — verified (2026-08-14)
- Conceived: 2026-08-14 · Implemented: 2026-08-14
- Related: 0029 (CYW43 board composition — the `ExternalDevice`/`attach_external_devices()` design
  this follows, and the one that explicitly flagged `demo/components/virtual_eink.py` as a future
  candidate for this exact treatment), 0030 (`ExternalDevice` concurrency contract — `attach()`
  synchronous/in-line/pre-`start_execution()` only), 0044 (SPI DMA hang fix — a different bug in
  the same wire-protocol area; see "Record 0044 overlap" below), 0028 (CYW43 module layout — why
  `cyw43/` is a subpackage and this isn't)

## Context

`component/epd2in9g` (a local+`origin` branch, commit `a6d6f34`, 2026-08-11) added a virtual
Waveshare 2.9" e-Paper (G) demo — `demo/components/virtual_eink.py` (the wire-protocol emulation),
`demo/components/mp_eink_demo.py` (the on-device MicroPython driver + wipe animation, pushed over
raw REPL), `demo/eink_run.py` (the CLI runner: Tkinter live view or numbered PNG screenshots) —
built as a validation vehicle for real display-driver firmware, deliberately kept out of
`src/rp2040py/` at the time. That branch was cut from `main` well before this work and never
merged; `main` kept moving (the async-native `MicroPythonDevice` API from 0025/0026,
`utils/firmware_retrieve.py`'s module move, 0044's own SPI/DMA fix) while the branch stayed frozen,
so by 2026-08-14 the two had diverged by ~17k lines. Ported forward at the user's request
("слідуючи docs портувати з гілки component/epd2in9g девайс").

## API drift fixed while porting

- `MicroPythonDevice.start()` (blocking) no longer exists — the async-native migration means only
  `start_async()`/`astart()` do. `eink_run.py` is a plain synchronous script (Tkinter's own event
  loop, pumped manually — not asyncio-native), so it uses `device.start_async().result()`: with
  `astart()`/`bind_loop()` never called, `Simulator._ensure_loop()` lazily spins up its own
  background thread, exactly reproducing the old dedicated-worker-thread behavior `.result()`
  replaces — which is what lets `Epd2in9G.on_frame` fire off the same thread driving Tkinter.
- `rp2040py.cli.firmware_retrieve` → `rp2040py.utils.firmware_retrieve` (module moved; `retrieve()`'s
  own signature is unchanged).
- Everything else in the original wire-protocol logic — `GPIOPinState`, `RPSPI.on_transmit`/
  `complete_transmit()`/`clock_frequency`/`data_bits`, `mcu.clock.create_alarm()` — needed no
  changes; that part of the API surface hadn't moved since the branch was cut.

## Design change beyond a straight port: promoted to a real `ExternalDevice`

User-directed, not part of the original port: the demo's class used to take `mcu` directly in
`__init__` and wire itself immediately — ad hoc, pre-dating the `ExternalDevice` Protocol (0029's
own record already flagged this exact file as a future candidate: *"a future `ExternalDevice`-
shaped rewrite of that demo would use the same interface as `Cyw43439`"*). Reworked to
structurally implement `ExternalDevice` (`external/device.py`): `attach(rp2040)` does the wiring
(SPI `on_transmit`, CS/DC/RST/BUSY GPIO listeners, the two `ClockAlarm`s) instead of `__init__`,
following 0030's contract — synchronous, in-line, safe only before `Simulator.start_execution()`.

Renamed `VirtualEpd2in9G` → `Epd2in9G` (drops the "Virtual" prefix to match `Cyw43439`'s own
naming — both are protocol-accurate chip/panel emulations, not simplified test doubles like
`LEDMock`/`KeyMock`, which keep their `_mock` framing).

## Location: `src/rp2040py/external/epd2in9g.py`, not `demo/`

Reverses an earlier, narrower decision (a prior session's memory: kept out of `src/rp2040py/`
specifically because "an E-Ink panel is an arbitrary external SPI peripheral the user wires up
themselves — not part of any real RP2040 board") — the user explicitly asked for this device to
live next to `external/cyw43/` this time ("і цей екстернал девайс має бути поряд з cyw43 девайсом
в проджект леяуті"). Landed as a flat sibling module, not a subpackage: `cyw43/` only needed one
(0028) because it's genuinely three files (`bus.py`/`chip.py`/`nat.py`); this is one class, so it
matches this project's own module-layout default (CLAUDE.md: "Default to sibling top-level
modules unless there's a real ownership/dependency reason for nesting") — briefly built as
`external/epd2in9g/__init__.py` + `panel.py` mirroring `cyw43/`'s shape, then collapsed back to a
flat file once that was pointed out.

Still **not** added to `boards.py`'s `BOARDS` registry — still arbitrary user-wired hardware, not
fixed on any real board, per 0029's "any board customization beyond the built-in presets is
API-only" principle. A caller wires it in directly: `attach_external_devices(mcu, Epd2in9G(...))`,
the same public mechanism 0029 describes for any custom device outside the registry.

## No Pillow/PIL dependency in `src/`

User-directed correction mid-port ("pil is not a part of virtual device and should not be"). The
original demo's `_decode_frame()` built a Pillow `Image` internally and handed it to `on_frame`;
moving the class into `src/` as written would have put `from PIL import Image` under `mypy`'s
`files = ["src"]` check and made Pillow a de facto runtime dependency of the core package —
unlike `littlefs-python`, which has a real `fs` optional-dependency extra in `pyproject.toml`,
Pillow was never given one, deliberately, since it was always meant to be demo-visualization-only.

Fixed by moving the protocol/picture boundary rather than adding a dependency: `Epd2in9G.on_frame`
now fires with the raw packed 2bpp frame buffer (`bytes`) instead of a decoded `Image`. `PALETTE`
(the vendor driver's 2-bit-index → RGB quantization table) stays in `epd2in9g.py` — it's a
wire-protocol constant, not a rendering concern — but the per-pixel decode loop that used to be
`_decode_frame()` moved to `demo/eink_run.py`, the only remaining Pillow consumer.

## Record 0044 overlap

The demo's `_on_transmit()` paces `complete_transmit()` via a real per-byte `ClockAlarm` instead of
completing synchronously, to avoid a TX/RX DMA FIFO-overrun hang discovered while first building
this demo (2026-08-11) — a different bug from the one 0044 later fixed at the simulator level
(2026-08-14: a stale DREQ cache after `RPDMA.reset()`, plus a same-tick `SimulationClock`
alarm-ordering bug). 0044's fix means an *unpaced* `on_transmit` would likely no longer hang here
either, but the demo's own pacing was left in place — it's also a more realistic per-byte SPI
clock model, not purely a correctness workaround, and removing it wasn't asked for.

## `external/cyw43/__init__.py` re-export (parallel, user-driven)

While `external/epd2in9g.py`'s own public shape was being decided, the user applied the same
"re-export the concrete class from `__init__.py`" idea to `external/cyw43/` too:
`external/cyw43/__init__.py` now does `from rp2040py.external.cyw43.chip import Cyw43439` /
`__all__ = ("Cyw43439",)`, and `boards.py` imports `Cyw43439` from `rp2040py.external.cyw43`
instead of `rp2040py.external.cyw43.chip` directly. `cyw43/`'s own `bus.py`/`chip.py`/`nat.py`
split (0028) is unaffected — only the public import path changed.

## Scope: demo files stay in `demo/`

`demo/mp_eink_demo.py` (on-device MicroPython driver — flattened out of the now-gone
`demo/components/` package, which only existed to make the old `virtual_eink.py` importable) and
`demo/eink_run.py` (the host-side CLI runner) both stay under `demo/` as-is; only the panel
emulation itself moved to `src/`. Considered and explicitly declined: deleting the demo now that
the device is a real library class, or replacing it with a test instead.

## Verification

`uv run pre-commit run --all-files` clean — mypy now type-checks `Epd2in9G` for real (it's under
`src/` now), ruff lint/format clean, both pure-Python and native-Cython pytest runs pass (581
tests, 1 skipped). Ran `demo/eink_run.py --image v1.28.0 --screenshot ...` against the cached
MicroPython v1.28.0 image (`uv run --with pillow ...`, since Pillow is still not a project
dependency — install it yourself per the script's own docstring): booted, drove the panel over
SPI1, all 4 wipe-animation frames decoded and written correctly.

## Addendum (2026-08-17): where the demo's wall time actually goes, measured

Prompted by "таймінги еінк дисплею у демо дуже великі, симуляція й так повільна" - i.e. the
assumption that the demo's `busy_nanos_*`/`sleep_ms()` values are what make it slow. Measured
instead of argued, with an instrumented harness (boot, then per-`on_frame` wall + simulated-clock
deltas) against real MicroPython `v1.21.0`:

| config | per steady-state frame, wall | per frame, simulated |
|---|---|---|
| as shipped before this addendum (SPI 4 MHz, `busy_power=2ms`, `busy_refresh=4ms`) | 1.45 s | 76 ms |
| `busy_power=0.5ms`, `busy_refresh=2ms` (4 MHz) | 1.48 s | 75 ms |
| SPI 10 MHz (busy values unchanged) | 1.19 s | 66 ms |
| SPI 20 MHz (busy values unchanged) | 1.07 s | 62 ms |

So the overridable BUSY delays are **~3% of a frame**, and halving them is inside the noise: of the
~76 ms of simulated time a frame costs, roughly 19 ms is the 9,472-byte framebuffer write paced at
the real 4 MHz byte time (`_on_transmit()`'s alarm), 4 ms is `busy_nanos_refresh`, and the rest is
the guest generating and pushing the frame in MicroPython. The wall/simulated ratio measured ~19x,
close to the ~30x this record's demo comments already estimated.

Two further results worth recording, both contradicting an initially plausible reading of the
data:

- **Whole-run wall times are a bad metric here.** Frame 0 (script upload + `init()`) and the tail
  after the last frame (`sleep()` + output retrieval over the emulated CDC) swing between ~1 s and
  ~15 s run to run for *identical* configurations - host/device interaction, not panel timings. An
  earlier pass that compared only total runtimes concluded a faster SPI clock made the demo
  *slower*; the per-frame instrumentation shows the opposite, and the steady-state frames are
  deterministic to ~0.05 s.
- **Real datasheet timings really are out of reach**, which is what justifies this demo's tuned
  values existing at all: a run with `busy_refresh=15s`/`RESET_MS=200`/`POWER_ON_SETTLE_MS=500` was
  killed after 15+ minutes without finishing 6 frames.

What changed as a result (`demo/mp_eink_demo.py` only, no `src/` change): guest-side settle delays
trimmed where they gate nothing (`RESET_MS` 5→2, `POWER_ON_SETTLE_MS` 10→3, `POWER_OFF_SETTLE_MS`
5→2), and the demo's SPI clock raised 4 MHz → 10 MHz, which is where the measured win actually is.
`BUSY_POLL_MS` (2 ms) and `eink_run.py`'s `busy_nanos_refresh` (4 ms) were deliberately **left
alone**: 4 ms is exactly two poll intervals, so firmware still observes BUSY low and spins its wait
loop at least once. Cutting either further would buy ~1 ms of simulated time per frame and turn the
BUSY handshake this demo exists to exercise into a no-op. Verified after the change: all six
rendered frames are byte-identical to the pre-change PNGs, and the whole demo now runs in ~12 s.
