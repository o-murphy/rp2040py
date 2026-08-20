# 0036. `--littlefs`/`--fat12` should be mutually exclusive

- Status: Implemented (2026-08-13)
- Conceived: 2026-08-12
- Related: found while working on 0035 (board-aware FS flash offset)

## Problem

`cli/__init__.py`'s `micropython` subcommand accepts both `--littlefs` and `--fat12`, intended for
two different, mutually-exclusive modes (`--littlefs` for a MicroPython run, `--fat12` for
`--circuitpython`). They aren't actually validated as exclusive - which one takes effect is decided
implicitly by the `--circuitpython` flag:

```python
littlefs = (
    args.littlefs if not args.circuitpython and args.littlefs is not None and Path(args.littlefs).exists() else None
)
fat12 = args.fat12 if args.circuitpython and args.fat12 is not None and Path(args.fat12).exists() else None
```

A user passing both `--littlefs foo.img --fat12 bar.img` without `--circuitpython` gets `foo.img`
loaded and `bar.img` **silently dropped**, with no warning or error - not what either an accidental
double-flag or a genuine misunderstanding of the two modes should produce.

## Proposed fix (not implemented)

Add an explicit `argparse` mutual-exclusion check (or an `add_mutually_exclusive_group()`) for
`--littlefs`/`--fat12` on the `micropython` subcommand, erroring clearly if both are given -
matching the existing pattern for other genuinely-exclusive flag pairs in this CLI (e.g.
`--tcp-port`/`--pty`/`-c`/`-m`/`<filename>`'s own exclusivity checks nearby in the same file).

## Implemented — 2026-08-13

Added `_validate_littlefs_fat12()` in `cli/__init__.py`, next to `_validate_console_mode()`, called
from `_micropython_async()` before the existing (unchanged) circuitpython-gated littlefs/fat12
resolution: errors with `"--littlefs and --fat12 are mutually exclusive"` and `sys.exit(1)` if both
flags are given, regardless of `--circuitpython`. The implicit-drop resolution logic itself was left
alone - out of scope per `docs/tasks/littlefs-fat12-exclusivity.md`'s scope check.

Test coverage in `tests/test_cli.py`: `_mp_args()`'s `littlefs`/`fat12` defaults changed from
placeholder filenames to `None` (both being non-`None` by default meant nearly every existing
`_mp_args()`-based test was unknowingly exercising the double-flag case), the two existing
littlefs/fat12-mode regression tests updated to pass only the one flag each is actually testing, and
two new tests added for the mutual-exclusivity error itself (with and without `--circuitpython`).

## Follow-through — 2026-08-20: the implicit-drop resolution is gone too

The 2026-08-13 fix deliberately stopped at the double-flag case and left "the implicit-drop
resolution logic itself" alone. That is now closed as well, on the maintainer's call while working
through [0089](0089-one-reset-for-every-trigger.md), and from the observation that makes it simple:
**the filesystem format is a property of the firmware family, not a choice** - MicroPython on rp2
reads littlefs, CircuitPython reads FAT12, and no other pairing was ever valid.

- `MicroPythonDevice.__init__()` takes one `filesystem=` argument instead of the `littlefs=`/
  `fat12=` pair, and picks the loader by `circuitpython` - the same way `dump_flash_image()` had
  always picked its dumper. Passing both, or the wrong one for the family, stops being expressible.
- `_validate_littlefs_fat12()` became **`_validate_filesystem()`**, which validates *and* returns
  the resolved image (or `None`), replacing the separate circuitpython-gated resolve-and-log block
  in `_micropython_async()`: one function answers "which flag is meaningful here", instead of two
  places answering it and drifting. It also now rejects the *mismatched* flag outright - `--fat12`
  without `--circuitpython`, or `--littlefs` with it - which used to boot cleanly with no
  filesystem loaded and no hint as to why.
- A named-but-missing image is still skipped rather than an error (README's "silently skipped if
  absent"): that is a fact about the file, not about the flag.

Tests: `tests/test_cli.py`'s fake device follows the constructor (`filesystem`, no
`littlefs`/`fat12`), plus two new tests for the mismatched-flag errors. `demo/lcd_run.py`,
`demo/wifi_lcd_run.py` and `demo/mkfat12.py`'s docstring updated to the new keyword. Verified live
end to end: `rp2040py micropython --image 1.23.0 --littlefs <img> --expect-text "Hello,
MicroPython!"` still passes.
