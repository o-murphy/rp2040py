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
- [ ] `peripherals/pio.spec.ts` → `tests/test_pio.py`
- [ ] `usb/cdc.spec.ts` → `tests/test_cdc.py`
- [x] `instructions.spec.ts` → `tests/test_instructions.py` (126/126 passing)
- [ ] `rp2040.spec.ts` → `tests/test_rp2040.py`
- [ ] `sio.spec.ts` → `tests/test_sio.py`

### demo / debug (needed to actually run firmware, e.g. MicroPython)
- [x] `demo/bootrom.ts` → `demo/bootrom.py` (RP2040 bootrom binary, data only; verified: real bootrom executes thousands of instructions correctly)
- [x] `demo/intelhex.ts` → `demo/intelhex.py`
- [x] `demo/load-flash.ts` → `demo/load_flash.py` (UF2 decoder implemented directly, no external `uf2` package - keeps zero runtime deps)
- [x] `demo/emulator-run.ts` → `demo/emulator_run.py` (generic hex/uf2 runner + GDB server)
- [x] `demo/micropython-run.ts` → `demo/micropython_run.py` (MicroPython/CircuitPython UF2 runner + USB CDC console)
- [ ] `debug/gdbdiff.ts` → `debug/gdbdiff.py` (deferred - needs real-hardware GDB client (`test-utils/gdbclient.ts`), out of scope for running firmware in the emulator)

### MicroPython CI test fixtures (`test/` in rp2040js)
- [x] `test/micropython/main.py` → `tests/micropython/main.py` (copied verbatim - already Python, runs *inside* the emulated device)
- [x] `test/micropython/main-spi.py` → `tests/micropython/main-spi.py` (copied verbatim, same reason)
- [x] `test/mklittlefs.py` → `tests/mklittlefs.py` (needs `littlefs-python`, added as a dev dependency)
- [x] `test/micropython-spi-test.ts` → `tests/micropython_spi_run.py`

### CI (`.github/workflows/`)
- [x] `ci-test.yml` → covered by the existing `pre-commit.yml` (mypy + ruff + pytest, equivalent lint/test gate)
- [x] `ci-micropython.yml` → `ci-micropython.yml` (uv-based)
- [x] `ci-pico-sdk.yml` → `ci-pico-sdk.yml` (uv-based)

## Known differences from rp2040js

Places where the Python port's runtime behavior necessarily diverges from the JS original,
beyond straightforward syntax translation.

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
| CPython 3.10 | 251,654 | 312.48s (208,014 steps/sec) |
| CPython 3.14 + `PYTHON_JIT=1` | 478,319 (~1.9x) | 175.41s (~1.8x, 370,570 steps/sec) |
| PyPy 3.10 | 28,975,249 (~115x) | 9.55s (~33x, 6,803,084 steps/sec) |

("Steps/sec" counts `WFI`/`WFE` clock-fast-forward iterations alongside real instructions, so
it's not directly comparable to the synthetic column's pure instructions/sec - the *ratio between
interpreters* is what's meaningful here, not the absolute numbers.) PyPy's JIT is decisively the
biggest lever; CPython 3.14's still-experimental JIT is a smaller but real, zero-code-change win.

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
  benchmarking. A dispatch-table redesign of `execute_instruction()` (opcode → handler function,
  replacing the linear `if`/`elif` scan) would likely be the single biggest remaining win, but was
  deferred as a larger, higher-risk refactor touching all ~90 instruction handlers.
