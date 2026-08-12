# 0035. Board-aware MicroPython/CircuitPython/Kaluma filesystem flash offset

- Status: Implemented — measured (2026-08-12)
- Conceived: 2026-08-12 · Implemented: 2026-08-12
- Related: 0027 (CYW43439/Pico W epic, where this was found), 0034 (`_execute_batch()` native
  port, during whose re-verification this was found)

## Context

0027's "Performance side quest, continued" entry documented a `pico_w` "wild execution" finding
(CPU ends up at `0xfffffffe`) with root cause not yet identified. Root-caused in this record.

## Root cause (confirmed via disassembly + runtime tracing + real MicroPython source, not guessed)

Real ARM/GCC startup code (`crt0`) on RP2040 firmware runs a table-driven flash→RAM copy (a
`{src, dst_start, dst_end}` triple per region, terminated by a zero `src`) before calling
`__libc_init_array()` (the standard C runtime constructor-array walker: `ldmia r4!, {r3}; blx r3`
per entry). Confirmed by disassembling the real `v1.28.0` `RPI_PICO_W` UF2 directly
(`arm-none-eabi-objdump -D -b binary -m arm --disassembler-options=force-thumb`) at the crash PC
and its callers - no ELF/symbols needed, the raw Thumb-2 matches this pattern unambiguously.

The crash: one `__libc_init_array` entry, read from RAM at `0x2000155c`, is `0xffffffff` -
`blx 0xffffffff` masks to PC `0xfffffffe`, which is unmapped, producing the "wild execution"
signature (millions of "invalid memory address" reads across nearly the full 32-bit space,
`0x0041fd80`-`0xffffffff`, as the CPU free-runs through whatever garbage/undefined dispatch
follows).

**Traced to a real emulator bug in `device/load_flash.py`, not firmware/CYW43 protocol at all:**

- `MICROPYTHON_FS_FLASH_START = 0xA0000` is a single, hardcoded constant used for *every*
  MicroPython board.
- The crashing RAM address (`0x2000155c`) is *inside* the copy-table's one real entry:
  `src=0x100d43fc, dst=[0x200000c0, 0x200015a8)` (5352 bytes) - confirmed by reading the actual
  UF2 bytes at the copy-table's own address (`0x10000238`), decoded as three `<III` words per
  entry.
- **The flash source bytes at `0x100d43fc`-adjacent addresses are correct, valid function
  pointers** when read directly from the loaded UF2 (`rp2040.flash[...]`, no littlefs loaded) -
  confirmed by dumping and disassembling them. They read back as `0xffffffff` only once
  `load_micropython_flash_image()` is also called - confirmed by instrumenting
  `RP2040.write_uint32` during a real boot: the copy loop's own `stmia` writes `0xffffffff` to
  `0x2000155c`, sourced from flash `r1=0x100d5898`, which independently reads back correctly
  *without* a littlefs image loaded.
- **`0xA0000` is only correct for plain `RPI_PICO`.** Confirmed directly from the real MicroPython
  source (`ports/rp2/rp2_flash.c`): `MICROPY_HW_FLASH_STORAGE_BASE = PICO_FLASH_SIZE_BYTES -
  MICROPY_HW_FLASH_STORAGE_BYTES` - computed from the *end* of the (physically identical, 2MB)
  flash chip, sized by a board-specific `MICROPY_HW_FLASH_STORAGE_BYTES`:
  - `boards/RPI_PICO/mpconfigboard.h`: `1408 * 1024` → base `0x200000 - 0x160000 = 0xA0000`
    (matches this project's existing hardcoded constant exactly - not a coincidence, it was
    presumably derived from the plain-`pico` case originally).
  - `boards/RPI_PICO_W/mpconfigboard.h`: `848 * 1024` (smaller - leaves more flash for the
    CYW43 driver/lwIP network stack compiled into the pico_w build) → base
    `0x200000 - 0xD4000 = 0x12C000`.
  - Real firmware even has its own safety net for exactly this class of bug: `rp2_flash.c` asserts
    `__flash_binary_end - XIP_BASE <= MICROPY_HW_FLASH_STORAGE_BASE` at flash-object construction -
    on real hardware, a firmware binary that grew past its board's own configured boundary would
    trip this assert (loud, at boot) rather than silently corrupt itself. This project's hardcoded
    single constant bypasses that safety net's entire purpose: it silently lets
    `load_micropython_flash_image()` write straight over the tail of `pico_w`'s (larger) compiled
    binary, which is exactly this table's own copy-table source (`0x100d43fc`).
  - **This is why plain `pico` never showed the bug**: `RPI_PICO`'s smaller firmware's own
    `.data`/copy-table region sits well clear of `0xA0000`, so the collision the hardcoded constant
    causes for `pico_w` specifically never manifests there. Confirmed empirically too, not just
    from the math: the identical trivial script against the plain (non-`_W`) `RPI_PICO` `v1.28.0`
    UF2 never reproduces the wild-execution signature.
