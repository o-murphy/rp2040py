# 0001. CLI tool and public device API

- Status: Implemented
- Conceived: 2026-07-31 · #3
- Related: #3, #5, #10

<!-- migrated verbatim from docs/PORTING.md lines 132-213 -->

### CLI packaging (no rp2040js equivalent)

rp2040js's `demo/*.ts` scripts are only ever run from a checkout (`npm run start`, `tsx
demo/emulator-run.ts`, etc.) - there's no npm-packaged CLI, since rp2040js is primarily consumed
as a library (e.g. embedded in Wokwi). rp2040py adds one: `src/rp2040py/cli/` is a real subpackage
(`intelhex.py`, `mklittlefs.py` - moved there from `tests/`, plus the argparse dispatch in
`cli/__init__.py`) that ships in the wheel, exposed as the `rp2040py` console script
(`[project.scripts]` in `pyproject.toml`) and via `python -m rp2040py` (`src/rp2040py/__main__.py`
is a two-line shim). `mklittlefs` needs `littlefs-python`, which ships platform-specific compiled wheels - gated behind
the optional `fs` extra (`[project.optional-dependencies]`) rather than pulled into the default
install, so a plain `pip install rp2040py` never needs a wheel that might not exist for some
platform; the `dev` dependency group depends on `rp2040py[fs]` so it's still there for `uv sync` in
CI and local dev. `pyelftools` (`--bootrom` ELF parsing - see
[README](../README.md#bootrom-revisions)) is a normal, always-installed dependency instead - it's
a pure-Python wheel with no platform-specific build, so there's no packaging reason to keep it
optional the way `fs` is. `demo/emulator_run.py`,
`demo/micropython_run.py`, and `demo/benchmark.py` are now thin wrappers around the same
`rp2040py.cli` subcommands (`run`, `micropython`, `bench`), kept so the documented `uv run python
demo/*.py` commands keep working unchanged for anyone working from a checkout rather than a
pip/uv install.

`utils/firmware_retrieve.py` (also no rp2040js equivalent; originally split across
`cli/mp_retrieve.py` and `cli/kaluma_retrieve.py`, merged once the duplication between them - and a
real `v`-prefix bug in the CircuitPython path only one of them had - became annoying enough to fix
properly; moved from `cli/` to `utils/` on 2026-08-12 once `--board`
(docs/CYW43_WIFI_BACKLOG.md) needed it too - it's a generic tag/URL/path resolver with no argparse
involvement, not CLI-specific) resolves `micropython --image`/`--circuitpython`, `kaluma --image`,
and `--bootrom` (see [README](../README.md#bootrom-revisions)) on all four subcommands: a known
version tag, a direct `http(s)://` URL, or an existing local path, downloading the matching file
into `~/.cache/rp2040py` on first use and reusing it thereafter (falls back to the current
directory, today's original behavior, if the cache directory isn't writable for any reason). Each
firmware is a declarative `FirmwareSpec` (default tag, plus either a `boards: dict[board,
dict[tag, url]]` table - MicroPython/CircuitPython/Kaluma, which genuinely ship different builds
per board - or a flat `known_versions: dict[tag, url]` for board-agnostic firmware, i.e. just
BOOTROM) loaded from `utils/firmware_specs.json` - kept as plain JSON data rather than Python
literals, and fetched from each firmware's own real release source by `scripts/fetch_firmware.py`
at development time rather than generated from a filename/URL template at request time, so bumping
a default tag or adding a new release is a data refresh (re-run the script, commit the diff), not
a code change - plus one generic `retrieve(spec, image, board)` instead of three near-duplicate
implementations. This replaces the previous "download it yourself and drop it next to the CLI"
instructions in the README; `ci-micropython.yml`'s separate `curl` download step was removed
accordingly, since `--image <tag>` now does the same job on demand.

`bootrom.py`'s `BOOTROM_B1` (a ~4,100-element constant list) is imported lazily inside the functions
that need it (`_cmd_run`, `BaseDevice.__init__`, etc.) rather than at module import time, so
commands that don't boot a device - `--version`, `mklittlefs`, `--help` - aren't paying to parse it.

`bootrom.py`/`load_flash.py` (moved there from `demo/`) and `raw_repl.py` deliberately live under
`src/rp2040py/device/` instead of `cli/`, alongside `MicroPythonDevice` (`device/mp_device.py`,
re-exported from `device/__init__.py`) that is *not* CLI plumbing: it's a programmatic API for
booting a MicroPython/CircuitPython image and running code on it from another Python program
(`device.exec("print(1+1)")`, `device.exec_file(path)`) via the raw-REPL protocol (`raw_repl.py`;
the same Ctrl-A/Ctrl-D protocol `mpremote run`/`tools/pyboard.py` use over real serial).
`cli/__init__.py`'s `micropython -c/-m/<filename>` batch mode is itself just a caller of this API,
not a separate implementation. This split exists because `rp2040py.cli` (needing `device`) and
`rp2040py.device` (needing `bootrom`/`load_flash`) would otherwise form a circular import if the
latter stayed nested under `cli/` - `device/` has no dependency on `cli/` in either direction.
`tests/micropython_spi_run.py` (test-only SPI harness, not part of the general CLI) imports
`rp2040py.device.bootrom`/`load_flash` directly rather than duplicating them.

`device/base_device.py`'s `BaseDevice` (also no rp2040js equivalent) factors out the UF2-boot
lifecycle `MicroPythonDevice` and the newer `KalumaDevice` (`device/kaluma_device.py`) both need -
load the image, create the `USBCDC` console, block `start()`/`stop()` around actually running the
emulator - which used to be duplicated between `MicroPythonDevice.__init__` and
`demo/kaluma_run.py`'s hand-rolled boot sequence before the `kaluma` subcommand existed.
`MicroPythonDevice(BaseDevice)` layers the raw-REPL `exec()` family and its `Simulator`-engine-room
queueing on top; `KalumaDevice(BaseDevice)` stays thin - Kaluma has no raw-REPL-equivalent protocol (see
"CLI packaging" above), so there's no `exec()` to add, just optional `littlefs`/`program` loading.

`KalumaDevice(program=...)`/`load_kaluma_program()` (`device/load_flash.py`) stage a local `.js`
file into Kaluma's "user program" flash region (offset `0x100000`, 512K, `KALUMA_PROG_SECTOR_BASE=4`
in `board.h`) - the same region `kaluma flash <file>` writes to on real hardware via YMODEM, which
`km_runtime_load()` (`src/runtime.c`) auto-executes on every boot. Confirmed by reading
kaluma-project/kaluma's `src/prog.c` directly that the on-flash format needs no ELF/YMODEM framing
at all - just the raw source bytes plus a single `\0` terminator (`km_prog_end()`'s
`page_buffer_push(0)`), unlike the bootrom/UF2 formats elsewhere in this codebase that do need real
parsing. Getting this to actually auto-execute needed the GPIO pull-up/pull-down resolution fix
below - see there for the diagnosis (`km_running_script_check()` in `src/system.c` gates auto-run
on reading GP22 with a pull-up enabled; ruled out the littlefs-mount failure discussed above as an
alternate cause first - `run_board_module()` in `global.c` catches and prints that error without
aborting the boot sequence, so it was never actually blocking anything).

