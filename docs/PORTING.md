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
