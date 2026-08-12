# 0035. Board-aware MicroPython filesystem flash offset

- Status: Proposed — root cause confirmed, fix designed, not yet implemented
- Conceived: 2026-08-12
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

## Open questions

- **CircuitPython not checked yet** - `CIRCUITPYTHON_FS_FLASH_START`/`_BLOCKCOUNT` are a second,
  separate pair of hardcoded constants in the same file with the same shape of risk (a `pico_w`-
  class board with a bigger compiled binary could hit the identical bug) - not verified against
  real CircuitPython board config in this pass.
- **Whether `MICROPY_HW_FLASH_STORAGE_BASE`/`_BYTES` are stable across MicroPython releases** - the
  fix above assumes one fixed pair of values per board works for every tracked version tag; not
  verified against anything but the current `v1.28.0` checkout.
