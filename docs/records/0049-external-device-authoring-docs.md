# 0049. Document external devices, and how a user writes their own

- Status: **Proposed — docs-only pieces landed (README pointer section, the `fs_blocksize`
  prerequisite), the `BoardSetup` board-authoring design is accepted but its 5-phase implementation
  plan hasn't started.** Scope was widened 2026-08-16 from "document devices" to "document devices
  *and boards*" - see "Accepted design" below.
- Conceived: 2026-08-16
- Related: 0030 (`ExternalDevice` concurrency model - the attach-timing rule any such doc has to
  state), 0028/0029 (module layout / board composition - why `external/` is a sibling top-level
  module), 0046 (`Epd2in9G`, the richest existing device and the closest thing to a worked
  example), 0032 (docs restructure - decides `README.md` vs `docs/records/` vs `docs/reference/`),
  0035 (board-aware flash offset - the record that first made `flash_layout()` board-keyed; the
  design update below runs into the same board/firmware coupling from the opposite direction),
  0036 (`--littlefs`/`--fat12` mutual-exclusivity pattern - the precedent for a new CLI flag
  validated by hand rather than by `argparse` machinery)

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

- ~~**Where does it live?**~~ — **resolved 2026-08-16:** `README.md` gets a short section (under
  "Library API") that names the pieces that exist and links to *this record* - not to a
  `reference/external-devices.md` that doesn't exist yet. Writing the full how-to reference doc is
  still future work; the README section only needs to stop being silent about external
  devices/boards existing at all, today.
- **Is `ExternalDevice` ready to be advertised as public API?** Its entire surface is `attach()` -
  no `detach()`, no reset hook, no shutdown participation (0021's coordinator knows nothing about
  external devices). That is fine for in-tree use, where every implementation is reviewed here;
  documenting it as a user extension point is a stronger commitment. Either the surface is declared
  sufficient on purpose, or the missing lifecycle gets designed first - this record does not decide
  which, but the docs shouldn't ship before that call is made. **Still open** - the 2026-08-16
  design update below is about *boards*, not this.
- ~~**Should the CLI grow a way to attach a user device at all?**~~ — **resolved 2026-08-16, for
  now: no.** Considered in depth (not just left undecided) as part of the board-authoring design
  update below, for both a single device (`--device pkg.mod:Factory`) and a whole board
  (`--board-spec target:attr`) - deliberately deferred in both forms. "Drop to the library API,
  the CLI stays a closed registry" is the answer for this pass; a CLI hook is a distinct, separate
  future feature (0029 already called this out for devices), not a byproduct of writing docs for
  what already exists.
- **Do `demo/eink_run.py`/`demo/mp_eink_demo.py` get promoted?** They already do the job of the
  example. Options: link them as-is, extract a smaller purpose-built example, or leave demos as
  demos and write a standalone snippet. Note `eink_run.py` carries PEP-723 inline metadata and a
  Pillow dependency that deliberately does not exist in `src/` (0046) - an example inheriting that
  dependency needs the same caveat. **Still open.**
- **How much of `LEDMock`'s caveat is user-facing?** Its docstring is explicit that on a real Pico
  W the onboard LED hangs off the CYW43439, not any RP2040 GPIO, so the `pico_w` LED attachment is
  a placeholder rather than hardware emulation. Documenting `--board pico_w` without that is
  mildly misleading; documenting it in the README needs a compact way to say it. **Still open.**

## Design update (2026-08-16): board authoring, not just device authoring

The original scope above ("a user writes their own *device*") turned out to be half the ask - a
user attaching their own device to an *existing* board (`pico`/`pico_w`) was already fully solved
by `demo/eink_run.py`. What wasn't examined is a user assembling their own **board** (own set of
devices, possibly own firmware). Working through it surfaced three distinct scenarios, each with a
different amount of code already behind it - conflating them would make the eventual docs promise
things that don't work yet.

### Scenario 1 - custom device, existing board and firmware

