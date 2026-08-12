# 0036. `--littlefs`/`--fat12` should be mutually exclusive

- Status: Proposed — documented only, not implemented
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
