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