- **Kaluma checked too, found to need no *board-aware* fix**: `kaluma-project/kaluma`'s (GitHub,
  `master` branch) `targets/rp2/boards/{pico,pico-w}/board.js`/`board.h` are identical between the
  two boards (`new Flash(132, 128)`, `KALUMA_FLASH_SECTOR_COUNT=260`,
  `KALUMA_PROG_SECTOR_COUNT=128`, ...) - Kaluma's own firmware reserves the same fixed code budget
  regardless of board, so `KALUMA_FS_FLASH_START`/`KALUMA_PROG_FLASH_START` don't need to vary by
  board the way MicroPython's do. **Still moving into the same single data source as MicroPython's
  values (decided in discussion, not just a correctness question)** - not because Kaluma needs
  per-board values, but so every firmware family's flash layout lives in one place
  (`firmware_specs.json`) instead of MicroPython's living there while Kaluma's stays a parallel set
  of hardcoded Python constants in `device/load_flash.py`. Kaluma's entry is simply board-invariant
  (the same `{"fs_start": ..., "fs_blockcount": ...}` shape, one value reused for every board key,
  or a single flat value if the schema ends up distinguishing "varies by board" from "doesn't" -
  exact shape TBD when implemented).

## Fix design (not yet implemented)

Move the two known-real MicroPython values into data, not a second hardcoded Python constant -
mirroring how `utils/firmware_specs.json`/`FirmwareSpec` already handles board-specific data
(download URLs, `boards: dict[board, dict[tag, url]]`) rather than a parallel ad hoc mechanism:

1. **`utils/firmware_specs.json`**: add a `flash_layout` key to *both* the `"micropython"` and
   `"kaluma"` entries (sibling to the existing `"boards"` key), not just MicroPython's - single
   source of truth for every firmware family's flash placement, per the discussion above, even
   though Kaluma's happens to be board-invariant:
   - `"micropython"`: `{"pico": {"fs_start": "0xA0000", "fs_blockcount": 352}, "pico_w":
     {"fs_start": "0x12c000", "fs_blockcount": 212}}`.
   - `"kaluma"`: same shape, both board keys pointing at the same values (fs + prog region -
     Kaluma has two flash regions, MicroPython/CircuitPython have one) - exact shape (whether to
     special-case "board-invariant" vs. just repeat the value under every board key) decided at
     implementation time, not designed further here.
   - `FirmwareSpec` (dataclass, `utils/firmware_retrieve.py`) gets a matching new frozen field,
     populated for `MICROPYTHON`/`KALUMA`, `None` for `CIRCUITPYTHON`/`BOOTROM` unless the open
     question below turns out to apply to CircuitPython too.