Already fully solved, zero code needed: `demo/eink_run.py` already does exactly this via
`attach_external_devices()` (`external/device.py`). This is piece 2 of the original scope above,
unchanged.

### Scenario 2 - custom board (own `extras`), existing firmware/layout

`boards.py`'s `BoardSpec` is a public, frozen dataclass (`mcu: type[RP2040]`,
`extras: tuple[ExternalDeviceFactory, ...]`) - a user can construct one directly, entirely outside
`boards.BOARDS`, with no new library code:

```python
from rp2040py.boards import BoardSpec
from my_devices import MyDevice

board = BoardSpec(extras=(MyDevice, lambda: MyOtherDevice(pin=5)))
```

But **this does not yet plug into `MicroPythonDevice`/`KalumaDevice`/`run`** - all three currently
take a board-*name* string, not a pre-built `BoardSpec`/`RP2040`:

- `BaseDevice.__init__` (`device/base_device.py:58`) is the one shared chokepoint for
  `micropython`+`kaluma`: `self.simulator = Simulator(rp2040=build_rp2040(board))`. `run` bypasses
  `BaseDevice` entirely and calls `build_rp2040(args.board)` a second, independent time
  (`cli/__init__.py:208`, inside `_run_async`) - two chokepoints for MCU construction, not one.
- For `run` alone, that's the *whole* story - `_run_async` never touches `flash_layout()` (raw
  `.hex`/`.uf2`, no filesystem), so a `BoardSpec`-accepting `run` path is a small, self-contained
  change.
- For `micropython`/`kaluma`, board-name string is *also* used independently for filesystem
  placement - see scenario 3, this doesn't stop at `BaseDevice`.

Accepting a `BoardSpec` object as an alternative to a board-name string in `BaseDevice.__init__`
(and separately, in `_run_async`) is real, small, not-yet-written code - not something the docs can
describe as already possible today outside the `run` path. Superseded by the accepted `BoardSetup`
design below - scenario 2 doesn't get its own separate mechanism; it becomes the trivial case of
`resolve_board_setup()` followed by overriding `extras`.

### Scenario 3 - fully custom board: own firmware + own flash layout

The harder case, and the one that took the most digging: `flash_layout()`
(`utils/firmware_retrieve.py:181-192`) resolves `fs_start`/`fs_blockcount` from
`firmware_specs.json`, keyed by the *exact same board-name string* as `BOARDS` - but it's a
completely independent registry, with **no fallback and no derivation from a `BoardSpec`/`RP2040`
object**. An unknown board name is a hard `KeyError`.

Confirmed against real MicroPython source (`/home/murphy/pyproj/micropython`,
`ports/rp2/boards/*/mpconfigboard.h`) that this isn't a `pico`/`pico_w`-only quirk: **every board
upstream hardcodes its own `MICROPY_HW_FLASH_STORAGE_BYTES`**, e.g. `RPI_PICO` `1408*1024`,
`RPI_PICO_W` `848*1024` (explicit `// todo: We need something to check our binary size` comment -
this is a manually-maintained safe margin, not measured from the actual binary),
`WEACTSTUDIO` (the upstream board id behind "YD-RP2040"-branded clones) `PICO_FLASH_SIZE_BYTES -
1MiB`, others ranging `384KiB`..`15MiB`. `fs_start = PICO_FLASH_SIZE_BYTES - that constant`, and
`PICO_FLASH_SIZE_BYTES` itself is **also** a board-specific (often flash-chip-variant-specific,
see `WEACTSTUDIO`'s four separate `weactstudio_{2,4,8,16}MiB.h`) compile-time constant - RP2040 has
no runtime flash-size probe (`hardware_flash/flash.c`'s OTP-based `flash_devinfo_get_cs_size()` is
RP2350-only). One practical consequence: the "single UF2 for YD-RP2040" people actually flash is
just the plain `RPI_PICO` build running under its `pico` `fs_start`/`fs_blockcount` and silently
never touching the clone's extra flash capacity - not evidence of auto-detection.

So a genuinely custom board's layout can't be guessed, reused from `pico`/`pico_w`, or derived from
anything already in this codebase - it has to be supplied explicitly, by whoever already knows
their own board's real numbers (having derived them the same way this section just did for
`pico`/`pico_w`/`WEACTSTUDIO`). Shape, matching exactly what `device/load_flash.py`'s four loader
functions each independently need:

```python
@dataclass(frozen=True)
class CustomFlashLayout:
    fs_start: int
    fs_blockcount: int
    fs_blocksize: int  # real `flash_layout()` key since 2026-08-16, see below - not a
                        # `load_flash.py` module constant, so a custom layout must supply it too
    prog_start: int | None = None  # Kaluma's separate YMODEM "user program" region only -
    # MicroPython/CircuitPython keep user code inside the FS itself
```

This is **not implemented** - `load_micropython_flash_image`/`load_circuitpython_flash_image`/
`load_kaluma_flash_image`/`load_kaluma_program` (`device/load_flash.py:106-133`) and their
`dump_*` counterparts (`:153-165`) each call `_flash_layout(SPEC, board)` themselves, independently
- there is no shared chokepoint the way scenario 2 has one for MCU construction. Threading a
pre-supplied `CustomFlashLayout` through instead of a board-name lookup touches all of them - which
is exactly the gap the accepted design below closes, by giving every consumer one already-resolved
object instead of a board-name string to re-look-up.

## Accepted design (2026-08-16): `BoardSetup`

**Decided, not just floated.** Both scenario 2's "custom `extras`, existing firmware" and
scenario 3's "fully custom board" collapse into one mechanism: `BoardSpec` (mcu + extras) is
renamed and extended into `BoardSetup`, which becomes the *only* thing `BaseDevice` and its
subclasses ever accept for "which board" - never a board-name string internally again. A board
name string is now purely a *CLI/SDK-convenience lookup key* that resolves to a `BoardSetup`,
never something a `Device` class or `load_flash.py` needs to know about or re-resolve itself.

```python
# boards.py
@dataclass(frozen=True)
class BoardSetup:
    mcu: type[RP2040] = RP2040
    extras: tuple[ExternalDeviceFactory, ...] = ()
    layout: CustomFlashLayout | None = None  # None where no filesystem concept applies (raw `run`)
    image: str | Path | None = None  # an already-resolved local file path ONLY - never a version
    # tag, never a URL. `retrieve()`'s tag/URL/cache resolution stays entirely a CLI/SDK-side
    # concern that happens *before* a `BoardSetup` is built, not something a `Device` class or a
    # custom board author's file ever has to replicate.

BOARDS: dict[str, BoardSetup] = {  # was BoardSpec; `layout`/`image` stay None here - filled in
    "pico": BoardSetup(extras=(lambda: LEDMock(gpio=25), BootselButton)),        # by resolve_board_setup()
    "pico_w": BoardSetup(extras=(lambda: LEDMock(gpio=25), BootselButton, Cyw43439)),
}


def resolve_board_setup(board: str, firmware_spec: FirmwareSpec, tag: "str | None" = None) -> BoardSetup:
    """The one shared shortcut both `--board` (CLI) and an SDK caller use for a *known* board -
    BOARDS[board]'s mcu/extras plus that firmware family's resolved image/layout, combined into
    one ready-to-run BoardSetup. `tag=None` defers to the board's own `default_tag`, same as
    `retrieve()` already does."""
    image = retrieve(firmware_spec, tag, board)
    layout = flash_layout(firmware_spec, board) if firmware_spec.boards is not None else None
    return dataclasses.replace(BOARDS[board], layout=CustomFlashLayout(**layout) if layout else None, image=image)
```

