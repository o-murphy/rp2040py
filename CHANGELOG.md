# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `kaluma` subcommand: runs [Kaluma](https://kaluma.io/) (a JavaScript runtime for RP2040)
  UF2 images, interactive REPL only - Kaluma has no raw-REPL-equivalent protocol, so unlike
  `micropython` there's no `-c`/`-m`/`<filename>`. Missing firmware is downloaded automatically
  (`rp2040py.cli.firmware_retrieve`, defaulting to `1.2.1` - the newest release still shipping a
  plain, non-`-w`, RP2040 `pico` build). `demo/kaluma_run.py` is now a thin wrapper around it
  (`--image` is now optional there too), matching `demo/micropython_run.py`. `--expect-text` works
  the same way it does for `micropython` (watches serial output for a substring, exits 0 once
  found). After connecting, `kaluma` sends `.hi\r\n` (a REPL command, not just a blank line) to
  deterministically reprint the "Welcome to Kaluma" banner - the one Kaluma prints once at actual
  boot is racy and gets lost before the emulated USB-CDC connection is up (confirmed empirically:
  a bare `\r\n` nudge never reproduced it, `.hi` did every time), same as real hardware racing a
  host terminal that isn't already attached (Kaluma's own docs: "if you cannot see the prompt,
  press Enter several times").
- `ci-kaluma.yml`: boots real Kaluma 1.2.1 firmware end to end (across the same
  `python_runtime` matrix as `ci-micropython.yml`) and checks for the (`.hi`-reprinted) boot
  banner via `--expect-text`. Kaluma has no MicroPython-`main.py`-equivalent auto-run-from-filesystem
  mechanism (confirmed by reading kaluma-project/kaluma's boot sequence directly - the "user
  program" auto-run path is a separate flash region written via `.flash -w`/YMODEM, not the
  littlefs `fs` mount), so this is a boot-only smoke test, not an `mklittlefs`-staged code-execution
  test like `micropython`'s CI.
- Kaluma littlefs filesystem support: `--littlefs` on the `kaluma` subcommand
  (`rp2040py.device.load_flash.load_kaluma_flash_image`), mounted at the same flash region
  (`0x180000`, 4096-byte blocks, 128 blocks) Kaluma's own `pico`/`pico-w` `board.js` uses
  (`new Flash(132, 128)`) - confirmed by reading kaluma-project/kaluma's source directly. Build a
  compatible image with `rp2040py mklittlefs --block-size 4096 --block-count 128`.
- `rp2040py.device.base_device.BaseDevice`: the UF2-boot lifecycle (load image, create the
  USB-CDC console, block `start()`/`stop()` around actually running the emulator) shared by
  `MicroPythonDevice` and the new `KalumaDevice`, instead of each hand-rolling it.
- `kaluma <script.js>`: stages a local `.js` file into Kaluma's "user program" flash region
  (`rp2040py.device.load_flash.load_kaluma_program`, `KalumaDevice(program=...)`) before boot -
  the same flash region (offset `0x100000`, 512K, raw source + a `\0` terminator - no ELF/YMODEM
  framing) `kaluma flash <file>` writes to on real hardware, confirmed by reading
  kaluma-project/kaluma's `src/prog.c`/`src/runtime.c` directly. The write itself is correct and
  covered by unit tests (confirmed the staged bytes land at the right flash offset before boot even
  starts), but **auto-run isn't verified working end to end yet**: the staged program's output was
  never observed in manual testing, and `board.js`'s littlefs-mount failure (see `--littlefs` below)
  turned out *not* to be the cause - `run_board_module()` (`src/global.c`) catches and prints that
  error without aborting, so `km_runtime_load()` (`src/runtime.c`) still runs afterward regardless.
  Current best theory: `km_running_script_check()` (`src/system.c`) gates auto-run on reading GP22
  with a pull-up enabled, and this emulator's GPIO model (`gpio_pin.py`) tracks
  `pullup_enabled`/`pulldown_enabled` as metadata without resolving them into the actual bit a bus
  read sees for a pin nothing drives (`_raw_input_value` stays `False`, i.e. reads low, regardless
  of the pull-up) - which would make `km_running_script_check()` always read "skip loading" in this
  emulator, matching the observed symptom exactly. Not yet fixed or confirmed; `mklittlefs`/CLI
  wiring for this ships as-is since the write path is correct in isolation, but treat
  `kaluma <script.js>` as unverified until the GPIO pull-resolution question is resolved -
  `ci-kaluma.yml` deliberately stays on the boot-only smoke test above rather than asserting on
  this.
- `mklittlefs -f`/`--force`: required to overwrite an existing `--output` path, and `files` may
  now be empty (producing a freshly formatted, empty image) - see below.

### Changed
- **Breaking:** `mklittlefs`'s output image path is now `-o`/`--output <path>` (defaults to
  `littlefs.img`, matching `micropython --littlefs`'s own default) instead of a required
  positional argument - `files` is now the first positional instead of the second. Lets
  `rp2040py mklittlefs your_main.py --main your_main.py` work without also having to spell out
  `littlefs.img` explicitly, since that's the same default `micropython` already looks for.
