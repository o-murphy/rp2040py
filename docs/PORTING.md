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
- [x] `gdb/gdb-tcp-server.ts` → `gdb/gdb_tcp_server.py` (Node `net` → Python `asyncio.start_server()`,
  its own dedicated engine-room thread - see "Threading model" below)

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
- [x] `demo/kaluma_run.py` (no rp2040js equivalent) - thin wrapper around `rp2040py.cli`'s `kaluma`
  subcommand, same as `demo/micropython_run.py` (originally talked to `USBCDC` directly before the
  `kaluma` subcommand existed - see "CLI packaging" below); verified against
  [Kaluma](https://kaluma.io/) 1.2.1
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
- [x] `ci-kaluma.yml` (no rp2040js equivalent) - boots real Kaluma firmware end to end, same shape
  as `ci-micropython.yml` but a boot-only smoke test (`--expect-text` against the unconditional
  startup banner) rather than an `mklittlefs`-staged code-execution test - see "CLI packaging"
  below for why Kaluma can't do the latter.

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

`cli/firmware_retrieve.py` (also no rp2040js equivalent; originally split across `cli/mp_retrieve.py`
and `cli/kaluma_retrieve.py`, merged once the duplication between them - and a real `v`-prefix bug
in the CircuitPython path only one of them had - became annoying enough to fix properly) resolves
`micropython --image`/`--circuitpython`, `kaluma --image`, and `--bootrom` (see
[README](../README.md#bootrom-revisions)) on all four subcommands: a known version tag, or an
existing local path, downloading the matching file into `~/.cache/rp2040py` on first use and
reusing it thereafter (falls back to the current directory, today's original behavior, if the
cache directory isn't writable for any reason). Each firmware is a declarative `FirmwareSpec`
(filename/URL templates, default tag,
optional known-version-tag table) loaded from `cli/firmware_specs.json` - kept as plain JSON
data rather than Python literals so bumping a default tag or adding a new MicroPython release is a
data edit, not a code change - plus one generic `retrieve(spec, image)` instead of three
near-duplicate implementations. This replaces the previous "download it yourself and drop it next
to the CLI" instructions in the README; `ci-micropython.yml`'s separate `curl` download step was
removed accordingly, since `--image <tag>` now does the same job on demand.

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

### GPIO pull-up/pull-down wasn't resolved into an actual bus reading for undriven pins

Found while debugging why `KalumaDevice(program=...)` never auto-executed even though the write to
flash was confirmably correct: `gpio_pin.py`'s `GPIOPin` (matching upstream rp2040js's `GPIOPin`
closely enough that this is very likely present there too - worth filing upstream) tracked
`pullup_enabled`/`pulldown_enabled` purely as pad-control-register metadata, never resolving them
into the actual bit `input_value`/`status` (and therefore firmware's `gpio_get()`) reads for a pin
nothing actively drives - `_raw_input_value` just stayed at its default `False` regardless of which
pull was configured, i.e. every undriven pin silently read low no matter what. Real hardware
resolves a floating, pulled-up pin to high.

This directly explained the Kaluma symptom: `km_running_script_check()` enables GP22's pull-up and
reads it to decide whether to skip auto-run (GP22 wired to GND is the documented "recovery mode"
signal) - an always-low reading is indistinguishable from "wired to GND," so auto-run silently
never ran, regardless of anything staged in the "user program" flash region.

Fixed by adding a `_driven` flag (set only by `set_input_value()`, the actual external-drive API -
button harnesses, other simulated peripherals) and a `_effective_raw_input_value` property that
falls back to resolving the enabled pull direction when the pin has never been driven, used in
`input_value`/`status` instead of the raw flag directly. `refresh_input()` (called whenever
`input_enable` toggles, e.g. from a normal `gpio_init()` config write - unrelated to anything
actually driving the pin) had to stop routing through `set_input_value()` for this to work - it
used to, and would otherwise have permanently marked every reconfigured pin as "driven" with its
stale default value the moment `input_enable` was toggled, defeating pull resolution immediately
for exactly the GP22 case this fixes. See `tests/test_gpio_pin.py`.

`start()`/`exec()`/`exec_file()` block the calling thread; each has an `_async` twin
(`start_async()`/`exec_async()`/`exec_file_async()`) returning a `concurrent.futures.Future`, plus
`astart()`/`aexec()`/`aexec_file()` for asyncio. All of these run as coroutines on the
`Simulator`'s own engine-room loop (`simulator.submit()`, `run_coroutine_threadsafe()` under the
hood - already returns a plain `concurrent.futures.Future`, no extra wrapping needed), serialized
by one `asyncio.Lock` per device: the device only has one REPL channel and can't run two `exec()`s
at once, so the lock gets FIFO queueing of overlapping calls the same way
`ThreadPoolExecutor(max_workers=1)` used to, for free. (Two earlier versions of this: first a
hand-rolled `deque` + a `Future`-per-call + a `threading.Timer` timeout watchdog; then a
`ThreadPoolExecutor(max_workers=1)` submitting plain blocking `threading.Event.wait()` calls -
replaced once `Simulator` got its own engine-room loop, see
`docs/ASYNCIO_MIGRATION_BACKLOG.md`'s phase 5: running on a worker thread neither of those first
two designs actually needed to exist raced `USBCDC.tx_fifo` against whatever thread was really
driving the simulator, the same class of bug PR 3 found and fixed for `cli/stdio_repl.py`.)
`concurrent.futures.TimeoutError` and `asyncio.TimeoutError` are each their own class, distinct
from the builtin `TimeoutError`, until Python 3.10 - `_result()`/`_await()` in `mp_device.py`
normalize all three to the builtin one so `except TimeoutError` behaves the same everywhere on the
3.10 floor this project supports.

### Threading model (`Simulator.execute()` / `RPPIO.run()`)

**Superseded - this section described the pre-`asyncio` port; kept below for historical context
(the reasoning explains *why* the current design looks the way it does), but none of the advice
here reflects current code.** See
[docs/ASYNCIO_MIGRATION_BACKLOG.md](ASYNCIO_MIGRATION_BACKLOG.md) for the full migration writeup.
Current shape, in short: `Simulator` owns one persistent background thread hosting a real
`asyncio` event loop (its "engine room"), created lazily on first use
(`Simulator._ensure_loop()`). `execute()` is `async def`, yielding via `await asyncio.sleep(0)`
between batches instead of rescheduling itself through a new OS thread every time
(`threading.Timer(0, self.execute)` is gone). Callers that used to call `simulator.execute()`
directly now call `simulator.start_execution()` (schedules `execute()` as a task on the engine
room and returns immediately) and `simulator.wait_for_shutdown()` to block until it's done - both
already used throughout `cli/__init__.py`; nothing outside this file needs the raw `threading`
patterns below anymore. Three bridge primitives make cross-thread calls into the engine room safe
without bespoke locking per caller: `Simulator.call(coro, timeout=None)` (blocking),
`Simulator.acall(coro)` (async, for a caller with its own running loop), `Simulator.submit(coro)`
(non-blocking, returns a `concurrent.futures.Future`) - `os._exit()`/raw `threading.Timer`
scheduling from inside a simulation callback (the old advice below) should no longer be necessary;
use `Simulator.shutdown_request.request(code)` and `simulator.clock.create_alarm(...)` instead, as
the old advice already recommended over the *other* raw-thread alternatives.

<details>
<summary>Original pre-<code>asyncio</code> analysis (historical)</summary>

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
  `tests/micropython_spi_run.py` for the pattern. **Prefer `Simulator.shutdown_request.request(code)`
  over a raw `os._exit()` where practical** (see `docs/BACKLOG.md`'s "Unified process-shutdown
  coordinator"): it flags the same cross-thread problem this bullet describes, but lets
  `Simulator.wait_for_shutdown()` - always running on the thread actually driving the simulator -
  do a real `sys.exit()` after running proper cleanup (terminal restore, `GDBTCPServer.close()`,
  etc.), instead of skipping straight past atexit/finally the way `os._exit()` does. `cli/__init__.py`
  uses this now; `demo/*.py`'s standalone scripts, listed below, still use the older
  `os._exit()`-direct pattern and haven't been migrated.
- **Wait on the main thread after calling `simulator.execute()`**, e.g.
  `while simulator.executing: time.sleep(0.1)` (or just call `simulator.wait_for_shutdown()`, which
  does exactly this). `execute()` only runs the first burst synchronously and then returns,
  rescheduling itself via a non-daemon `threading.Timer` so the process stays alive while the
  simulation runs (matching Node keeping the event loop alive). If `main()` returns without
  waiting, Python proceeds straight into interpreter shutdown, which blocks joining that non-daemon
  timer thread - and a Ctrl+C at that point produces an ugly `Exception ignored in: <module
  'threading'>` traceback instead of a clean exit. All four demo entry points
  (`demo/emulator_run.py`, `demo/micropython_run.py`, `demo/kaluma_run.py`,
  `tests/micropython_spi_run.py`) do this wait-then-`os._exit(130)`-on-`KeyboardInterrupt` dance.
- **Don't schedule follow-up work with `threading.Timer`/a real OS thread if it touches anything a
  `Simulator` worker thread also touches** (a FIFO, a peripheral register, `USBCDC.tx_fifo`, etc.)
  - use `simulator.clock.create_alarm(...)` instead, whose callback runs synchronously inside
    `Clock.tick()` on whichever thread is already driving the simulator. See the next section for
    a real bug this exact mistake caused.

Each `threading.Timer` handoff above is a real OS-thread creation, so how many of them a boot
needs matters, not just their existence. `execute()`'s inner loop bounds a batch to ~1,000,000
"units" before yielding via that handoff; an idle (`WFI`'d) core jumping straight to the next
clock alarm costs essentially nothing in real time no matter how far away that alarm is, so an
earlier version of this loop weighting that jump by the simulated nanoseconds it covered (instead
of counting it as ~1 unit, same as a real instruction) was a real bug, not just a style choice -
see `docs/BACKLOG.md`'s CDC performance investigation for the full writeup. USB SOF's 1ms recurring
alarm alone was enough to exhaust a whole batch after ~8 firings while idle, and a booted device is
idle almost all the time, so this turned "connected and waiting" into a thread handoff roughly
every 8ms of simulated idle time - the actual driver behind wildly variable `--expect-text`
wall-clock times, not anything USB-specific. Fixed by counting an idle jump as ~1 unit like
everything else, matching what `_bench_firmware`'s independent hand-rolled loop in
`cli/__init__.py` (which doesn't go through `Simulator.execute()`) already did.

That fix addresses *idle* runs specifically. A *busy* run (guest actively executing, not WFI'd -
e.g. MicroPython 1.28's resident-script loop) still pays for every handoff regardless: found while
investigating a report that `rp2040py.native` "shouldn't be 3x slower than expected" running real
guest code. A ~65M-step MicroPython 1.28 + littlefs boot needs ~65 `threading.Timer` handoffs at
the default 1,000,000-step batch size; a headless `rp2040py bench` run of the identical workload
(single tight loop, zero handoffs) finished in 24.95s against the CLI path's ~45s for the same
work - patching `execute()` to use one giant batch (no handoffs at all) brought the CLI path down
to 19.45s, actually beating the headless number. The yield-and-reschedule dance mirrors upstream
JS's `setTimeout(..., 0)`, necessary there because Node's event loop is single-threaded; CPython's
GIL already preemptively time-slices between real OS threads, so a tight loop on a background
thread doesn't starve the main thread the way single-threaded JS would - this port carried the
pattern over without needing it, and each handoff's cost (new thread creation, GIL contention
against the main thread's own periodic poll) was always there, just dwarfed by how slow pure-Python
instruction dispatch was until `rp2040py.native` made dispatch itself ~4x+ faster. See
`docs/BACKLOG.md`'s CDC investigation follow-up for the full numbers.

**This section used to end here with "not yet fixed."** It's fixed now, via the full `asyncio`
migration linked at the top of this section - `execute()`'s `threading.Timer` reschedule is gone,
replaced by the persistent-engine-room-thread design that section describes. That in turn
surfaced a *different* real-time cost specific to idle batches under the new model - see
`docs/BACKLOG.md`'s CDC section and CHANGELOG.md's `[Unreleased]` entry for that follow-up.

</details>

### Raw-REPL uploads and cross-thread `USBCDC.tx_fifo` access (a real, previously undiscovered bug)

`device/raw_repl.py`'s `RawReplRunner.feed()` originally pushed an entire raw-REPL upload (the
whole `source` argument to `MicroPythonDevice.exec()`/`exec_file()`, or the CLI's
`micropython <filename>`) into `send_byte` - ultimately `USBCDC.send_serial_byte()` - in one
synchronous burst, the instant the raw-REPL banner arrived. `send_serial_byte()` just pushes into
`tx_fifo`, a fixed-size `FIFO` (`TX_FIFO_SIZE = 512` in `usb/cdc.py`) that silently drops anything
pushed once full (`FIFO.push()` in `utils/fifo.py` just no-ops past capacity - no exception, no
blocking). Any upload over ~512 bytes therefore lost everything past that point, *including the
terminating Ctrl-D* - the device was left waiting forever for an end-of-paste marker it had
already been "sent" but never actually received. Confirmed against real firmware, not assumed: a
440-byte script ran fine; an otherwise-identical 890-byte one hung indefinitely with zero output.
Real-world impact was large - `tests/test_bclibc.py` in
[ballistics-lab/micropython-bclibc](https://github.com/ballistics-lab/micropython-bclibc), a
perfectly ordinary ~13KB test file, silently hung forever under both CPython and PyPy.

`feed()` can't drain the FIFO itself mid-burst to make room: it's invoked synchronously from
*inside* the emulated CPU's own `execute_instruction()` call chain (the device writing to its USB
TX register), so nothing else runs - and no bytes actually get pulled from `tx_fifo` - until it
returns. Pacing has to happen *across* separate calls instead. `RawReplRunner` gained a `pump()`
method that sends only as much as an optional `free_space()` callback currently allows, returning
whether everything's out yet; call it again while it isn't.

The first fix attempt scheduled those repeat `pump()` calls with `threading.Timer` from
`MicroPythonDevice`'s `_exec_blocking()`. That "worked" in the sense that uploads no longer hung -
but intermittently corrupted them instead: a different `IndentationError`, then a `SyntaxError` on
an otherwise-untouched line, on repeated runs of the identical input. Root cause: a real `Timer`
fires its callback on its own OS thread, which raced `tx_fifo.push()` (from `pump()`, called via
the timer) against `tx_fifo.pull()` (from the emulated USB peripheral's own register-read path,
invoked deep inside `execute_instruction()` on whichever thread is driving the simulator) - `FIFO`
was never written to be thread-safe, and it's a hot enough path (used by every peripheral's FIFO
registers, not just CDC) that adding locking there for this one caller wasn't acceptable. The fix
that actually stuck: schedule `pump()` retries via `simulator.clock.create_alarm(...)` instead of
`threading.Timer`. An alarm's callback runs synchronously inside `Clock.tick()`, on whatever thread
already drives the simulator - the same thread `feed()`/`pull()` run on - so there's no second
thread to race in the first place. `MicroPythonDevice._exec_blocking()` now takes the device's
`Simulator.clock` for exactly this. Re-verified against the same real `test_bclibc.py` build,
repeatably clean.

The same unbounded-burst pattern existed in `micropython`'s interactive-mode stdin-forwarding loop
(`cli/stdio_repl.py`) and `demo/kaluma_run.py`'s: `os.read()` can return up to 4096 bytes in one
chunk from a single large paste into the terminal, comfortably over the 512-byte FIFO. **Updated
for the `asyncio` migration**: `StdioInteractiveRepl`'s `add_reader()` callback now runs directly
on `Simulator`'s own engine-room loop (not a separate stdin-reader thread - that design predates
the migration), so a plain blocking retry-with-sleep would stall the same loop `execute()` needs to
keep advancing. It uses the same `simulator.clock.create_alarm(...)`-based pacing this section's
`RawReplRunner` fix above already established (`_queue()`/`pump()`, re-armed via a clock alarm
instead of a blocking sleep) rather than reinventing a third mechanism.

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

### `mklittlefs` used to silently corrupt images when reusing an output path with different block params

`build_littlefs_image()` originally "updated in place" when `--output` already existed: read the
existing file's bytes into `UserContext`, then mounted it with *this run's* `block_size`/
`block_count`. When those didn't match whatever built the file previously (e.g. rebuilding
`littlefs.img` first at MicroPython's default block count, then again at Kaluma's), littlefs-python
either silently ignored the new values (image stayed the old size) or reformatted and dropped
existing files - both with no error or warning, reproducible even against a validly-built image,
not just a stale/foreign one. Fixed by always building fresh from an empty buffer and requiring
`-f`/`--force` to overwrite an existing path (raising `ValueError` otherwise) - see the CHANGELOG's
`mklittlefs -f`/`--force` entry.

### `mklittlefs` crashes at exit under PyPy (littlefs-python, not a port bug)

`rp2040py mklittlefs` writes the image correctly and prints its success message, then aborts
(SIGABRT: `littlefs/lfs.c:6200: lfs_file_sync: Assertion `lfs_mlist_isopen(lfs->mlist, (struct
lfs_mlist*)file)' failed`) - but *only* when the whole process is running under PyPy (e.g. `uv tool
install --python pypy-3.10 rp2040py[fs]`, which is exactly what
`.github/actions/setup-rp2040py`'s composite action does for the emulator speedup). Never
reproduces under CPython.

Root cause, isolated by instrumenting each step with `flush=True` prints: every operation
(`LittleFS(...)`, `lfs.open(...)`/write/close, an explicit `lfs.unmount()`, even a manual
`gc.collect()` immediately after closing the file) completes and prints successfully - the abort
only happens later, during interpreter shutdown itself, after the process has nothing left to do.
This points at `littlefs-python`'s Cython `__dealloc__` finalizers for the `LittleFS`/file C
objects running in an order CPython's deterministic refcounting happens to always get right, but
that PyPy's non-refcounting GC doesn't guarantee - a finalizer apparently re-closes a file object
whose underlying `lfs_mlist` entry was already removed when it was correctly closed the first time
(via its own `with` block). Neither an explicit `lfs.unmount()` nor a manual `gc.collect()` forces
that finalization to happen in-order early enough to dodge it - this is a real upstream
`littlefs-python`/PyPy interaction, not something fixable from call-site code alone.

Workaround: `_cmd_mklittlefs` in `cli/__init__.py` calls `os._exit(0)` right after printing success
- but only when `sys.implementation.name == "pypy"`, never unconditionally. The image on disk is
already complete and correct by that point, so skipping the rest of interpreter shutdown is safe
for *this* process's exit - but doing it unconditionally would also kill the caller's process if
`_cmd_mklittlefs`/`main()` is ever invoked in-process rather than as the real entry point (e.g. from
a test suite, confirmed the hard way while first writing this fix). This only papers over the CLI's
own one-shot process exit; a long-running PyPy program that calls the public
`build_littlefs_image()` as a library and keeps running afterwards can still hit the same crash
later, unpredictably, whenever PyPy's GC happens to finalize the stale objects - there's no
workaround for that case from rp2040py's side.

### Performance: pure-Python interpretation is much slower than V8

`CortexM0Core.execute_instruction()` is a large `if`/`elif` chain re-evaluated for every emulated
instruction - straightforward to port faithfully, but CPython interprets it roughly two orders of
magnitude slower than V8 JIT-compiles the equivalent JS. This is a throughput limitation, not a
correctness bug - every interpreter below reaches the same correct "Hello, MicroPython!" REPL
output, just at very different speeds.

`demo/benchmark.py` is a reproducible benchmark for this (see its docstring for usage): a
synthetic mode that isolates raw instruction-dispatch overhead (no bus/peripheral traffic beyond
RAM fetches), and a firmware mode that boots a real image and runs a script to a
REPL/`--expect-text` match, the same workload `ci-micropython.yml` and `ci-pico-sdk.yml` exercise.
Measured on this machine:

| Interpreter                               | Synthetic (instructions/sec) | MicroPython 1.28 + littlefs, running a typical script |
| ----------------------------------------- | ---------------------------- | ----------------------------------------------------- |
| CPython 3.10                              | 499,806                      | 188.98s (342,244 steps/sec)                           |
| CPython 3.10 + `rp2040py.native` (Cython) | 2,049,726 (~4.1x)            | 46.65s (~4.1x, 1,386,605 steps/sec)                   |
| CPython 3.14 + `PYTHON_JIT=1`             | 961,218 (~1.9x)              | 113.77s (~1.7x, 568,486 steps/sec)                    |
| PyPy 3.10                                 | 37,989,746 (~76x)            | 11.59s (~16x, 5,580,638 steps/sec)                    |

("Steps/sec" counts `WFI`/`WFE` clock-fast-forward iterations alongside real instructions, so
it's not directly comparable to the synthetic column's pure instructions/sec - the *ratio between
interpreters* is what's meaningful here, not the absolute numbers.) PyPy's JIT is decisively the
biggest lever; CPython 3.14's still-experimental JIT is a smaller but real, zero-code-change win.
`rp2040py.native` (see "Cython port of the interpreter core" below and `docs/BACKLOG.md`) is on by
default whenever a C compiler is available, so the plain "CPython 3.10" row above is actually the
*worse* case (no compiler, or `RP2040PY_SKIP_CYTHON=1`) - most real installs land on the native row
without doing anything differently. It doesn't touch PyPy at all (compilation is skipped there on
purpose - see below), so PyPy remains the fastest option for CPU-bound runs regardless.
The "MicroPython 1.28 + littlefs" column specifically times booting, mounting a littlefs image,
and running a resident `while True: print(...); time.sleep(1)` script (`tests/micropython/main.py`,
what `ci-micropython.yml` actually boots) to its first line of output - reaching the bare REPL
prompt itself, with no such script auto-running, is fast on every version tested (well under a
second) and isn't what this table measures.

**MicroPython 1.21 is the recommended version to boot in the emulator, not 1.28**: running that
same script is dominated by how much work the firmware itself does per loop iteration, not just
interpreter speed. On the same machine, under the same CPython 3.10, MicroPython 1.21 reaches that
script's first `print()` in 3.72s (1,418,835 steps) versus 1.28's 188.98s (64,679,599 steps) - about
45x fewer steps for byte-identical script content, with the instruction count reproducing exactly
run-to-run (deterministic - a property of the firmware's own control flow, not host-speed
variance). Profiling shows the CPU core essentially never reaches `WFI`/idle during 1.28's run
(waiting is near-zero even over tens of millions of steps), so this is real Thumb code being
interpreted somewhere in 1.28's own compiled firmware, not an emulator hang - the exact upstream
cause (compiler, GC, string formatting, or something else specific to what changed in 1.28's
firmware between it and 1.21) hasn't been isolated further, since it lives in MicroPython's own
compiled code rather than anything in this repo. 1.28 still boots and mounts a `mklittlefs`-built
littlefs image correctly (that's exactly the version pinned `disk_version` fixed compatibility
for, see below), it's simply much more expensive to run typical resident scripts on; use it only
when you specifically need whatever changed between 1.21 and 1.28.

Two mitigations, worth combining:

- **Run CPU-bound demo/CI workloads under PyPy** (`uv run --python pypy3.10 --no-dev -- python
  demo/micropython_run.py ...`) instead of CPython. PyPy's JIT gave a ~15x instructions/sec
  speedup in local benchmarking (once warmed up) and comfortably completes the same MicroPython +
  littlefs boot in well under a minute. Note `--no-dev` (or a separate PyPy-only sync): the `dev`
  dependency group's `mypy` pulls in `ast-serialize`, whose PyO3 build currently requires PyPy
  ≥3.10, so `uv sync`-ing the full dev group under PyPy 3.10 fails - this only matters for
  mypy/ruff/pytest tooling, not for running the emulator itself, whose only runtime dependency
  (`pyelftools`, for `--bootrom` ELF parsing) is a pure-Python wheel with no PyPy-specific build
  issues of its own. `ci-micropython.yml` and `ci-pico-sdk.yml` run the firmware-boot steps against a
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
- **`CortexM0Core.registers` is now a plain `list[int]` instead of `Uint32Array`.** It's the
  hottest bus in the whole emulator - every single instruction reads/writes several of its 16
  slots - and every access went through `Uint32Array.__getitem__`/`__setitem__`, a Python-level
  method call, versus a `list` subscript's C-level bytecode op. Compared against rp2040js (Node
  v26/V8) booting the identical firmware+littlefs+script combination the table above measures:
  rp2040js finishes in 4.11s versus CPython 3.10's pre-this-change 224.79s (~55x) and even PyPy's
  11.51s (~2.8x) - `cProfile` on that run showed `Uint32Array.__getitem__`/`__setitem__` alone
  accounting for over 1.8 million calls in a 356K-instruction sample (~3.3 reads + ~1.9 writes per
  instruction), the same class of overhead the `PC_REGISTER` direct-indexing change above already
  removed for one register, now extended to all 16.

  The catch: `Uint32Array.__setitem__` did `int(value) & 0xFFFFFFFF` unconditionally on every
  write, and roughly 60 call sites across `cortex_m0_core.py` (plus `tests/utils/
  rp2040_test_driver.py`'s direct `core.registers[i] = ...` pokes) relied on that implicitly -
  Python ints don't wrap at 32 bits on their own, so e.g. `_subtract_update_flags()` can return a
  genuine negative int, `~register_value` (MVNS, BICS' operand) is always negative, and a left
  shift (LSLS, ROR) can exceed 32 bits outright. Every one of those write sites now masks
  explicitly with `& 0xFFFFFFFF` at the point of assignment instead of relying on the wrapper -
  audited one by one against real ARM semantics rather than masking indiscriminately everywhere
  (though a handful of sites that are simplest to reason about that way, e.g. the `sp`/`lr`/`pc`
  property setters, do mask unconditionally to match the old wrapper's behavior exactly). Two
  sites that looked masked already turned out not to be and only surfaced as real test failures
  once the wrapper's safety net was gone (`test_should_execute_an_cmn_r5_r2_instruction`,
  `test_should_execute_a_subs_r1_1_instruction_with_overflow` - both traced to the test driver's
  `set_registers()` writing raw negative Python ints like `-2` to probe wraparound behavior, which
  the wrapper used to silently fix up) - a reminder that "obviously safe" needs verifying by
  running the full suite, not just reasoning about the production call sites in isolation.

  Verified via the full instruction test suite (with the two fixed test-driver sites above) plus
  real MicroPython + littlefs boots on 1.21 and 1.28 before/after, confirming byte-identical
  instruction-count traces (64,679,598 either way for 1.28's run in the table above - purely a
  dispatch-speed change, not a behavior change). Measured effect: ~13% faster under both CPython
  3.10 and 3.14+JIT on the real 1.28 boot-and-run workload (224.79s -> 195.19s, 130.23s -> 113.13s)
  and ~16% higher synthetic instructions/sec under CPython 3.10 (the synthetic benchmark is
  ADD/SUB-heavy, i.e. almost entirely register reads/writes, so it's more sensitive to this
  specific change than a real boot's mixed workload). PyPy's synthetic/real numbers were
  essentially unchanged (within run-to-run noise) - its JIT already optimizes the old
  `Uint32Array` indirection away at this level, same pattern as the struct-based bit-ops change
  above.
- **`RP2040.bootrom` got the same `list[int]` treatment as `registers` above, and `Uint32Array`
  was deleted from `utils/bit.py` entirely** once that left it with zero remaining callers. Much
  smaller surface than `registers` (`bootrom` is read heavily whenever execution is actually
  inside bootrom - real ROM routines like its own memcpy helper get called repeatedly during
  flash/USB operations - but read/written from just two call sites in `RP2040.read_uint32()`/
  `write_uint32()`, plus one bulk-load site in `load_bootrom()`, versus `registers`' ~60): the
  bulk-load site (`self.bootrom.set(bootrom_data)`) became an explicit masked slice-assignment
  (`self.bootrom[: len(bootrom_data)] = (v & 0xFFFFFFFF for v in bootrom_data)` - `load_bootrom()`
  is called with fewer than the full 4096 words in `tests/test_rp2040.py`, so a full-length
  `self.bootrom[:] = ...` would've been wrong), and the single `write_uint32()` write site got the
  same explicit `& 0xFFFFFFFF`. Verified the same way: full instruction suite, plus real 1.21/1.28
  boots confirming identical instruction counts before/after. Measured effect on top of the
  `registers` change above, under CPython 3.10: another ~3% off the real 1.28 boot-and-run
  (195.19s -> 188.98s); negligible for CPython 3.14+JIT and PyPy (both already fast enough here
  that bootrom's smaller share of total instructions doesn't move the needle much, unlike
  `registers` which every single instruction touches).
- **`RP2040.write_uint32()` checked `find_peripheral()` (a dict lookup) unconditionally, before any
  of the cheap RAM/flash/bootrom range comparisons.** Found while profiling (`cProfile` on a real
  MicroPython 1.21 boot-to-first-print run) why a large share of executed instructions - ~23-28% in
  both 1.21 and 1.28, see the 1.21-vs-1.28 discussion above - go through USB-interrupt-adjacent
  code: `find_peripheral()` showed up called almost 1:1 with every `write_uint32()` call
  (159,443 vs. 158,989 in that trace), which only makes sense if it's being called unconditionally
  rather than as a fallback. It was: unlike `read_uint32()`/`write_uint8()`/`write_uint16()`, which
  all check RAM (and flash/bootrom) via plain integer comparisons *before* falling back to the
  dict-based peripheral lookup, `write_uint32()` did the dict lookup first - so every 32-bit RAM
  write (stack spills, GC, locals - the overwhelmingly common case for real firmware) paid for a
  `dict.get()` that was always going to miss. Reordered to match the other three methods' range
  order. This is a general throughput fix, not specific to the 1.21-vs-1.28 gap or to USB - it
  just happened to surface while investigating that. Verified via the full test suite (436/436) and
  a clean A/B (`rp2040py bench`) on a real MicroPython 1.21 boot-to-first-print run, three runs
  each side: ~273K-313K instructions/sec before (noisy) vs. ~307K-312K after (tighter), roughly a
  7-8% improvement on top of everything above.
- **`RP2040.bootrom_byte_size`'s caching pattern extended to `sram`/`usb_dpram`/`flash`
  (`ram_byte_size`/`dpram_byte_size`/`flash_byte_size`)** - `read_uint16()`/`read_uint8()`/
  `write_uint8()`/`write_uint16()` were still calling `len(self.sram)`/`len(self.flash)` on
  essentially every RAM/flash bus access. Honest result, unlike the fix above: no measurable
  difference on a clean A/B (3 runs each side, same 1.21 benchmark) -
  `bytearray.__len__()` is already O(1) in CPython (a struct field read), so there wasn't
  much to save here, unlike `find_peripheral()`'s real `dict.get()`. Kept for consistency with
  the established pattern and because it cannot be a regression, not because it's a proven win.
- **HLE (high-level emulation) hook for bootrom's `__memcpy`/`__memcpy_44`, opt-in via
  `RP2040PY_ENABLE_HLE_MEMCPY=1` (off by default).** Found while tracing the 1.21-vs-1.28
  instruction-count gap (see above) that real firmware's own `memcpy()` calls - TinyUSB's
  `tu_fifo_read`/`tu_fifo_write`, littlefs's `lfs2_bd_read()` - route through these two bootrom
  entry points, and that interpreting their per-byte/per-word copy loop one emulated Thumb
  instruction at a time is pure overhead once the copy itself can be done as a single Python-level
  bulk operation. `CortexM0Core.execute_instruction()` checks `core.pc` against
  `RP2040.hle_memcpy_entries` (a `frozenset[int]`) before the normal fetch/decode/dispatch path; on
  a hit, `_hle_memcpy()` performs the copy via `RP2040.bus_copy()` (a `bytearray` slice copy when
  both ends land in RAM/flash - the common case - falling back to a plain byte-by-byte bus copy for
  anything else, e.g. touching peripheral space) and jumps directly to the return address, still
  advancing the clock by a rough (not cycle-accurate) cost estimate so SOF-cadence/timing-sensitive
  code elsewhere isn't disturbed by treating the copy as free.

  Detecting *where* these two routines live is a whole-bootrom byte-pattern scan (`bytes.find()`
  over the 16KB bootrom, once per `load_bootrom()` call - nowhere near the hot per-instruction
  path), not a hardcoded address: downloaded `b0.elf`/`b2.elf` via `--bootrom` and confirmed the
  routines' own machine code is byte-for-byte identical across B0/B1/B2 (only their *position*
  differs - B0: `0x2888`/`0x28a0`, B1: `0x2628`/`0x2640`, B2: `0x2604`/`0x261c` - presumably other
  ROM code shifting around them, not the routines themselves changing), so a scan finds the right
  offset for any of the three (or any future revision carrying the same unchanged routine)
  automatically, rather than needing a manually-maintained per-revision offset table. A revision
  where the signature genuinely isn't found anywhere leaves the hook inert for that image rather
  than risking a misfire. Verified booting real MicroPython 1.21 + littlefs against all three
  bootrom revisions with the hook enabled - identical, correct output (`Hello, MicroPython!
  version: 1.21.0`) on each.

  **Measured net negative on both benchmarks - off by default, not recommended to enable.** No
  measurable improvement on the fast 1.21 boot-to-first-print benchmark (memcpy is only ~0.2% of
  its instructions there). The real test - the full MicroPython 1.28 boot-and-run, where `memcpy`
  traffic is ~18x higher - is now measured too: 213.86s baseline vs. 217.82s with the hook enabled,
  **~1.8% *slower***, not faster (instructions/sec is misleading here and shouldn't be used - the
  hook collapses many interpreted instructions into one counted step, so the two runs' step counts
  aren't the same unit; wall-clock time is the only fair comparison). The per-instruction
  `core.pc in self.rp2040.hle_memcpy_entries` check is paid by every single instruction in the run,
  and even 18x more memcpy traffic isn't a large enough share of total execution to outweigh that
  fixed tax. The mechanism itself is correct (verified against real boots on all three bootrom
  revisions) - this is a clean "measured, doesn't pay off" result, not a bug. See
  `docs/BACKLOG.md` for the full numbers and rationale.
- **Ahead-of-time Cython compilation of `CortexM0Core`/`RP2040`'s hot paths - unlike every
  runtime-check-based idea above (dispatch table aside), this one is a genuine ~4x win, on by
  default.** All the items above tried to make the *interpreter loop* itself cheaper or skip parts
  of it conditionally; this instead compiles the whole thing to C ahead of time, so there's no
  per-instruction "should I take the fast path" check to weigh against the savings - the exact
  problem that made the HLE hook and three separate JIT attempts (`docs/JIT_BACKLOG.md`) all net
  negative. Ships as an optional-but-automatic `rp2040py.native` extension: every one of
  `CortexM0Core`'s ~90 instruction handlers is a genuinely C-typed function (not just typed class
  fields - an earlier, narrower attempt at exactly that shipped first and measured only ~2-9%
  real-world despite an ~11.5x isolated estimate, then got replaced by this full port once the gap
  was root-caused to untyped method *bodies* re-boxing every value at the call boundary), dispatched
  through a real C function-pointer table, plus `RP2040`'s `read`/`write_uint8/16/32` bus paths.
  Falls back to the identical pure-Python implementation automatically if no C compiler is
  available at install time, or at runtime via `RP2040PY_SKIP_CYTHON=1`. Measured **~3.9x** on the
  synthetic instructions/sec benchmark and **~4.1x** on a real MicroPython 1.21 boot - see
  `docs/BACKLOG.md`'s "Cython port of the interpreter core" section for the full writeup (the
  root-cause analysis of why the first attempt underperformed, the abi3/stable-ABI build, the PyPy
  regression this found and fixed, and the two real correctness bugs the build-then-test loop
  caught along the way).

### `RPWatchdog` reset - real, not a no-op (unlike upstream)

`peripherals/watchdog.py`'s `RPWatchdog.on_watchdog_trigger` (defaulted to
`_default_watchdog_trigger`, overridden by `BaseDevice.__init__` for both `MicroPythonDevice` and
`KalumaDevice`) performs a real in-place device reset when `machine.reset()`/`machine.bootloader()`
write the `CTRL` register's `TRIGGER` bit: `CortexM0Core.reset()` (sp/pc/cycles plus
interrupt/exception state, both the pure-Python and `rp2040py.native` Cython ports),
`RPPWM.reset()`/`RPDMA.reset()`, and `USBCDC.reset()`/`RPUSBController.reset()` all run, then
execution jumps back to flash's entry point - `RP2040.reset(preserve_flash=True)`, a new parameter
(existing callers unaffected, still wipe flash by default). Every externally-referenced peripheral
object (notably `mcu.usb_ctrl`, which `BaseDevice.cdc = USBCDC(mcu.usb_ctrl)` holds a direct
reference to) keeps its identity rather than being reconstructed.

Confirmed directly against upstream's `src/peripherals/watchdog.ts`: its `RPWatchdog` has the
identical register layout (`CTRL`/`LOAD`/`REASON`/`SCRATCH0-7`/`TICK`) and the same `TRIGGER`-bit
detection in `writeUint32()`, but its `onWatchdogTrigger` default is just
`this.rp2040.logger.warn(this.name, 'Watchdog triggered, but no reset handler provided')` - no
demo script in rp2040js's own `demo/` overrides it either. `machine.reset()`/`machine.bootloader()`
against upstream leaves the emulated CPU spinning forever waiting for a reset that never happens -
the exact behavior rp2040py had before `docs/BACKLOG.md`'s "Unified process-shutdown coordinator"
work wired this handler up (found while checking which `mpremote` commands work over
`--tcp-port` - see `docs/BACKLOG.md` for the full writeup).

### Configurable bootrom revisions (`--bootrom`) - upstream ships exactly one, hardcoded

`device/bootrom.py` exposes `BOOTROM_B1` (used by default, unchanged from the original port) plus
`--bootrom <b0|b1|b2|path>` (`cli/__init__.py`'s `_resolve_bootrom_words`, downloaded/cached the
same way firmware images are via `firmware_retrieve.py`'s `BOOTROM` spec) to boot against a
different bootrom revision's ELF or raw binary instead - see
[README](../README.md#bootrom-revisions) and `docs/BACKLOG.md`'s "Bootrom B0/B2 support (issue
#11)".

Upstream's `demo/bootrom.ts` ships exactly one `Uint32Array` (`bootromB1`, "revision: B1"),
imported directly by every demo script with no alternative and no CLI flag to select a different
one - confirmed by reading the file directly, it's the same ~4,100-word data-only export
`bootrom.py`'s `BOOTROM_B1` was ported from in the first place, just with no B0/B2 counterpart
alongside it anywhere in the repo.

### External serial-tool passthrough (`--tcp-port`/`--pty`, `rp2040py mpremote`) - no rp2040js equivalent

`cli/socket_repl.py`'s `SocketInteractiveRepl` (`--tcp-port`) and `cli/pty_repl.py`'s
`PtyInteractiveRepl` (`--pty`, POSIX only) serve the device's USB-CDC console over a real TCP
socket or pseudo-terminal instead of this process's own stdio, so external serial-oriented tools -
`mpremote` chief among them - can drive the emulator exactly as they would a real board, with no
rp2040py-specific client needed for most commands (see `docs/mpremote.md`). `rp2040py mpremote
<args...>` (`cli/__init__.py`'s `_cmd_mpremote`) goes one step further for `mpremote` specifically:
a thin proxy that also monkeypatches around a real upstream `mpremote`/pySerial bug
(`mpremote.console.ConsolePosix.waitchar()` unconditionally reading a `.fd` attribute pySerial's
`socket://` backend never defines - filed at
https://github.com/micropython/micropython/issues/18660#issuecomment-5239811170), so `mpremote`'s
own bare interactive REPL works over `--tcp-port` too, not just `--pty`.

Confirmed by reading upstream's `src/usb/cdc.ts` and the rest of `src/`/`demo/` directly: there is
no pty/socket-backed serial passthrough anywhere in rp2040js - `grep -rl "pty\|socket\|net\."`
across its source turns up nothing beyond its own GDB TCP server (`src/gdb/gdb-tcp-server.ts`,
unrelated to the USB-CDC console) and unrelated matches in PIO/FIFO/peripheral code. Every
rp2040js demo drives the emulated console through its own process's stdio only - there is no
built-in way for an external tool like `mpremote` to attach to it at all.
