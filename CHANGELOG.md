# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  image and running code on it from another Python program (`device.exec("print(1+1)")`,
  `device.exec_file(path)`), as a context manager wrapping a daemon-thread `Simulator` plus the
  raw-REPL protocol - previously this was CLI-only. The CLI's `micropython -c/-m/<filename>` is
  now itself a caller of this API. `bootrom.py`/`load_flash.py`/`raw_repl.py` moved from `cli/` to
  `device/` accordingly (they aren't CLI-specific, and `device` importing from `cli` would have
  been circular).
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

[Unreleased]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/o-murphy/rp2040py/releases/tag/v0.1.0b1