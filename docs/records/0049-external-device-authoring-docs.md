# 0049. Document external devices, and how a user writes their own

- Status: **Proposed — not implemented.** This record is the note about what should eventually
  happen; nothing in `README.md`, `docs/reference/` or `src/` is changed by it.
- Conceived: 2026-08-16
- Related: 0030 (`ExternalDevice` concurrency model - the attach-timing rule any such doc has to
  state), 0028/0029 (module layout / board composition - why `external/` is a sibling top-level
  module), 0046 (`Epd2in9G`, the richest existing device and the closest thing to a worked
  example), 0032 (docs restructure - decides `README.md` vs `docs/records/` vs `docs/reference/`)

## The gap

`rp2040py` has a real external-device story in the tree and **no user-facing documentation of it
at all**:

- `external/device.py` defines the whole public contract: an `ExternalDevice` `Protocol` with a
  single `attach(rp2040) -> None` method, plus `attach_external_devices(rp2040, *devices)`.
- Four implementations already exist - `LEDMock` (`led_mock.py`), `key_mock.py`, `Epd2in9G`
  (`epd2in9g.py`, 0046) and `Cyw43439` (`cyw43/`, 0027/0028/0029).
- `demo/eink_run.py` + `demo/mp_eink_demo.py` are, between them, already a complete worked example:
  a host-side device attached via `attach_external_devices()`, a guest-side MicroPython driver
  written against it, and a viewer that renders what the device produced.

None of that is mentioned in `README.md`. The word "external" doesn't appear in it in this sense;
`--board {pico,pico_w}` is described as attaching "just the onboard LED, except for `pico_w`", and
that's the closest the README gets. Meanwhile
[reference/os-compatibility.md](../reference/os-compatibility.md) *does* carry per-OS rows for
"MockLED — onboard LED" and "Waveshare 2.9″ e-Paper (`Epd2in9G` external device)" - so the
compatibility matrix promises platform support for features the README never introduces. A reader
who finds those rows has nowhere to go to learn what an external device is.

The sharper half of the gap is the one the CLI cannot close. `--board` picks an entry from
`boards.py`'s registry (`BoardSpec(extras=...)`), and that registry is a fixed, in-tree list -
there is no flag, no plugin path, no entry-point hook by which a user attaches *their own* device.
The moment someone wants to emulate a peripheral this project doesn't ship, the CLI stops being
enough and they must drop to the library API. That transition is exactly what needs documenting,
and it currently isn't documented anywhere.

## What should eventually exist

Two distinct pieces, not one:

1. **A section covering the external devices that ship today** - what `ExternalDevice` is, that
   `--board pico_w` attaches `Cyw43439` and an `LEDMock`, that `Epd2in9G` exists and is driven
   from `demo/eink_run.py`. Mostly descriptive; documents current behavior, invents nothing.
2. **A worked "write your own device" example** - the "when the CLI isn't enough" path: implement
   `attach(rp2040)`, subscribe to whatever GPIO/SPI/peripheral surface the device needs, construct
   the `RP2040` yourself (or via `MicroPythonDevice(board=...)`), call
   `attach_external_devices()`, then run. `LEDMock` is the minimal end of that spectrum (one GPIO
   pin, a state flag and a toggle counter - it exists precisely as a validation vehicle for this
   machinery), `Epd2in9G` the substantial end.

Whatever form this takes must state the **attach-timing rule** rather than leaving a reader to
find it in a docstring: devices may only be attached *before* `Simulator.start_execution()`. What's
wired to a board is fixed at power-on by design, not hot-pluggable - `GPIOPin`'s listener set is
iterated unsynchronized on the engine-room thread once execution starts (0030). A user-facing
example that omits this teaches a race.

## Open questions - decide before writing, not while writing

- **Where does it live?** A README section is the discoverable answer for piece 1, but piece 2 is
  a how-to with real code, which is what `docs/reference/` exists for (0032). Plausibly: a short
  README section that links to a new `reference/external-devices.md`. Not decided here.
- **Is `ExternalDevice` ready to be advertised as public API?** Its entire surface is `attach()` -
  no `detach()`, no reset hook, no shutdown participation (0021's coordinator knows nothing about
  external devices). That is fine for in-tree use, where every implementation is reviewed here;
  documenting it as a user extension point is a stronger commitment. Either the surface is declared
  sufficient on purpose, or the missing lifecycle gets designed first - this record does not decide
  which, but the docs shouldn't ship before that call is made.
- **Should the CLI grow a way to attach a user device at all?** e.g. `--device pkg.mod:Factory`,
  resolved by import path, so the "CLI isn't enough" cliff becomes a gentler slope. Genuinely
  optional - "drop to the library API" is a defensible answer - but if the answer is yes, the docs
  should be written against that flag rather than rewritten right after it lands.
- **Do `demo/eink_run.py`/`demo/mp_eink_demo.py` get promoted?** They already do the job of the
  example. Options: link them as-is, extract a smaller purpose-built example, or leave demos as
  demos and write a standalone snippet. Note `eink_run.py` carries PEP-723 inline metadata and a
  Pillow dependency that deliberately does not exist in `src/` (0046) - an example inheriting that
  dependency needs the same caveat.
- **How much of `LEDMock`'s caveat is user-facing?** Its docstring is explicit that on a real Pico
  W the onboard LED hangs off the CYW43439, not any RP2040 GPIO, so the `pico_w` LED attachment is
  a placeholder rather than hardware emulation. Documenting `--board pico_w` without that is
  mildly misleading; documenting it in the README needs a compact way to say it.

## Cleanup this work would naturally pick up

`external/device.py` and `external/led_mock.py` both cite `docs/CYW43_WIFI_BACKLOG.md` in their
docstrings (twice and three times respectively, e.g. for the "Module layout decision" / "Board
composition decision" / "Implementation order" sections). That file no longer exists - the 0032
restructure replaced it with `docs/records/` (0028 and 0029 carry those two decisions). Anyone
writing the user-facing docs will read exactly these docstrings first and hit the dead references,
so re-pointing them belongs to this task. Not a reason to do the task on its own.

## Explicitly not decided here

No API is added, no README/reference file is written, and the shape of the example is left open.
This record exists so the gap is tracked rather than rediscovered; per this repo's
document-vs-implement convention, turning it into code or prose needs a separate go-ahead.
