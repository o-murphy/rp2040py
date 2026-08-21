# 0059. Firmware resolution inside `BoardSpec`: one path for `--board` and `--board-spec`

- Status: **Implemented (2026-08-17).** Everything below landed as designed, with one deliberate
  departure on `mklittlefs --target` — see "Implementation notes" at the end, which is also where
  the live-boot verification is recorded. (Original status, kept: *Proposed — documented, not
  implemented (2026-08-17). Nothing here is built. Agreed to be the first thing implemented next
  session, ahead of other work, because two live defects (below) are waiting on it rather than on
  fixes of their own.*)
- Conceived: 2026-08-17
- Related: 0049 (`BoardSpec`/`--board-spec` board authoring - this extends its accepted design and
  supersedes one row of its flag-compatibility table), 0035 (board-aware flash offsets - where
  per-board `layout` came from), 0056 (the two board files whose duplication motivated this), 0032
  (docs structure)

## What is wrong today

`BoardSpec` carries an *already-resolved* `image` and `layout`, and there are two separate ways to
get one:

- `--board <name>` → `resolve_board_spec(board, firmware_spec, tag)` → `retrieve()` against a
  module-level `FirmwareSpec` (`MICROPYTHON`/`CIRCUITPYTHON`/`KALUMA`) plus `flash_layout()`.
- `--board-spec target:attr` → whatever the board file itself did, at **import time**.

Every board file in `boards/` therefore reimplements the first path by hand, and four concrete
problems fall out of that:

1. **A board file downloads firmware as a side effect of `import`.** `boards/micropython/
   WEACTSTUDIO/`, `boards/micropython/WAVESHARE_RP2040_LCD_0_96/` and `boards/circuitpython/
   waveshare_rp2040_lcd_0_96/` all call `retrieve()` at module level. Importing a board to read its
   *layout*, or to run `mklittlefs --board-spec` (which needs nothing but the layout), still hits
   the network. In an offline environment the import itself fails - during 0056's work this was
   worked around twice, first by patching `retrieve()` and then by hand-seeding
   `~/.cache/rp2040py` under the exact filename the URL ends with. **This record subsumes that
   bug**: it is deliberately not being fixed separately, because the fix *is* the design below -
   resolution moves to use time, and an explicit `image` keeps overriding it.