2. **`scripts/fetch_firmware.py`**: since this is the one place `firmware_specs.json` gets
   regenerated from authoritative sources (per that script's own existing convention - "run it,
   diff, commit," not maintained by hand), it needs to populate `flash_layout` too, not just leave
   it for someone to hand-edit once and forget to update. Source: the same two upstream constants
   used above (`MICROPY_HW_FLASH_STORAGE_BASE`/`_BYTES` per board) - worth checking whether they've
   ever changed release-to-release before assuming they're static across all tracked MicroPython
   versions (not done here).
3. **`device/load_flash.py`**: `load_micropython_flash_image()`/`dump_micropython_flash_image()`
   gain a `board: str` parameter, looking up `MICROPYTHON.flash_layout[board]` instead of the
   module-level `MICROPYTHON_FS_FLASH_START`/`MICROPYTHON_FS_BLOCKCOUNT` constants (which get
   deleted, not deprecated-in-place - nothing outside this fix should keep reading them once board-
   awareness lands).
4. **Call sites needing a `board` argument threaded through**, all of which already know or can
   easily obtain it:
   - `cli/__init__.py`: `_bench_firmware()`, the `micropython` subcommand's littlefs load, both
     already take `--board`.
   - `device/mp_device.py`'s `MicroPythonDevice` - already constructed against a specific board's
     `RP2040`/`Simulator`; store `board` at `__init__` time for `load_micropython_flash_image()`/
     `dump_micropython_flash_image()` to use.
   - `cli/mklittlefs.py` - **needs `--board` added as a new flag, used *in tandem with* the
     existing `--target`** (per direct discussion, not just inferred): `--target micropython
     --board pico_w` must build a 212-block image sized/offset for `pico_w`, not silently reuse
     `pico`'s 352-block layout. Kaluma's `--target` stays board-independent per the finding above
     (no `--board`-driven size/offset change needed there, only for `micropython`/`circuitpython`
     if that turns out to matter too - not checked yet, see "Open questions").
   - `tests/micropython_spi_run.py`, `tests/test_cli_mklittlefs.py` - update call sites, no design
     question, just following the new signature.

## Implemented (2026-08-12)

Built as designed above, with one addition made during implementation: **CircuitPython audited
too**, not left as an open question - real source (`adafruit/circuitpython`,
`ports/raspberrypi/{mpconfigport.h,link-rp2040.ld}` +
`boards/raspberry_pi_pico{,_w}/{mpconfigboard.mk,link.ld}`) confirmed the identical bug shape:
`CIRCUITPY_CIRCUITPY_DRIVE_START_ADDR = CIRCUITPY_FIRMWARE_SIZE + CIRCUITPY_INTERNAL_NVM_SIZE(4096)`
- plain `pico`'s default `firmware_size = 1020K` (no board override) gives `0x100000`, matching
  this project's pre-existing constant exactly (was already correct, by luck or original intent).
  `pico_w` overrides `firmware_size = 1532K` (`boards/raspberry_pi_pico_w/link.ld`, "Must be
  accompanied by a linker script change" per its own `mpconfigboard.mk` comment - same underlying
  reason as MicroPython's, the CYW43 driver/lwIP stack needs the room) → real start `0x180000`,
  not `0x100000`. `fs_blockcount` left at the existing `512` for both boards (unlike
  MicroPython's, which genuinely shrinks on `pico_w`) - rp2040py's own emulated flash buffer is
  16MB (`_rp2040.py`), far bigger than either board's real 2MB chip, so only the *start* address
  needs to be correct; a generously-sized region past it doesn't collide with anything either
  board's real firmware actually uses.

`firmware_specs.json` gained a `flash_layout` key on all three of `micropython`/`kaluma`/
`circuitpython` (Kaluma's board-invariant, stored per-board anyway for one uniform shape -
confirmed via `kaluma-project/kaluma`'s own `board.h`/`board.js`, identical between boards).
`scripts/fetch_firmware.py` gained matching `_MICROPYTHON_FLASH_LAYOUT`/`_KALUMA_FLASH_LAYOUT`/
`_CIRCUITPYTHON_FLASH_LAYOUT` constants (hand-curated, not fetched live - no API exists for these,
they're board hardware-config constants not release metadata) that `main()` now writes into the
regenerated spec every run, so the JSON's copy can't silently drift from its cited source.
`utils/firmware_retrieve.py` gained `FirmwareSpec.flash_layout` (a new frozen field) and a
`flash_layout(spec, board)` helper resolving the JSON's hex-string values into plain ints.
`device/load_flash.py`'s six load/dump functions for MicroPython/Kaluma/CircuitPython all take a
new required `board: str` parameter, resolving their real offsets via that helper instead of
module-level constants (which are now deleted, not deprecated in place - `MICROPYTHON_FS_BLOCKSIZE`
etc. stay, since block *size* never varies, only start/count).

`device/base_device.py`'s `BaseDevice.__init__` now stores `self.board`, so
`MicroPythonDevice`/`KalumaDevice` (both already accepted a `board` constructor arg) can resolve
it again later for `dump_flash_image()`. `cli/mklittlefs.py`'s `mklittlefs` subcommand gained
`--board` (via the existing `parents=[_shared_arg_parser(...)]` mechanism, used in tandem with
`--target`, per direct discussion during design) - `_target_fs_layout(target, board)` now resolves
the real size for whichever board `--board` names, replacing the old static `_TARGET_FS_LAYOUTS`
dict.

**Verified:**
- `uv run pre-commit run --all-files` clean (mypy, ruff, pytest both backends).
- **The original crash is gone.** Re-ran the exact reproduction from 0027's own investigation
  (`scan_range.py`-style address tracing against `tests/micropython/main-cyw43.py`'s real content,
  now built into a littlefs image with `mklittlefs --board pico_w` so it lands at the *correct*
  `0x12c000` offset): distinct invalid-address hits dropped from 987K (spanning
  `0x0041fd80`-`0xffffffff`) to 2, in the same tiny benign range (`0x14000004`-`0x14002000`) a
  healthy boot with no littlefs at all already shows - i.e. indistinguishable from "nothing wrong
  here." A longer, non-bounded run also progressed 24M+ instructions (5.8s) without incident,
  versus crashing within the first few hundred thousand before this fix.

**Not resolved by this fix - a real, different, next issue found while verifying end-to-end via
the actual CLI (`rp2040py micropython --board pico_w tests/micropython/main-cyw43.py`, not just
the bounded tracing harness above):** the full boot still doesn't reach the script's own output
within 30-35s. Debug-level logging shows a suspiciously uniform (~0.24s real-time interval)
repeating sequence - `[CortexM0Core] SEV` / `[USB] Start USB transfer, ...]` / `[PIO1]
clkDivRestart not implemented` - textually identical each cycle, which reads more like a stalled
retry loop than genuinely-slow-but-progressing SPI bit-banging. Not investigated further in this
pass - flagged as a new, separate open item in 0027 rather than pursued here.

## Open questions

- **Whether `MICROPY_HW_FLASH_STORAGE_BASE`/`_BYTES` (and CircuitPython's/Kaluma's equivalents)
  are stable across every tracked version tag** - the fix above assumes one fixed pair of values
  per board works for every release; verified against the current checkouts only (MicroPython
  v1.28.0, current CircuitPython/Kaluma `main`/`master`).
- The still-open `[PIO1] clkDivRestart not implemented` stall noted above - real root cause not
  yet investigated.