- **Breaking:** `mklittlefs` no longer opens an existing `--output` and updates it in place -
  it now always builds a fresh image from scratch, and refuses to overwrite an existing file
  unless `-f`/`--force` is given (raising a clear error instead). The old "update in place"
  behavior silently trusted whatever `--block-size`/`--block-count` built the existing file,
  regardless of what was passed on this run - reusing an output path with different values (e.g.
  MicroPython's default block count vs Kaluma's) produced a corrupted or wrong-sized image with no
  warning; confirmed reproducible even against a validly-built image, not just a stale/foreign
  one. There's no way to recover the previous "merge new files into an existing image" behavior -
  rebuild from the full file list instead.

### Fixed
- `micropython --circuitpython --image v8.0.2` (a `v`-prefixed version tag) silently 404'd instead
  of downloading - CircuitPython's resolution path never stripped the `v`, unlike MicroPython's
  (dead code: `mp_retrieve.py`'s `is_circuitpython` branch skipped the very function that did the
  stripping). Fixed as part of unifying firmware retrieval below, which no longer has a
  CircuitPython-specific code path to skip it.

### Internal
- `RawReplRunner`'s FIFO-backpressure/threading plumbing (`pump()`/queueing, `cdc.on_serial_data`
  wiring) is now shared with the CLI's interactive stdin forwarding and `demo/kaluma_run.py`
  through a new `BaseReplRunner`/`InteractiveRepl` base (`device/repl_runner.py`) and
  `StdioInteractiveRepl` (`cli/stdio_repl.py`), instead of three separate hand-rolled copies of the
  same backpressure loop. No behavior change for CLI users.
- `cli/mp_retrieve.py` and `cli/kaluma_retrieve.py` merged into `cli/firmware_retrieve.py`: one
  declarative `FirmwareSpec` per firmware (filename/URL templates, default tag, optional
  known-version-tag table), loaded from `cli/firmware_specs.json`, plus a single generic
  `retrieve(spec, image)` instead of three near-duplicate implementations. Per-firmware data now
  lives in JSON rather than mixed into the retrieval logic as Python literals - adding a new
  MicroPython release to `known_versions` or bumping a default tag is a plain data edit. No
  behavior change for CLI users beyond the CircuitPython fix above.

## [0.1.0b5] - 2026-07-31

### Added
- `demo/kaluma_run.py`, a generic USB-CDC REPL runner for firmware other than
  MicroPython/CircuitPython - talks to `USBCDC` directly rather than wrapping an `rp2040py.cli`
  subcommand (unlike the other `demo/*.py` scripts), demonstrating that the USB/CDC emulation
  itself isn't MicroPython-specific. Verified against [Kaluma](https://kaluma.io/) 1.2.1: boots,
  USB enumerates, and evaluates real JS at its REPL prompt.

### Fixed
- **Raw-REPL code uploads (`micropython <filename>`, `MicroPythonDevice.exec()`/`exec_file()`)
  silently hung forever on any source over ~512 bytes**, with zero output - a real, previously
  undiscovered bug, not a throughput/timeout issue. `RawReplRunner.feed()` used to push the entire
  source into the device's USB-CDC receive FIFO (`TX_FIFO_SIZE = 512` in `usb/cdc.py`) in one
  synchronous burst; that FIFO silently drops pushes once full instead of raising or blocking, so
  anything past ~512 bytes - including the terminating Ctrl-D - was lost, leaving the device
  waiting forever for an end-of-paste marker it had already been sent but never actually received.
  Confirmed against real firmware: a 440-byte script ran fine, an otherwise-identical 890-byte one
  hung indefinitely. `RawReplRunner` now paces uploads via a new `pump()` method that only ever
  sends what currently fits, retried until the whole payload's out; `MicroPythonDevice` schedules
  those retries through the simulated clock (`Clock.create_alarm()`), not a real
  `threading.Timer` - the latter's callback runs on its own OS thread, racing `USBCDC.tx_fifo`
  against whatever thread is driving the simulator (`pull()` happens deep in the emulated USB
  peripheral's own read path, mid-instruction-execution) and intermittently corrupting uploads
  (confirmed the hard way: a different `IndentationError`/`SyntaxError` almost every run, same
  input) - `FIFO`/`USBCDC` were never meant to be thread-safe, and adding locking there wasn't an
  option (it's a hot path used everywhere in peripheral emulation). An alarm callback instead runs
  synchronously inside `Clock.tick()`, on whichever thread already drives the simulator - same
  thread `feed()`/`pull()` run on, no race. Verified end to end against a real natmod build
  ([ballistics-lab/micropython-bclibc](https://github.com/ballistics-lab/micropython-bclibc)'s
  ~13KB `tests/test_bclibc.py`, previously hanging indefinitely under both CPython and PyPy) -
  passes cleanly and repeatably now.
- The same unbounded-burst pattern in `micropython`'s interactive-mode stdin forwarding (and
  `demo/kaluma_run.py`'s) could hit the same FIFO-overflow silent-drop for a single large paste
  into the terminal (`os.read()` can return up to 4096 bytes in one chunk - well over the 512-byte
  FIFO). Both now back off and retry while the FIFO's full, rather than assuming
  `send_serial_byte()` always has room. This path runs on its own dedicated stdin-reader thread,
  not the simulator's, so a plain blocking retry (no clock-alarm scheduling needed) is safe here.

## [0.1.0b4] - 2026-07-31

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

[Unreleased]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b4...HEAD
[0.1.0b4]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b3...v0.1.0b4
[0.1.0b3]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b2...v0.1.0b3
[0.1.0b2]: https://github.com/o-murphy/rp2040py/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/o-murphy/rp2040py/releases/tag/v0.1.0b1