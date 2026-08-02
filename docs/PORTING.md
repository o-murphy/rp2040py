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
is a two-line shim). `mklittlefs` is the one subcommand with a dependency (`littlefs-python`), so
it's gated behind the optional `fs` extra (`[project.optional-dependencies]`) rather than pulled
into the zero-runtime-dependency default install; the `dev` dependency group depends on
`rp2040py[fs]` so it's still there for `uv sync` in CI and local dev. `demo/emulator_run.py`,
`demo/micropython_run.py`, and `demo/benchmark.py` are now thin wrappers around the same
`rp2040py.cli` subcommands (`run`, `micropython`, `bench`), kept so the documented `uv run python
demo/*.py` commands keep working unchanged for anyone working from a checkout rather than a
pip/uv install.

`cli/firmware_retrieve.py` (also no rp2040js equivalent; originally split across `cli/mp_retrieve.py`
and `cli/kaluma_retrieve.py`, merged once the duplication between them - and a real `v`-prefix bug
in the CircuitPython path only one of them had - became annoying enough to fix properly) resolves
`micropython --image`/`--circuitpython` and `kaluma --image`: a known version tag, or an existing
local path, downloading the matching UF2 into the current directory on first use and reusing it
thereafter. Each firmware is a declarative `FirmwareSpec` (filename/URL templates, default tag,
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
`MicroPythonDevice(BaseDevice)` layers the raw-REPL `exec()` family and its `ThreadPoolExecutor` on
top; `KalumaDevice(BaseDevice)` stays thin - Kaluma has no raw-REPL-equivalent protocol (see
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
  `Exception ignored in: <module 'threading'>` traceback instead of a clean exit. All four demo
  entry points (`demo/emulator_run.py`, `demo/micropython_run.py`, `demo/kaluma_run.py`,
  `tests/micropython_spi_run.py`) do this wait-then-`os._exit(130)`-on-`KeyboardInterrupt` dance.
- **Don't schedule follow-up work with `threading.Timer`/a real OS thread if it touches anything a
  `Simulator` worker thread also touches** (a FIFO, a peripheral register, `USBCDC.tx_fifo`, etc.)
  - use `simulator.clock.create_alarm(...)` instead, whose callback runs synchronously inside
    `Clock.tick()` on whichever thread is already driving the simulator. See the next section for
    a real bug this exact mistake caused.

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
(`cli/__init__.py`) and `demo/kaluma_run.py`'s: `os.read()` can return up to 4096 bytes in one
chunk from a single large paste into the terminal, comfortably over the 512-byte FIFO. Both now
retry with a short sleep while the FIFO's full instead of assuming `send_serial_byte()` always has
room - safe as a plain blocking retry here (no clock-alarm scheduling needed) because this loop
runs on its own dedicated stdin-reader thread, not the simulator's; blocking it briefly doesn't
race anything.

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
