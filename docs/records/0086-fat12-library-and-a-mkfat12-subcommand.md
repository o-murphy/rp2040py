# 0086 - A FAT12 library dependency, and a `mkfat12` CLI subcommand

- Status: **Proposed. Nothing here is implemented, and deliberately so** - the demo-level half
  landed in [0085](0085-circuitpython-code-py-and-wifi-on-screen.md), and this record is the plan
  for the parts that need an API decision first (what the optional dependency looks like in
  `pyproject.toml`, and what the CLI surface is).
- Depends on: [0085] (`demo/mkfat12.py`, the hand-rolled 8.3 builder this would grow past),
  [0010](0010-littlefs-dump-fs.md)/`mklittlefs` (the shape a CLI subcommand should mirror).

## Where this stands today

`demo/mkfat12.py` builds the CIRCUITPY volume CircuitPython auto-runs `boot.py`/`code.py` from.
It writes **root-level 8.3 names only** with no dependencies at all, and hands anything else - a
long name (`settings.toml`), or a path (`lib/greeter.py`) - to `pyfatfs`, declared in that file's
own PEP 723 block so `uv run --script demo/mkfat12.py` installs it for that script alone. The
project itself takes no dependency on it, and a plain `python demo/mkfat12.py` still builds an 8.3
image with nothing installed.

**The pure-Python 8.3 builder stays** until there is a settled alternative - that is a deliberate
decision, not an oversight. It is what every `--code`/`--boot` run in `demo/lcd_run.py` and
`demo/wifi_lcd_run.py` uses, it is covered by `tests/test_demo_mkfat12.py`, and it costs the
project nothing.

## Two things to build, once the API is decided

1. **A real optional dependency**, the way `fs = ["littlefs-python>=0.18.0"]` already is in
   `pyproject.toml` - e.g. a `fat` extra - with the CLI registering its subcommand only when the
   library is importable (`cli/__init__.py`'s existing `_HAS_LITTLEFS` pattern: advertise it in
   `--help` when it is installed, rather than adding it unconditionally and failing lazily once
   invoked).
2. **A `mkfat12` subcommand**, mirroring `mklittlefs`: build an image from a directory or a file
   list, `-f`/`--force` to overwrite, and the volume/region sizes resolved from `--board`/
   `--board-spec`'s own CircuitPython `layout` rather than passed as raw byte counts the way the
   demo script takes them. `--dump-fs` already exists as the read side and needs nothing new.

Note the asymmetry with `mklittlefs` worth designing around: MicroPython's own firmware can write
its filesystem over the raw REPL (`demo/mklittlefs_dump.py` exists precisely so `littlefs-python`
is optional), and CircuitPython's cannot - it refuses to write CIRCUITPY while USB is attached. So
there is no "let the firmware do it" fallback on this side; whatever library is chosen is the only
route to long names, and the 8.3 builder is the only dependency-free floor.

## Library survey (2026-08-19)

Three candidates on PyPI. Every one of them is pure Python (`py3-none-any`, no compiled
extensions), so none is disqualified on the portability grounds that ruled out `mkfs.fat`/`mtools`
in the first place - this project ships wheels for Linux, Windows, macOS **and** Android, and its
`pre-commit` matrix runs three of those.

### `pyfatfs` 1.1.0 - what the demo uses today

- MIT, `Requires-Python ~=3.8`, and it does the job: LFN, subdirectories, mkfs, and reads back.
  Live-verified end to end - an image built with it boots on the emulated Waveshare board, with
  guest `code.py` importing from `/lib` and `os.getenv()` reading a real `settings.toml`.
- Dependency chain is its weak point: `pyfatfs` → `fs` (PyFilesystem2 2.4.16, last released 2022)
  → `appdirs` (deprecated in favour of `platformdirs`), `six`, `setuptools`.
- **`import fs` is broken on current setuptools**: `fs/__init__.py` calls
  `__import__("pkg_resources").declare_namespace(__name__)`, and `pkg_resources` is being removed.
  A clean environment with only `pyfatfs` declared fails with `ModuleNotFoundError: No module
  named 'pkg_resources'`; the demo script pins `setuptools<81` alongside it to work around this.
  That pin is acceptable for one script's own PEP 723 block and is **not** acceptable for a
  library's declared extra without revisiting.
- Rough edges around `mkfs`: it opens its target `'rb+'`, so the file must already exist at full
  size; `close()` after `mkfs()` raises `TypeError` (its `_mark_clean()` indexes a FAT table
  `mkfs()` never fills in) *after* having flushed and closed the handle; and `__del__` only
  swallows the library's own exception type, so it raises again at collection unless `close` is
  neutered.

### `PyFAT12` 0.8.post2 - unusable from PyPI

MIT, no dependencies, FAT12-specific - on paper the best fit of the three. In practice its only
release is **mispackaged**: the 3.5 KB wheel's `top_level.txt` says `path`, and it ships
`path/__init__.py` + `path/path.py` (a generic basename/join helper) and no `pyfat12` package at
all. `import pyfat12` fails after installing it, and what it *does* install collides with the
`path` package. Usable only by vendoring the source from `github.com/hisahi/PyFAT12`, which is a
different decision from taking a dependency.

### `FATtools` 1.1.23 - capable, but GPL

- Far the most capable: FAT12/16/32 + exFAT, LFN, partitions, VHD/VDI/VMDK images, and a `mkfat`
  that works straight on an in-memory stream (`fat_mkfs(BytesIO, size, params={"fat_bits": 12})`
  produced a correct `totsec16 = 2048` FAT12 volume with no temp file - cleaner than pyfatfs's
  file-first `mkfs`). Writing LFN names and directories works.
- **GPLv3, where this project is MIT.** That is the decisive question and it is not a technical
  one: it needs an explicit decision rather than being absorbed into a demo's dependency list.
- Also pulls `hexdump`, and its read side did not behave in two straightforward attempts here:
  `root.open(name).read()` raised `AttributeError: 'NoneType' object has no attribute 'wADate'`
  inside `update_time()`, and `copy_out(root, ["settings.toml", "lib"], dest)` extracted only the
  directory. Not investigated further - the licence question comes first.

### Where that leaves the choice

Nothing here is obviously right, which is exactly why this record is a plan and not a patch:

- `pyfatfs` works and is MIT, but drags a legacy chain and needs a `setuptools` pin that should
  not be inherited by anyone installing `rp2040py[fat]`.
- `FATtools` is technically the best of the three and is licence-incompatible-by-default with an
  MIT project.
- `PyFAT12` would be ideal and is not installable.
- A fourth option stays open: **grow the 8.3 builder's own LFN support** (~40 lines - the name in
  UTF-16LE across 13-character slots, the 8.3 checksum, the ordinal bytes; subdirectories are a
  little more) and take no dependency at all. That keeps `demo/mkfat12.py`'s "needs nothing
  installed" property, which is the whole reason it exists, and is the only option whose cost is
  bounded by code we own.
