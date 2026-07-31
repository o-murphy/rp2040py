# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Breaking:** `mklittlefs` no longer auto-picks the first file as `main.py` - every file now
  keeps its own basename. Pass the new `--main <basename>` (`build_littlefs_image(..., main=...)`)
  to mark one of them as `main.py` explicitly - matched against each file's basename (e.g.
  `--main app.py` for a `files` entry of `src/app.py`), not the full path, so it doesn't need
  repeating; omit it entirely for filesystems that don't need an auto-run entry point (e.g. modules
  staged only for a raw-REPL-driven test). `--main` must match one of the given files' basenames,
  or `mklittlefs` exits with a clear error instead of silently writing no `main.py`. Two files that
  would land on the same destination name - a duplicate basename, or a file already named `main.py`
  colliding with `--main`'s target - is now also a clear error instead of one silently overwriting
  the other in the image.

### Fixed
- `mklittlefs` no longer crashes (SIGABRT, `lfs_file_sync: Assertion` \`lfs_mlist_isopen(...)\`
  `failed`) under PyPy after successfully writing the image - `littlefs-python`'s C objects were
  getting finalized out of order during PyPy's interpreter shutdown. Only reproducible when
  running under PyPy specifically (e.g. via `setup-rp2040py`'s composite action, which installs
  `rp2040py` under PyPy for the emulator speedup); not an issue under CPython.

## [0.1.0b3] - 2026-07-31

### Added
- Automatic MicroPython/CircuitPython firmware download: `micropython --image` now accepts a known
  version tag (e.g. `1.21.0`, `1.28.0`, `10.2.1` for CircuitPython) in addition to a local file path,
  downloading the matching UF2 from micropython.org/Adafruit's S3 bucket into the current directory
  on first use and reusing it thereafter (`rp2040py.cli.mp_retrieve`). Omitting `--image` now falls
  back to downloading the recommended version (MicroPython 1.21.0 / CircuitPython 8.0.2) instead of
  requiring it to already be present. `ci-micropython.yml`'s separate `curl` download step was
  removed in favor of this.
- `micropython --littlefs`/`--fat12` options to point at a littlefs/FAT12 filesystem image from a
  path other than the default `littlefs.img`/`fat12.img`.
- `mklittlefs --disk-version {2.0,2.1}` to choose the littlefs on-disk format explicitly (defaults
  to `2.0`, still the safe choice for MicroPython <=1.21 - see
  [docs/PORTING.md](docs/PORTING.md#littlefs-image-format-vs-old-micropython-not-actually-a-port-bug)).
- `-V` as a short alias for `--version`.
- Test coverage raised from 56% to 66% (241 -> 345 tests): `tests/test_cli.py`,
  `tests/test_cli_mklittlefs.py`, `tests/test_cli_mp_retrieve.py`, and `tests/test_cli_intelhex.py`
  cover the CLI package end to end (previously untested at 0%), including regression tests for the
  three bugs below. `tests/test_pio.py`, `tests/test_sio.py`, `tests/test_rp2040.py`, and
  `tests/test_cdc.py` port the remaining upstream `*.spec.ts` backlog from
  [docs/PORTING.md](docs/PORTING.md), each call individually verified against the emulator rather
  than translated by argument position (see
  [docs/PORTING.md](docs/PORTING.md#pio_assemblerpys-pio_jmppio_mov-argument-order-differs-from-upstream)
  for a `pio_jmp`/`pio_mov` argument-order gotcha found along the way).

### Changed
- `fs` extra's `littlefs-python` floor raised to `>=0.18.0` (from `>=0.4.0`), matching the version
  the pinned on-disk format was verified against.
- `mklittlefs` writes source files into the image in binary mode instead of text mode, fixing
  corruption of `.mpy` (compiled MicroPython bytecode) files - and non-UTF-8/binary files in
  general, plus platform-dependent line-ending translation on `.py` sources.
- `bootrom.py`'s large bootrom constant table is now imported lazily where it's actually needed
  (`run`/`micropython`/`bench`), so commands that don't boot a device (`--version`, `mklittlefs`,
  `--help`) start faster.
- `ci-micropython.yml`'s `micropython_version` matrix now uses bare version tags (e.g. `1.21.0`)
  instead of dated firmware slugs, resolved through the new download helper.

### Fixed
- `micropython --circuitpython` was silently never loading a `fat12.img`, regardless of whether one
  existed - a regression from refactoring the littlefs/fat12 path-resolution logic into a single
  `if not args.circuitpython` branch that (incorrectly) gated both.
- The "could not find image" error messages (`micropython`, `tests/micropython_spi_run.py`) always
  printed the literal string `None` instead of the version/path that was actually requested.
- `mklittlefs`/`build_littlefs_image` now raises a clear `ValueError` for an unrecognized
  `disk_version` instead of passing `None` through to `littlefs-python` and failing with an opaque
  `TypeError`.
- `tests/micropython_spi_run.py` passed the parsed `argparse.Namespace` to `load_uf2()` instead of
  the resolved image filename, which would always raise a `TypeError`.

## [0.1.0b2] - 2026-07-31

### Added
- `rp2040py` console script (and `python -m rp2040py`) with `run`, `micropython`, and `bench`
  subcommands, so the emulator is runnable from a plain `pip install rp2040py` / `uv add
  rp2040py` / `uvx rp2040py ...` - no git checkout required. `demo/*.py` remain as thin wrappers
  around the same code for anyone working from a checkout.
- `mklittlefs` subcommand (replacing `tests/mklittlefs.py`) to build or update a littlefs image
  for the `micropython` subcommand's filesystem support - opens and updates the image in place if
  it already exists, rather than always reformatting. Needs the new optional `fs` extra
  (`pip install rp2040py[fs]`), which keeps `littlefs-python` out of the zero-dependency default
  install. Only registered as a subcommand when `littlefs-python` is actually installed.
- `micropython -c <command>` / `-m <module>` / `<filename>` (mutually exclusive), matching
  `micropython`'s own CLI: instead of dropping into the REPL, runs the given command/module/script
  on the device non-interactively via the raw-REPL protocol, prints its stdout/stderr, and exits
  with its status (0, or 1 if it raised).
- `rp2040py.device.MicroPythonDevice`, a programmatic API for booting a MicroPython/CircuitPython
  image and running code on it from another Python program - previously this was CLI-only, and the
  CLI's `micropython -c/-m/<filename>` is now itself just a caller of this API. `start()`/`exec()`/
  `exec_file()` block the calling thread; each has a `_async` twin (`start_async()`/
  `exec_async()`/`exec_file_async()`) returning a `concurrent.futures.Future`, plus `astart()`/
  `aexec()`/`aexec_file()` for asyncio. All of these share one `ThreadPoolExecutor(max_workers=1)`
  per device: since the device only has one REPL channel and can't run two `exec()`s at once,
  overlapping calls queue behind each other automatically instead of erroring, and get
  cancellation of not-yet-started calls for free from the standard library.
  `bootrom.py`/`load_flash.py`/`raw_repl.py` moved from `cli/` to the new `device/` subpackage
  accordingly (they aren't CLI-specific, and `device` importing from `cli` would have been
  circular).
- `rp2040py --version`.

## [0.1.0b1] - 2026-07-30

Initial beta release: a complete port of [rp2040js](https://github.com/wokwi/rp2040js) to Python,
capable of booting real firmware (native `.hex`/`.uf2` images, MicroPython, CircuitPython) end to
end.

### Added
- Full RP2040 emulator core: Cortex-M0+ CPU, all peripherals (DMA, PIO, USB, UART, SPI, I2C, ADC,
  PWM, timers, GPIO, interpolators, etc.), GDB server, and the `demo/`/`tests/` runner scripts
  needed to actually boot firmware in the emulator.
- CI workflows (`ci-micropython.yml`, `ci-pico-sdk.yml`) that boot real firmware end to end across
  a `python_runtime` matrix (CPython 3.10, CPython 3.14 with `PYTHON_JIT=1`, PyPy 3.10), plus
  `pre-commit.yml` for lint/type/test checks and coverage reporting.
- `demo/benchmark.py`, a reproducible synthetic and real-firmware-boot benchmark.

### Fixed
- `tests/mklittlefs.py` now pins the littlefs on-disk format to v2.0 (`disk_version=0x00020000`)
  regardless of the installed `littlefs-python` version, so generated filesystem images stay
  mountable by MicroPython releases across the full range tested in CI, not just the newest ones.

### Performance
- `CortexM0Core.execute_instruction()` now dispatches through a precomputed O(1) table instead of
  a linear `if`/`elif` scan, alongside several smaller hot-path optimizations (see
  [docs/PORTING.md](docs/PORTING.md#known-differences-from-rp2040js) for the full breakdown and
  measurements). Combined effect versus the initial port: real MicroPython + littlefs boot time
  dropped from minutes to seconds under CPython, and to single-digit seconds under PyPy.

[Unreleased]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b2...HEAD
[0.1.0b2]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/o-murphy/rp2040py/releases/tag/v0.1.0b1