**Devices take `BoardSetup`, never a board-name string or a separate `image` kwarg.**
`BaseDevice.__init__`/`MicroPythonDevice`/`KalumaDevice` (and `_run_async`'s raw `run` path, for
`mcu`/`extras` only - its `layout`/`image` stay unused, `run`'s own `--image` is a raw program, not
board-family-versioned firmware, so it plausibly stays a separate argument; **not settled**, the
one open sub-question in this design) accept `board: BoardSetup` as their single board-related
parameter. `device/load_flash.py`'s six load/dump functions take the resolved `CustomFlashLayout`
directly instead of `(rp2040, board: str)` - `flash_layout()`/`_flash_layout` calls disappear from
every one of them, since resolution already happened once, in `resolve_board_setup()` or in
whatever hand-built the custom `BoardSetup`. This is what actually closes the "no shared
chokepoint" gap noted above: there was never a single place `load_flash.py`'s functions could all
defer to for layout - now there is, and it's the same place MCU construction already had one.

**CLI: `--board-spec target:attr`, mutually exclusive with `--board`** (not layered on top of it -
an earlier draft of this design had them combine, letting `--board-spec` override just `extras`
while silently inheriting `--board`'s image/layout; reversed, because that's just scenario 2 again
and blurs which one a reader is looking at - `--board-spec` means "I bring everything", full stop).
`target` is a file path (`my_board.py`, loaded via `importlib.util.spec_from_file_location` - no
package required) or a dotted module path (`importlib.import_module`, for an installed package);
`attr` names a module-level `BoardSetup` instance. Validated by hand, not `argparse`'s
`choices=`/mutual-exclusion machinery - same pattern as `--littlefs`/`--fat12` (0036). An
`RP2040PY_BOARD_SPEC` env var (Flask's `FLASK_APP`/Django's `DJANGO_SETTINGS_MODULE` pattern)
resolved through the exact same `target:attr` code, for a persistent local setup that doesn't want
the flag typed every invocation - not a separate mechanism. Deliberately **not** `conftest.py`-style
silent auto-discovery of a conventionally-named file in `cwd` - fine for a test framework's
expected magic, wrong for this project's "explicit opt-in, no hidden import-on-every-run surface"
posture (the same reasoning 0029/this record's own resolved CLI-hook question already leaned on).

**Flag compatibility with `--board-spec`** - worked out flag by flag, not assumed:

| Flag | With `--board-spec` | Why |
|---|---|---|
| `--image` | incompatible | `BoardSetup.image` is already the concrete file - nothing left to resolve a tag/URL against |
| `--target` (`mklittlefs`) | incompatible | `_target_fs_layout()` resolves `flash_layout()` by board *name*, which doesn't exist here |
| `--fetch-fw-only` | incompatible | exists to pre-resolve+cache a *tag*; `BoardSetup.image` is never a tag or URL, nothing to fetch |
| `--circuitpython` | **compatible** | not just a `FirmwareSpec` selector - also picks the loader/dump function (`load_circuitpython_flash_image` vs `load_micropython_flash_image`, `mp_device.py:92,248-249`) *and* post-boot console behavior (send `\r\n` vs Ctrl-C, `cli/__init__.py:538-543`), both independent of how the board was resolved |
| `--bootrom` | compatible | silicon revision (B0/B2), orthogonal to board/firmware entirely |
| `--gdb`/`--gdb-port`/`--expect-text`/`--expect-regex`/`--tcp-port`/`--pty` | compatible | how you talk to an already-running device, unrelated to board resolution |
| `--littlefs`/`--dump-fs`/`--fat12` | compatible | consume `board_setup.layout` directly; a custom board that left `layout=None` just fails at use, not a CLI-level conflict |
| `--block-size`/`--block-count` (`mklittlefs`) | compatible | always manual overrides, regardless of resolution source |

Same validate-by-hand pattern as the `--littlefs`/`--fat12` precedent (0036) for the incompatible
row - not `argparse` mutual-exclusion groups.

### Phased implementation plan (not started)

1. **`boards.py`**: rename `BoardSpec` → `BoardSetup`, add `layout`/`image` fields (default `None`
   on both - existing `BOARDS` entries need no other change). Add `CustomFlashLayout` (move out of
   this doc into real code) and `resolve_board_setup()`.
2. **`device/load_flash.py`**: six load/dump functions stop taking `board: str` and calling
   `_flash_layout()` themselves - take a resolved `CustomFlashLayout` (or the equivalent plain
   dict) instead. Delete the now-dead `_flash_layout`/`MICROPYTHON`/`CIRCUITPYTHON`/`KALUMA`
   imports from this module.
3. **`device/base_device.py`/`mp_device.py`/`kaluma_device.py`**: `board: str` constructor param
   becomes `board: BoardSetup`; drop the separate `image` kwarg in favor of `board.image` (decide
   the still-open `run`-path question from above as part of this phase, since `_run_async` is the
   one place this doesn't cleanly fold in); update every internal call into `load_flash.py` to pass
   `board.layout` instead of `self.board`/a name string.
4. **CLI (`cli/__init__.py`)**: wire `--board <name>` through `resolve_board_setup()` instead of
   `build_rp2040()` directly; add `--board-spec target:attr` (+ `RP2040PY_BOARD_SPEC` env var) with
   the file-or-module `target:attr` resolver; add the by-hand incompatible-flag validation from the
   table above, mirroring `_validate_littlefs_fat12()`'s existing shape (0036).
5. **Tests + docs**: update every test that constructs a `Device` with `board: str` (same set
   `tests/test_firmware_retrieve.py`/`test_cli.py`/`test_cli_mklittlefs.py` the 2026-08-16
   `fs_blocksize` reshape already touched, plus `tests/test_boards.py`/device-level tests); write
   the actual "write your own board" how-to this whole record was originally about, now that the
   API it documents is real.

Each phase is independently mergeable and independently revertable - 1-2 land first with zero
external behavior change (pure refactor, `BOARDS`'s existing entries keep working through
`resolve_board_setup()` exactly as `build_rp2040()` did), 3-4 are the actual breaking constructor
change, 5 closes the loop. Phases 3-4 change `MicroPythonDevice`/`KalumaDevice`'s public
`__init__(image, *, board: str = ...)` signature outright, no deprecation shim - acceptable
specifically *because* this record's own "The gap" section already established there is no
documented SDK usage contract for `board`/`image` to break yet (README's "Library API" section
shows `MicroPythonDevice("...")`/`board="pico_w"` as a keyword string, but nothing has ever
promised that stays a string). Shipping the breaking change now, before any doc teaches the old
shape as stable API, is cheaper than shipping it after and needing a migration note. Per this
repo's document-vs-implement convention, writing this plan down does not authorize starting phase
1 - that needs its own, separate go-ahead, same as every other "not started" row on the tracker.

**Concrete near-term prerequisite - done 2026-08-16, and grew into a small reshape along the way.**
`device/load_flash.py` used to hardcode the littlefs block size per firmware family as plain Python
module constants - `MICROPYTHON_FS_BLOCKSIZE`/`CIRCUITPYTHON_FS_BLOCKSIZE`/`KALUMA_FS_BLOCKSIZE`,
all `4096` - not part of `firmware_specs.json`'s per-board data at all, unlike `fs_start`/
`fs_blockcount`, so `CustomFlashLayout` above had no way to express a board whose real firmware
used a different value. While moving it, a second, related smell surfaced: `flash_layout` (and
`default_tag`) already lived as *sibling* top-level dicts next to `boards`, both keyed by the same
board-name string as `boards` purely by convention, not by construction - exactly the same "two
registries that happen to agree" shape scenario 3 above already had to reason carefully about for
`firmware_specs.json` vs. `boards.py`'s `BOARDS`. Fixed at the same time: `FirmwareSpec.boards` is
now `dict[str, BoardFirmwareSpec]`, where `BoardFirmwareSpec` bundles `default_tag`, `fw: {tag:
url}`, and `layout` (now including `fs_blocksize`) together per board - everything board-specific
lives in one place instead of three dicts a caller has to know share a key. BOOTROM (board-agnostic)
keeps its own top-level `default_tag`/`known_versions`, untouched. `scripts/fetch_firmware.py`
updated to match (still hand-curates `layout` and only ever seeds a *new* board's `default_tag`,
never overwrites an existing one). `4096` is still every tracked board/family's actual value
(confirmed against real upstream MicroPython board configs, `ports/rp2/boards/*/mpconfigboard.h` -
it's the RP2040's flash sector-erase granularity, not a firmware choice) and every board keeps its
existing `default_tag`, so none of this was expected to change current behavior - verified by the
full test suite (`tests/test_firmware_retrieve.py` updated for the new `spec.boards[board].fw`/
`.default_tag`/`.layout` shape) plus `mypy`/`ruff` both clean. See 0035, which originally asserted
"block size never varies" when `fs_start`/`fs_blockcount` first became per-board data - revisited
inline there rather than left to contradict this record silently.

### Contributing upstream instead

For anyone who wants their board to get real `--board`/CLI support (not just API/`--board-spec`-
level custom setup above): that means a PR to *this* repo adding an entry to both `boards.py`'s
`BOARDS` dict *and* a new `BoardFirmwareSpec` under `firmware_specs.json`'s `boards` (per firmware
family it should support) - the two registries `--board {run,micropython,kaluma,bench,mklittlefs}`
(`cli/__init__.py:966`, all five subcommands that accept `--board`, feeding the same
`choices=tuple(BOARDS)`) actually reads from.
This is the existing, already-working path - nothing new needed, just worth stating explicitly so
users don't reach for a hand-built `BoardSetup` when what they actually want is upstream board
support.

### What this design update does and doesn't authorize

Scenario 1 is real today and can be documented as such. The `BoardSetup` design above is
**accepted** - not merely floated, per the discussion that produced it - but every phase in its
implementation plan is still **design, not code**: `boards.py`/`load_flash.py`/`BaseDevice`/
`MicroPythonDevice`/`KalumaDevice`/`_run_async`/`cli/__init__.py` all still need the actual changes
phases 1-4 describe. The one piece of this whole record that *was* implemented outright - moving
`fs_blocksize` into `firmware_specs.json` (see above) - was narrow, additive, board-count-preserving
(no behavior change for `pico`/`pico_w`), and explicitly called out as a prerequisite rather than
part of the `BoardSetup` API surface itself; it doesn't change that `BoardSetup`/
`resolve_board_setup()`/`--board-spec` remain undesigned-into-code. Per this repo's
document-vs-implement convention, this record documents an accepted design and a concrete phased
plan for it; it does not authorize starting any phase - that needs its own, separate go-ahead, same
as every other "not started" row on the tracker.

## Cleanup this work would naturally pick up

`external/device.py` and `external/led_mock.py` both cite `docs/CYW43_WIFI_BACKLOG.md` in their
docstrings (twice and three times respectively, e.g. for the "Module layout decision" / "Board
composition decision" / "Implementation order" sections). That file no longer exists - the 0032
restructure replaced it with `docs/records/` (0028 and 0029 carry those two decisions). Anyone
writing the user-facing docs will read exactly these docstrings first and hit the dead references,
so re-pointing them belongs to this task. Not a reason to do the task on its own.

## Explicitly not decided here

No API is added, no `reference/` how-to file is written (a short README pointer section to *this
record* is the one exception - see the resolved "where does it live?" question above), and the
shape of the worked example (scenario 1) is left open, same as scenarios 2/3's actual
implementation. This record exists so the gap - and now the board-authoring design - is tracked
rather than rediscovered; per this repo's document-vs-implement convention, turning any of it into
code needs a separate go-ahead.
