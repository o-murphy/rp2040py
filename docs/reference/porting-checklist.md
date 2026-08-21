<!-- Reference (living doc). Individual decisions/investigations moved to docs/records/;
     the two minor known-differences below stayed here. See docs/0000-TRACKER.md. -->

<!-- migrated verbatim from docs/PORTING.md lines 1-131 -->

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


<!-- migrated verbatim from docs/PORTING.md lines 411-425 -->

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


<!-- migrated verbatim from docs/PORTING.md lines 744-766 -->

### `RPWatchdog` reset - real, not a no-op (unlike upstream)

`peripherals/watchdog.py`'s `RPWatchdog.on_watchdog_trigger` (defaulted to
`_default_watchdog_trigger`, overridden by `BaseDevice.__init__` for both `MicroPythonDevice` and
`KalumaDevice`) performs a real in-place device reset when `machine.reset()`/`machine.bootloader()`
write the `CTRL` register's `TRIGGER` bit, then execution jumps back to flash's entry point -
`RP2040.reset(preserve_flash=True)`, a parameter added for this (construction-time callers
unaffected, still wipe flash by default). Every externally-referenced peripheral object (notably
`mcu.usb_ctrl`, which `BaseDevice.cdc = USBCDC(mcu.usb_ctrl)` holds a direct reference to) keeps
its identity rather than being reconstructed.

Since [record 0089](../records/0089-one-reset-for-every-trigger.md) that handler is **one caller of
one owner**, not the only reset path - a RESET button (`external/reset_button.py`, a real RUN-pin
level) and a host-side `device.ahard_reset()` reach the same sequence - and what the sequence
covers grew from `core`/`pwm`/`dma`/`ppb` to what a real reset covers: the pads and IO, SIO, the
clocks, UART/SPI/I2C/PIO/TIMER/ADC/USB/RTC/BUSCTRL and the XIP domain (`XIP_CTRL` + `SSI`),
gated by `PSM.WDSEL`/`RESETS.WDSEL` the way hardware
gates them. Two consequences worth knowing when comparing against upstream, since neither is
modelled there at all: a GPIO the guest left driving is released by a reset, and the firmware reads
back the reset *cause* the trigger actually had (`machine.reset_cause()` /
`microcontroller.cpu.reset_reason`).

Confirmed directly against upstream's `src/peripherals/watchdog.ts`: its `RPWatchdog` has the
identical register layout (`CTRL`/`LOAD`/`REASON`/`SCRATCH0-7`/`TICK`) and the same `TRIGGER`-bit
detection in `writeUint32()`, but its `onWatchdogTrigger` default is just
`this.rp2040.logger.warn(this.name, 'Watchdog triggered, but no reset handler provided')` - no
demo script in rp2040js's own `demo/` overrides it either. `machine.reset()`/`machine.bootloader()`
against upstream leaves the emulated CPU spinning forever waiting for a reset that never happens -
the exact behavior rp2040py had before `docs/BACKLOG.md`'s "Unified process-shutdown coordinator"
work wired this handler up (found while checking which `mpremote` commands work over
`--tcp-port` - see `docs/BACKLOG.md` for the full writeup).


<!-- restructure: the individual "known differences" grew into their own records -->

## Known differences — index

The individual known-differences that grew large are now numbered records
(see [../0000-TRACKER.md](../0000-TRACKER.md)):

- [0001](../records/0001-cli-device-api.md) CLI packaging
- [0002](../records/0002-mklittlefs-image.md) mklittlefs image handling
- [0003](../records/0003-littlefs-image-format.md) littlefs image format vs. old MicroPython
- [0006](../records/0006-gpio-pull-floating.md) GPIO pull-up/pull-down for undriven pins
- [0007](../records/0007-bootrom-revisions.md) configurable bootrom revisions
- [0014](../records/0014-threading-model.md) threading model
- [0017](../records/0017-perf-python-vs-v8.md) performance: pure-Python vs V8
- [0018](../records/0018-raw-repl-txfifo.md) raw-REPL cross-thread tx_fifo bug
- [0020](../records/0020-pty-serial-passthrough.md) external serial-tool passthrough

The two minor ones (`pio_assembler.py` argument order, `RPWatchdog` reset) stayed above in this file.