2. **`--image` is incompatible with `--board-spec`** (0049's table), for a reason that is really a
   consequence of (1): the spec already froze an image, so there was nothing left to resolve.
3. **One spec can carry only one image**, so one physical board needs one file per firmware family.
   That is exactly why 0056 ships `boards/micropython/WAVESHARE_RP2040_LCD_0_96/` and
   `boards/circuitpython/waveshare_rp2040_lcd_0_96/` as separate directories for the *same* piece
   of hardware, with the same three devices, differing only in image and flash layout.
4. **`layout` is per-family too**, and the current single field hides it: littlefs on MicroPython
   and FAT12 on CircuitPython land at different offsets for the same board.

## Accepted shape

`BoardSpec` gains **one declarative field**, of a type that already exists:

```python
@dataclass(frozen=True)
class BoardSpec:
    mcu: type[RP2040] = RP2040
    extras: tuple[ExternalDeviceFactory, ...] = ()
    layout: FlashLayout | None = None  # resolved, or an explicit override
    image: str | Path | None = None  # resolved, or an explicit override
    firmware: dict[str, BoardFirmwareSpec] | None = None  # NEW: how to resolve the two above
```

`BoardFirmwareSpec` (`utils/firmware_retrieve.py`) already carries exactly what is needed -
`default_tag`, `fw: {tag: url}`, `layout` - and the dict keys are the family names
`firmware_specs.json` already uses at its top level: `micropython`, `circuitpython`, `kaluma`.
So a board file stops importing `retrieve`/`flash_layout` entirely and just declares data; and one
file can serve several families, which collapses problem (3) and (4) together.

**Data, not a method.** An earlier sketch put a `.retrieve()` method on `BoardSpec`. Rejected: a
frozen dataclass field is inspectable, comparable, serialisable and testable without running
anything, while a method invites side effects at whatever moment someone calls it - which is the
very failure mode (1) already is. A `Callable` escape hatch for a board that genuinely needs custom
logic can be added later without changing this shape.

### A declaration is a URL *or* a local path, and `image` becomes optional

`fw`'s values are URLs today. They should accept **either a URL or a local file path**, because
`retrieve()` already coerces exactly that: the CLI's own `--image` is documented as "version tag,
local `.hex`/`.uf2` file path, or omitted", and `_download()` returns a cached file before it
fetches anything. So this is not new machinery, it is the same coercion applied one level down -
and it is what makes the *author-written* case pleasant:

```python
# the whole of a minimal board file: one board, one firmware, one variant
BOARD = BoardSpec(
    extras=(lambda: LEDMock(gpio=25), BootselButton),
    firmware={
        "micropython": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "/home/me/firmware/my-board-v1.28.0.uf2"},  # or an https:// URL
            layout={"fs_start": "0x100000", "fs_blockcount": 352, "fs_blocksize": 4096},
        )
    },
)
```

No `retrieve` import, no `flash_layout` import, no `image` - **an author never sets `image` at
all**. That field stops being part of the authoring surface and becomes purely the resolved-value
slot (and the `--image <path>` override). A board file that ships a local path is then fully
offline by construction, which is the failure mode (1) turned inside out.

The family key stays explicit even for a one-firmware board (`{"micropython": ...}`), rather than
being inferred from "there is only one entry": the family selects the flash loader and the console
behavior as well as the image (`--circuitpython` picks FAT12 + a different post-boot handshake),
so a `--circuitpython` run silently booting a lone MicroPython declaration would be a trap. An
undeclared family is an error that names what the board *does* declare.

### Resolution rules

Resolution happens **at use time** (CLI/SDK), never at import:

1. explicit `image` (from `--image <path>`, or a spec someone resolved already) wins outright;
2. otherwise `firmware[family]` + a tag (`--image <tag>`, else that entry's `default_tag`) resolves
   through the same `retrieve()` every built-in board uses - a URL goes through the usual
   `~/.cache/rp2040py` download-and-cache, a local path is used as-is with no copy;
3. `layout` follows the same order: explicit override, else `firmware[family].layout`;
4. neither available → the existing "no image" failure, unchanged (`BaseDevice` already asserts).

**No validation in `__post_init__`.** "Exactly one of `image`/`firmware`" would be wrong: a spec
legitimately exists unresolved (that is the whole point), and `dataclasses.replace()` is how it
becomes resolved - `resolve_board_spec()` and `demo/eink_run.py` both rely on that today. The
requirement is "an image by the time you boot", and it already lives where it belongs.

### The concrete payoff: 0056's board becomes *one* spec

Today the Waveshare RP2040-LCD-0.96 is two board files with identical `extras` (the same
`St7735s`, `BootselButton` and backlight `LEDMock` - the same soldered hardware), differing only in
which image and which flash layout they carry. With `firmware` keyed by family, that is one
declaration:

```python
BOARD = BoardSpec(
    extras=(St7735s, BootselButton, lambda: LEDMock(gpio=25)),
    firmware={
        "micropython": BoardFirmwareSpec(
            default_tag="1.28.0",
            fw={"1.28.0": "https://micropython.org/resources/firmware/WAVESHARE_RP2040_LCD_0_96-20260406-v1.28.0.uf2"},
            layout={"fs_start": "0xa0000", "fs_blockcount": 352, "fs_blocksize": 4096},
        ),
        "circuitpython": BoardFirmwareSpec(
            default_tag="10.2.1",
            fw={
                "10.2.1": "https://downloads.circuitpython.org/bin/waveshare_rp2040_lcd_0_96/en_US/"
                "adafruit-circuitpython-waveshare_rp2040_lcd_0_96-en_US-10.2.1.uf2"
            },
            layout={"fs_start": "0x100000", "fs_blockcount": 512, "fs_blocksize": 4096},
        ),
    },
)
```

`--circuitpython` then picks the family, exactly as it already picks the loader and console
behavior, and the devices - the part that actually describes the board - are written once. This is
the change's most visible win, and the clearest sign the current split is a workaround for a
missing field rather than a real distinction: nothing about *the board* differs between those two
files.

### `boards/` reorganises: one directory per *board*, not per firmware family

Decided together with the above, since it is the same realisation applied to the filesystem: the
per-family split (`boards/micropython/…`, `boards/circuitpython/…`) exists **only** because a spec
could carry one image. Once one spec covers every family, the family level has nothing left to
separate - the devices, the pin map, the flash geometry and the vendor documentation are all
properties of the board, not of whichever firmware happens to be flashed.

    boards/
      waveshare_rp2040_lcd_0_96/__init__.py   # one board: micropython + circuitpython inside
      weactstudio/__init__.py                 # one board: micropython today, room for more
      my_board.py                             # a single file is still fine

Naming rule to carry over: keep the firmware's own upstream board id, case-normalized. It survives
this reorganisation intact for 0056's board, where both firmwares happen to use the same string in
different cases (`WAVESHARE_RP2040_LCD_0_96` / `waveshare_rp2040_lcd_0_96`); where two firmwares
genuinely disagree on the id, pick one for the directory and cite both in the docstring, next to
the upstream files each number came from. What must not be lost is *that* citation - it is what
makes a board file checkable rather than folklore (0027's "3g rule").

Migration when this lands: fold `boards/micropython/WAVESHARE_RP2040_LCD_0_96/` and
`boards/circuitpython/waveshare_rp2040_lcd_0_96/` into one directory, move `WEACTSTUDIO` up a
level, delete the now-empty `boards/micropython/__init__.py` / `boards/circuitpython/__init__.py`,
and update the `--board-spec` paths in `README.md`,
`docs/reference/external-devices-and-boards.md`, `demo/lcd_run.py` and each board's own docstring.
The dotted-module form (`PYTHONPATH=. --board-spec boards.waveshare_rp2040_lcd_0_96:BOARD`) gets
shorter, which is a small extra win.

### Two levels of unification, one difference: who maintains the data

This unifies more than `BoardSpec`. It also unifies the *firmware* spec: built-in and custom
boards stop having two shapes for "which images exist and where does the filesystem go" and share
one (`BoardFirmwareSpec`), resolved by one function. What stays different is only the **maintenance
model**, and that difference is inherent rather than a wart:

| | built-in boards | custom board files |
|---|---|---|
| where the data lives | `firmware_specs.json`, in-tree | inline in the board file |
| who keeps it current | `scripts/fetch_firmware.py`, re-run to pick up new releases | the board's author, by hand |
| why | these are the boards this project promises to support, so the tag list must not rot | there is no API to enumerate releases for an arbitrary third-party board; the URL pattern is all its author has |

So a custom board typically pins the one tag it was built and verified against (all three files in
`boards/` do exactly that today, and say so in a comment), and gains more by an explicit edit.
That is also what makes checklist item 2 below meaningful: moving a board into
`firmware_specs.json` is precisely the moment its firmware list stops being hand-maintained and
starts being generated.

### Built-in boards move onto the same field

`BOARDS["pico"]`/`["pico_w"]` get their `firmware` populated from `firmware_specs.json`, and
`resolve_board_spec()` becomes a thin "look the name up, then resolve this spec" wrapper over the
same code path a `--board-spec` file uses. One resolution path total, instead of two that drifted.

### Flag-compatibility consequences (supersedes part of 0049)

| flag | today | after |
|---|---|---|
| `--image <path>` + `--board-spec` | incompatible | **allowed** - an explicit local file overrides whatever the spec would resolve |
| `--image <tag>` + `--board-spec` | incompatible | **allowed when the spec declares `firmware`** for the selected family; otherwise a clear error saying so |
| `--fetch-fw-only` + `--board-spec` | incompatible | allowed for a spec with `firmware` (there is finally something to pre-fetch) |
| `--circuitpython` + `--board-spec` | compatible | unchanged, but now also selects *which family* of the spec's `firmware` to resolve |
| `--target` (`mklittlefs`) + `--board-spec` | incompatible | unchanged - `--target` picks a family for a *name* lookup, which a spec does not do |

`mklittlefs --board-spec` also stops needing the network at all: it consumes `layout`, which
resolution can produce from `firmware[family].layout` without downloading anything.

## Why this unblocks the "ship community boards in the package" question

Raised alongside this (idea: `rp2040py.community.boards` / `.external` inside `src/`). The answer
today is **no, and this record is why**: a board file that downloads firmware on import is not
something to put in a wheel. Once boards are pure data, that objection disappears and the question
becomes purely one of *ownership*, which deserves an explicit bar rather than a directory move.

**Promotion checklist** - what a board in `boards/` must have before it graduates into
`boards.BOARDS` (i.e. gets real `--board` support and ships in the package):

1. every number derived from the firmware's own upstream board config, cited in the file (0027's
   "3g rule"), not measured by trial and error;
2. an entry in `firmware_specs.json` with at least one release URL per supported family;
3. a live-boot check in CI - the bar `tests/pico_spec.py` + `ci-micropython.yml`'s
   `test-board-spec` job already set, not just a local run;
4. its devices already in `rp2040py.external` (a board is not a place to hide a device
   implementation - `Epd2in9G`/`St7735s` both live in `src/` for this reason);
5. a named maintainer for the row, because a dead firmware URL becomes a user-facing bug the
   moment it ships.

Boards that do not clear that bar stay in `boards/` as examples - which is a feature, not a
holding pen: they are how someone learns to write their own. **No third `community` tier**: it
would need its own answer to every question above, with no obvious owner.

## Open questions

- **Does `scripts/fetch_firmware.py` grow a check for board files?** The maintenance split above
  says it does not generate them - but it could still *validate* the in-tree ones (are the pinned
  URLs still alive?), which is a cheap way to catch a rotted example before a user does.
- **Should `--board` names come from board files?** Once both use one path, `BOARDS` could be a
  registry *of* board files (entry points, or a directory scan). Attractive, but it reopens the
  "explicit opt-in, no hidden import-on-every-run surface" posture 0049 deliberately took; not
  part of this record.
- **Nothing here needs `--board`'s registry to change.** A merged board file is still a
  `--board-spec` target; whether `BOARDS` should eventually be built from such files is the
  separate question above.

## Implementation notes (2026-08-17)

Landed as designed. What exists now:

- `BoardSpec.firmware: dict[str, BoardFirmwareSpec] | None`, plus two functions in `boards.py`:
  `resolve_firmware(spec, family, image=None)` (the whole resolution order above, in one place) and
  `resolve_layout(spec, family)` — its image-free half, which is what lets `mklittlefs` size an
  image with no network at all. `UnknownFirmwareFamilyError` names what the spec *does* declare.
- `BOARDS["pico"]`/`["pico_w"]` carry `firmware` built straight from `firmware_specs.json`
  (`micropython`/`circuitpython`/`kaluma`), so `--board` and `--board-spec` are literally the same
  call. `resolve_board_spec()` survives unchanged as public API, now a thin wrapper: it maps its
  `FirmwareSpec` argument back to a family name (`firmware_retrieve.family_of()`), and adapts a
  caller-built one — including a `known_versions`-shaped spec like BOOTROM, which becomes one
  board-agnostic `BoardFirmwareSpec` — so custom specs keep working exactly as before.
- `firmware_retrieve.py` gained `SPECS`, `family_of()` and `board_flash_layout()` (the board-name-
  free half of `flash_layout()`, which now delegates to it).
- The CLI resolves through `_board_source()` + `resolve_firmware()` on `run`/`micropython`/
  `kaluma`/`mklittlefs` — `_resolve_board()` takes a family *name* now, not a `FirmwareSpec`, which
  incidentally makes [0061](0061-cli-family-flag.md)'s step 1 nearly free. `--image`
  and `--fetch-fw-only` work with `--board-spec`; `_validate_board_spec_flags()` is gone.
- `boards/` reorganised as designed: `boards/waveshare_rp2040_lcd_0_96/` (one file, both families —
  the two former per-family files had identical `extras`) and `boards/weactstudio/`, with the
  per-family directories and their `__init__.py`s deleted. `demo/lcd_run.py` imports the one file
  and resolves the family itself. `pyproject.toml`'s `boards/**` `N999` exemption went with them:
  the directory names are case-normalized now, so PEP8 module naming applies unassisted.

**Departure: `--target` + `--board-spec` on `mklittlefs` is now allowed, and selects the family.**
The flag table above kept them incompatible, on the reasoning that "`--target` picks a family for a
*name* lookup, which a spec does not do". That reasoning expired inside this very record: once one
spec carries several families with *different* layouts (which is exactly what
`waveshare_rp2040_lcd_0_96` does — `fs_blockcount` 352 under MicroPython, 512 under CircuitPython),
something has to pick, and `mklittlefs` has no `--circuitpython` flag of its own. So `--target` is
that selector, and it is only *needed* when a spec declares more than one family — one declaration
is selected implicitly, and a spec with an explicit `layout` skips the question entirely. Declaring
several families and passing neither `--target` nor `--block-size`/`--block-count` is an error that
says so, rather than a silent pick. This is the same "the family key stays explicit, a silent pick
would be a trap" rule the record already argued for `--circuitpython`.

One consequence worth naming, since [0061](0061-cli-family-flag.md) lists it as an open
question: `--target`'s `choices` are still the three built-in family names, so a board file that
invented a fourth family key could not be selected through it. Nothing needs one today.

**Verified.** Full suite green plus `pre-commit run --all-files`, and live against real firmware —
every image resolved offline from `~/.cache/rp2040py`, which is itself the point of (1):

| what | result |
|---|---|
| `--board pico --image 1.28.0 -c ...` (built-in path, unchanged behavior) | boots, executes |
| `--board-spec boards/waveshare_rp2040_lcd_0_96/__init__.py:BOARD -c ...` | boots real `WAVESHARE_RP2040_LCD_0_96` v1.28.0, `sys.implementation.version == (1, 28, 0)` |
| the same spec `--circuitpython` | boots real CircuitPython `10.2.1`, `board.DISPLAY` 160x80 — one file, two families, picked at run time |
| the same spec **plus `--image 1.28.0`** | boots — the combination this record set out to allow |
| `mklittlefs --board-spec <same spec> --target {micropython,circuitpython}` | 1441792 vs. 2097152 bytes (352 vs. 512 blocks) — the two families' real, different geometries off one file, no network |
| the same, no `--target` | refused, naming both families and how to pick |
| `mklittlefs --board-spec boards/weactstudio/__init__.py:BOARD_FLASH_2M` | 1048576 bytes (256 blocks), no `--target` needed — one declared family is selected implicitly |
| full round trip: that `mklittlefs` image booted back through `--board-spec --littlefs` | `os.statvfs('/')` = `(4096, 4096, 352, 350)`, and a module imported off it |
| `boards/weactstudio/` under MicroPython `1.28.0` | boots, `os.statvfs('/')` = `(4096, 4096, 3840, 3838)` — the 16 MiB variant's own derived `fs_blockcount` |
| the same file under CircuitPython `10.2.1` (a family added to it *because* this landed) | boots, `board.board_id == "weact_studio_pico"`, CIRCUITPY mounted and populated at the derived `0x100000` |

`boards/weactstudio/` gaining CircuitPython is the change's first real use by someone other than
itself: upstream ships that board under a *different id* (`weact_studio_pico` vs. MicroPython's
`WEACTSTUDIO`), which is exactly the "two firmwares disagree on the id" case the naming rule above
anticipated — one directory, both ids cited. Before this record it would have been a second file
under `boards/circuitpython/`.

CI (`ci-micropython.yml`'s `test-board-spec` job) gained four steps for the parts unit tests can't
reach: a `boards/` file resolving its own firmware at use time, `--image` alongside `--board-spec`,
`--circuitpython` selecting the second family of one file, and `mklittlefs --target` producing the
two families' different sizes (1441792 vs. 2097152 bytes) off that same file.
