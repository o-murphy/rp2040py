# Port checklist

Tracks the file-by-file port from [rp2040js](https://github.com/wokwi/rp2040js) (TypeScript) to
rp2040py (Python), ordered from fewest dependencies to most.

### utils / base types
- [x] `utils/bit.ts` → `utils/bit.py`
- [x] `utils/fifo.ts` → `utils/fifo.py`
- [x] `utils/logging.ts` → `utils/logging.py`
- [x] `utils/time.ts` → `utils/time.py`
- [x] `utils/timer32.ts` → `utils/timer32.py`
- [x] `utils/assembler.ts` → `utils/assembler.py`
- [x] `utils/pio-assembler.ts` → `utils/pio_assembler.py`
- [x] `irq.ts` → `irq.py`
- [x] `interpolator.ts` → `interpolator.py`
- [x] `gdb/gdb-utils.ts` → `gdb/gdb_utils.py`

### clock
- [x] `clock/clock.ts` → `clock/clock.py`
- [x] `clock/mock-clock.ts` → `clock/mock_clock.py`
- [x] `clock/simulation-clock.ts` → `clock/simulation_clock.py`

### peripherals — base
- [x] `peripherals/peripheral.ts` → `peripherals/peripheral.py`

### peripherals — simple (only depend on `peripheral.py`)
- [x] `peripherals/sysinfo.ts` → `peripherals/sysinfo.py`
- [x] `peripherals/tbman.ts` → `peripherals/tbman.py`
- [x] `peripherals/syscfg.ts` → `peripherals/syscfg.py`
- [x] `peripherals/psm.ts` → `peripherals/psm.py`
- [x] `peripherals/reset.ts` → `peripherals/reset.py`
- [x] `peripherals/ssi.ts` → `peripherals/ssi.py`
- [x] `peripherals/xosc.ts` → `peripherals/xosc.py`
- [x] `peripherals/rtc.ts` → `peripherals/rtc.py`

### peripherals — need `RP2040` / `GPIOPin` (forward-ref)
- [x] `peripherals/pads.ts` → `peripherals/pads.py`
- [x] `peripherals/busctrl.ts` → `peripherals/busctrl.py`
- [x] `peripherals/io.ts` → `peripherals/io.py`
- [x] `peripherals/ppb.ts` → `peripherals/ppb.py`
- [x] `peripherals/clocks.ts` → `peripherals/clocks.py`
- [x] `peripherals/watchdog.ts` → `peripherals/watchdog.py`
- [x] `peripherals/timer.ts` → `peripherals/timer.py`

### peripherals — need `dma.py` (DREQChannel)
- [x] `peripherals/dma.ts` → `peripherals/dma.py`
- [x] `peripherals/spi.ts` → `peripherals/spi.py`
- [x] `peripherals/uart.ts` → `peripherals/uart.py`
- [x] `peripherals/adc.ts` → `peripherals/adc.py`
- [x] `peripherals/pwm.ts` → `peripherals/pwm.py`
- [x] `peripherals/i2c.ts` → `peripherals/i2c.py`
- [x] `peripherals/pio.ts` → `peripherals/pio.py`
- [x] `peripherals/usb.ts` → `peripherals/usb.py`

### core
- [x] `gpio-pin.ts` → `gpio_pin.py`
- [x] `sio.ts` → `sio.py`
- [x] `cortex-m0-core.ts` → `cortex_m0_core.py`
- [x] `rp2040.ts` → `rp2040.py`
- [x] `simulator.ts` → `simulator.py`
- [x] `index.ts` → `__init__.py`

### gdb
- [x] `gdb/gdb-utils.ts` → `gdb/gdb_utils.py`
- [x] `gdb/gdb-target.ts` → `gdb/gdb_target.py`
- [x] `gdb/gdb-server.ts` → `gdb/gdb_server.py`
- [x] `gdb/gdb-connection.ts` → `gdb/gdb_connection.py`
- [x] `gdb/gdb-tcp-server.ts` → `gdb/gdb_tcp_server.py` (Node `net` → Python `socket`+`threading`)

### usb
- [x] `usb/interfaces.ts` → `usb/interfaces.py`
- [x] `usb/setup.ts` → `usb/setup.py`
- [x] `usb/usb-device.ts` → `usb/usb_device.py`
- [x] `usb/cdc.ts` → `usb/cdc.py`

### tests (`*.spec.ts` → `tests/test_*.py`, pytest)
- [x] `test-utils/*.ts` → `tests/utils/*.py` (shared driver infra for instructions/sio/pio tests; the GDB-driver variant was not ported)
- [x] `utils/fifo.spec.ts` → `tests/test_fifo.py`
- [x] `utils/time.spec.ts` → `tests/test_time.py`
- [x] `utils/assembler.spec.ts` → `tests/test_assembler.py`
- [x] `utils/pio-assembler.spec.ts` → `tests/test_pio_assembler.py`
- [x] `peripherals/timer.spec.ts` → `tests/test_timer.py`
- [x] `peripherals/dma.spec.ts` → `tests/test_dma.py`
- [x] `peripherals/uart.spec.ts` → `tests/test_uart.py`
- [x] `peripherals/pio.spec.ts` → `tests/test_pio.py` (35/35 passing)
- [x] `usb/cdc.spec.ts` → `tests/test_cdc.py`
- [x] `instructions.spec.ts` → `tests/test_instructions.py` (126/126 passing)
- [x] `rp2040.spec.ts` → `tests/test_rp2040.py`
- [x] `sio.spec.ts` → `tests/test_sio.py`

### demo / debug (needed to actually run firmware, e.g. MicroPython)
- [x] `demo/bootrom.ts` → `src/rp2040py/device/bootrom.py` (RP2040 bootrom binary, data only; verified: real bootrom executes thousands of instructions correctly)
- [x] `demo/intelhex.ts` → `src/rp2040py/cli/intelhex.py`
- [x] `demo/load-flash.ts` → `src/rp2040py/device/load_flash.py` (UF2 decoder implemented directly, no external `uf2` package - keeps zero runtime deps)
- [x] `demo/emulator-run.ts` → `demo/emulator_run.py` (generic hex/uf2 runner + GDB server; thin wrapper around `rp2040py.cli`'s `run` subcommand - see "CLI packaging" below)
- [x] `demo/micropython-run.ts` → `demo/micropython_run.py` (MicroPython/CircuitPython UF2 runner + USB CDC console; thin wrapper around `rp2040py.cli`'s `micropython` subcommand)
- [ ] `debug/gdbdiff.ts` → `debug/gdbdiff.py` (deferred - needs real-hardware GDB client (`test-utils/gdbclient.ts`), out of scope for running firmware in the emulator)

### MicroPython CI test fixtures (`test/` in rp2040js)
- [x] `test/micropython/main.py` → `tests/micropython/main.py` (copied verbatim - already Python, runs *inside* the emulated device)
- [x] `test/micropython/main-spi.py` → `tests/micropython/main-spi.py` (copied verbatim, same reason)
- [x] `test/mklittlefs.py` → `src/rp2040py/cli/mklittlefs.py`, exposed as the `mklittlefs` subcommand (needs `littlefs-python`, an optional `fs` extra rather than a hard runtime dependency - see "CLI packaging" below)
- [x] `test/micropython-spi-test.ts` → `tests/micropython_spi_run.py`

### CI (`.github/workflows/`)
- [x] `ci-test.yml` → covered by the existing `pre-commit.yml` (mypy + ruff + pytest, equivalent lint/test gate)
- [x] `ci-micropython.yml` → `ci-micropython.yml` (uv-based)
- [x] `ci-pico-sdk.yml` → `ci-pico-sdk.yml` (uv-based)

## Backlog

Deferred, not blocked on anything technical - just not done yet.

- [ ] `debug/gdbdiff.ts` → `debug/gdbdiff.py` - previously marked out of scope (needed a real Pico
      and its GDB client to diff emulator behavior against), no longer the case now that real
      hardware is available for comparison; `test-utils/gdbclient.ts` needs porting alongside it.

## Known differences from rp2040js

Places where the Python port's runtime behavior necessarily diverges from the JS original,
beyond straightforward syntax translation.

### CLI packaging (no rp2040js equivalent)

rp2040js's `demo/*.ts` scripts are only ever run from a checkout (`npm run start`, `tsx
demo/emulator-run.ts`, etc.) - there's no npm-packaged CLI, since rp2040js is primarily consumed
as a library (e.g. embedded in Wokwi). rp2040py adds one: `src/rp2040py/cli/` is a real subpackage
(`intelhex.py`, `mklittlefs.py` - moved there from `tests/`, plus the argparse dispatch in
`cli/__init__.py`) that ships in the wheel, exposed as the `rp2040py` console script
(`[project.scripts]` in `pyproject.toml`) and via `python -m rp2040py` (`src/rp2040py/__main__.py`
is a two-line shim). `mklittlefs` is the one subcommand with a dependency (`littlefs-python`), so
it's gated behind the optional `fs` extra (`[project.optional-dependencies]`) rather than pulled
into the zero-runtime-dependency default install; the `dev` dependency group depends on
`rp2040py[fs]` so it's still there for `uv sync` in CI and local dev. `demo/emulator_run.py`,
`demo/micropython_run.py`, and `demo/benchmark.py` are now thin wrappers around the same
`rp2040py.cli` subcommands (`run`, `micropython`, `bench`), kept so the documented `uv run python
demo/*.py` commands keep working unchanged for anyone working from a checkout rather than a
pip/uv install.

`cli/mp_retrieve.py` (also no rp2040js equivalent) resolves the `micropython --image`/`--circuitpython`
argument: a known version tag (`MICROPYTHON_KNOWN_FW_VERSIONS`/`CIRCUITPYTHON_DEFAULT_TAG`) or an
existing local path, downloading the matching UF2 from micropython.org/Adafruit's S3 bucket into the
current directory on first use and reusing it thereafter. This replaces the previous "download it
yourself and drop it next to the CLI" instructions in the README; `ci-micropython.yml`'s separate
`curl` download step was removed accordingly, since `--image <tag>` now does the same job on demand.

`bootrom.py`'s `BOOTROM_B1` (a ~4,100-element constant list) is imported lazily inside the functions
that need it (`_cmd_run`, `MicroPythonDevice.__init__`, etc.) rather than at module import time, so
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

`start()`/`exec()`/`exec_file()` block the calling thread; each has an `_async` twin
(`start_async()`/`exec_async()`/`exec_file_async()`) returning a `concurrent.futures.Future`, plus
`astart()`/`aexec()`/`aexec_file()` for asyncio. All of these submit the same plain blocking
implementation (`threading.Event.wait()`) to one `ThreadPoolExecutor(max_workers=1)` per device -
deliberately reusing the standard library's own executor/Future machinery rather than hand-rolling
a queue: the device only has one REPL channel and can't run two `exec()`s at once, so a
single-worker executor gets FIFO queueing of overlapping calls, `Future.add_done_callback()` for
callback style, and cancellation of not-yet-started calls, all for free. (An earlier version of
this hand-rolled the queue with a `deque` + a `Future`-per-call + a `threading.Timer` timeout
watchdog; it worked, but was substantially more code for the same guarantees the stdlib already
provides.) `concurrent.futures.TimeoutError` and `asyncio.TimeoutError` are each their own class,
distinct from the builtin `TimeoutError`, until Python 3.11 - `_result()`/`_await()` in
`mp_device.py` normalize all three to the builtin one so `except TimeoutError` behaves the same
everywhere on the 3.10 floor this project supports.

### Threading model (`Simulator.execute()` / `RPPIO.run()`)

Upstream JS yields back to Node's single-threaded event loop every N steps via
`setTimeout(() => this.execute(), 0)`, so an external `stop()` call (or the process exiting)
can interleave between bursts. Python has no equivalent single-threaded event loop, so this was
ported using `threading.Timer(0, self.execute)` instead - the closest analogue, but it introduces
**real concurrency** the JS version never had: every burst after the first runs on a new,
non-main thread.

Two concrete consequences, both already handled in the demo scripts but worth knowing if you
write new ones against `Simulator`:

- **Use `os._exit(code)`, not `sys.exit(code)`, to stop the process from inside a simulation
  callback** (GPIO listeners, `USBCDC.on_serial_data`, etc.). Those callbacks run on a
  `Simulator` worker thread once the first 1,000,000-step burst has completed, and
  `sys.exit()`/`SystemExit` only unwinds the thread that raised it - it will not terminate the
  process the way Node's `process.exit()` does. See `demo/micropython_run.py` and
  `tests/micropython_spi_run.py` for the pattern.
- **Wait on the main thread after calling `simulator.execute()`**, e.g.
  `while simulator.executing: time.sleep(0.1)`. `execute()` only runs the first burst
  synchronously and then returns, rescheduling itself via a non-daemon `threading.Timer` so the
  process stays alive while the simulation runs (matching Node keeping the event loop alive). If
  `main()` returns without waiting, Python proceeds straight into interpreter shutdown, which
  blocks joining that non-daemon timer thread - and a Ctrl+C at that point produces an ugly
  `Exception ignored in: <module 'threading'>` traceback instead of a clean exit. All three demo
  entry points (`demo/emulator_run.py`, `demo/micropython_run.py`,
  `tests/micropython_spi_run.py`) do this wait-then-`os._exit(130)`-on-`KeyboardInterrupt` dance.

### `pio_assembler.py`'s `pio_jmp`/`pio_mov` argument order differs from upstream

`utils/pio_assembler.py`'s `pio_jmp(address, cond=0, delay=0)` and
`pio_mov(dest, src, op=0, delay=0)` take their arguments in a different order than upstream's
`pioJMP(cond = 0, address, delay = 0)` and `pioMOV(dest, op = 0, src, delay = 0)`. This isn't a
stylistic choice - it's forced by a language difference. TS/JS allows a defaulted parameter before
a required one (the default only kicks in when the argument is literally `undefined`), so upstream
puts `cond`/`op` first. Python's `def` syntax rejects that outright - a non-default parameter can't
follow a default one - so the required `address`/`src` had to move first, pushing `cond`/`op` after
it. Both encode identically; this is purely a call-site ordering gotcha, not a behavior difference.
But it means TS call sites can't be transliterated positionally - every call in `tests/test_pio.py`
(ported from `pio.spec.ts`) was individually verified against the emulator rather than translated
by argument position. Trips up anyone porting more `pioJMP`/`pioMOV` call sites from upstream in
the future.

### littlefs image format vs. old MicroPython (not actually a port bug)

`ci-micropython.yml` builds a `littlefs.img` via `tests/mklittlefs.py` (now the `mklittlefs`
subcommand, `src/rp2040py/cli/mklittlefs.py`) and expects MicroPython to
auto-run `main.py` from it. This worked for MicroPython 1.28 but hung indefinitely - CPU spinning
forever re-acquiring an SIO hardware spinlock with interrupts disabled - for every older version
(<=1.21) in the CI matrix.

Root cause, confirmed by bisecting against the JS original itself (running the real
`wokwi/rp2040js` checkout locally against the same firmware/image reproduced the identical hang,
including on the exact commit whose CI run shows green - ruling out a port-specific bug entirely):
`tests/mklittlefs.py` (as it was at the time) depended on `littlefs-python>=0.4.0` with no upper bound, and newer releases
of that package default to a newer littlefs on-disk format (v2.1) than the one MicroPython <=1.21's
bundled littlefs C implementation understands (v2.0). Confirmed byte-for-byte: `LittleFS(...,
disk_version=0x00020000)` under `littlefs-python==0.18.0` produces an image identical to
`littlefs-python==0.4.0`'s default output (which upstream rp2040js's `test/requirements.txt` pins
exactly, sidestepping the issue there). MicroPython 1.28's newer littlefs implementation reads
*both* formats fine, so pinning the on-disk *format* - not the `littlefs-python` package version -
is a strictly better fix: the `mklittlefs` subcommand and the README's filesystem-image snippet now both
pass `disk_version=0x00020000` explicitly, keeping `littlefs-python` itself unpinned (avoids that
package's own baggage - 0.4.0 imports the deprecated `pkg_resources` API, which newer `setuptools`
no longer bundles by default).

`mklittlefs` now exposes this as a `--disk-version {2.0,2.1}` flag (`LITTLEFS_DISK_VERSIONS` /
`build_littlefs_image(..., disk_version=...)` in `mklittlefs.py`) rather than hardcoding `2.0`
unconditionally, still defaulting to `2.0` for the reasons above. The `fs` extra's floor was also
raised to `littlefs-python>=0.18.0` (from `>=0.4.0`) - the version the byte-for-byte comparison
above was actually run against - while still leaving the upper bound open.

### Performance: pure-Python interpretation is much slower than V8

`CortexM0Core.execute_instruction()` is a large `if`/`elif` chain re-evaluated for every emulated
instruction - straightforward to port faithfully, but CPython interprets it roughly two orders of
magnitude slower than V8 JIT-compiles the equivalent JS. This is a throughput limitation, not a
correctness bug - every interpreter below reaches the same correct "Hello, MicroPython!" REPL
output, just at very different speeds.

`demo/benchmark.py` is a reproducible benchmark for this (see its docstring for usage): a
synthetic mode that isolates raw instruction-dispatch overhead (no bus/peripheral traffic beyond
RAM fetches), and a firmware mode that boots a real image to a REPL/`--expect-text` match, the
same workload `ci-micropython.yml` and `ci-pico-sdk.yml` exercise. Measured on this machine:

| Interpreter | Synthetic (instructions/sec) | MicroPython 1.28 + littlefs boot |
|---|---|---|
| CPython 3.10 | 426,854 | 221.11s (293,976 steps/sec) |
| CPython 3.14 + `PYTHON_JIT=1` | 779,343 (~1.8x) | 121.74s (~1.8x, 533,917 steps/sec) |
| PyPy 3.10 | 36,320,045 (~85x) | 9.59s (~23x, 6,778,518 steps/sec) |

("Steps/sec" counts `WFI`/`WFE` clock-fast-forward iterations alongside real instructions, so
it's not directly comparable to the synthetic column's pure instructions/sec - the *ratio between
interpreters* is what's meaningful here, not the absolute numbers.) PyPy's JIT is decisively the
biggest lever; CPython 3.14's still-experimental JIT is a smaller but real, zero-code-change win.

**MicroPython 1.21 is the recommended version to boot in the emulator, not 1.28**: the boot time
above is dominated by how much work the firmware itself does before dropping to the REPL, not just
interpreter speed. On the same machine, under the same CPython 3.10, MicroPython 1.21 reaches the
REPL in 6.85s (2,000,000 steps) versus 1.28's 160.35s (65,000,000 steps) - over 20x fewer steps for
an otherwise identical boot-to-prompt workload. 1.28 still boots and mounts a `mklittlefs`-built
littlefs image correctly (that's exactly the version pinned `disk_version` fixed compatibility
for, see below), it's simply much slower to reach interactively; use it only when you specifically
need whatever changed between 1.21 and 1.28.

Two mitigations, worth combining:

- **Run CPU-bound demo/CI workloads under PyPy** (`uv run --python pypy3.10 --no-dev -- python
  demo/micropython_run.py ...`) instead of CPython. PyPy's JIT gave a ~15x instructions/sec
  speedup in local benchmarking (once warmed up) and comfortably completes the same MicroPython +
  littlefs boot in well under a minute. Note `--no-dev` (or a separate PyPy-only sync): the `dev`
  dependency group's `mypy` pulls in `ast-serialize`, whose PyO3 build currently requires PyPy
  ≥3.11, so `uv sync`-ing the full dev group under PyPy 3.10 fails - this only matters for
  mypy/ruff/pytest tooling, not for running the emulator itself, which has zero runtime
  dependencies. `ci-micropython.yml` and `ci-pico-sdk.yml` run the firmware-boot steps against a
  `python_runtime` matrix - `pypy-3.10`, `cpython-3.10`, and `cpython-3.14` (with `PYTHON_JIT=1`)
  - each with a 10-minute timeout, so a regression specific to any one interpreter can't slip
  through even though PyPy is the realistic day-to-day way to run this.
- If profiling ever calls for it, `RP2040.read_uint32`/`write_uint32` and
  `CortexM0Core.execute_instruction()` are the hot path (per `cProfile` on a real boot): the
  bootrom-bounds check used to call `len()` on a `Uint32Array` (a Python-level `__len__`) on every
  single bus access regardless of target address - now cached once as `RP2040.bootrom_byte_size`.
  `CortexM0Core.pc` also used to be a `property` indirecting through `Uint32Array.__getitem__`/
  `__setitem__`; the hot path inside `execute_instruction()` now indexes
  `self.registers[PC_REGISTER]` directly (the `pc` property itself is unchanged and still used by
  external callers like the demo scripts and GDB target). `CortexM0Core` also used to expose its
  own `read_uint32`/`read_uint16`/`read_uint8`/`write_uint32`/`write_uint16`/`write_uint8` methods
  that did nothing but forward to the identically-named `RP2040` methods - pure indirection with
  no external callers (nothing outside the class used `core.read_uint32(...)` etc.), so those were
  removed and all internal call sites now call `self.rp2040.read_uint32(...)` etc. directly.
  `utils/bit.py`'s `read_uint16_le`/`write_uint16_le`/`read_uint32_le`/`write_uint32_le` used to
  slice out a temporary `bytes` object and call `int.from_bytes()`/`int.to_bytes()` on it; they now
  use module-level pre-built `struct.Struct("<H")`/`struct.Struct("<I")` instances'
  `unpack_from`/`pack_into` instead, which read/write directly against the buffer with no
  intermediate allocation - measured ~40% faster for these four functions in isolation, and (more
  strikingly) cut the real MicroPython + littlefs boot time roughly in half under PyPy specifically
  (15.87s -> 9.55s), since PyPy's JIT couldn't optimize away the old temporary-`bytes`-object
  allocation the way it can lean on the already-C-implemented `struct` module. Together with the
  two items above, these gave roughly a 20-25% instructions/sec improvement under CPython in local
  benchmarking.
- **`execute_instruction()` now dispatches via a precomputed table instead of a linear `if`/`elif`
  scan.** Each of the ~90 instruction patterns became its own `_op_*` method; a module-level
  `_DISPATCH_TABLE` (65536 entries, built once at import time from `_DISPATCH_PATTERNS`, in
  original priority order) maps `opcode -> handler` directly for O(1) lookup. Seven patterns
  (`BL`, `DMB`, `DSB`, `ISB`, `MRS`, `MSR`, `UDF` encoding T2) need `opcode2` as well as `opcode`
  to decode correctly, which a flat `opcode`-keyed table alone can't express; their opcode-only
  prefixes all happen to fall inside `0xF000`-`0xF7FF` with zero overlap from any other
  instruction (verified exhaustively, and enforced both by an assertion in
  `_build_dispatch_table()` and by `tests/test_dispatch_table.py`), so that narrow range is
  special-cased to a small hand-written `_resolve_wide()` resolver instead of the table, and the
  main table simply never gets populated there. This was the single biggest lever in the whole
  session: `execute_instruction()`'s own `cProfile` self-time dropped ~65% (the O(n) scan was
  replaced by a single array index), cumulative real-boot `cProfile` time dropped a further ~21%
  on top of the two items above, and the synthetic instructions/sec benchmark went from ~251,654
  to ~426,854 under CPython 3.10 (+70%). Generated mechanically (a one-off script split the
  original `if`/`elif` chain into methods verbatim, preserving every condition's exact source
  text as a lambda rather than hand-deriving mask/value bit patterns) and verified via the full
  126-case instruction test suite plus real MicroPython + littlefs boots on both old (1.16) and
  new (1.28) firmware before and after.
