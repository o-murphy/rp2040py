# 0047. CYW43 pure-Python hot path: `check_changed_pins` fix + `GPIOPin`/`RPPIO` Cython ports

- Status: Implemented — verified (2026-08-14)
- Conceived: 2026-08-14 · Implemented: 2026-08-14
- Related: 0013 (Cython interpreter core — the facade + `.pxd`/`.pyx` pattern this extends to two
  more peripherals), 0031 (`StateMachine` → Cython + `clock.tick()` batching — `RPPIO` was left
  pure there, "not yet tried #4"; this finishes it), 0034/0039 (native `_execute_batch`/
  `SimulationClock` ports — the reason native CPython had already closed most of the gap to PyPy),
  0037/0043/0044 (PIO/DMA clock-coupled stepping — why the `bench` loop is invalid for PIO work,
  see "Measurement pitfall" below), 0027 (CYW43 epic — the workload this speeds up)

## Context

After the native `_execute_batch`/core/clock ports, the remaining interpreter cost on a **CYW43
(Pico W) boot** — the emulated CYW43439 firmware upload + `nic.scan()`/`connect()`, all driven by a
bit-banged gSPI running on **PIO** — was still pure Python. Profiling the real native loop showed
where: essentially all of it in the per-PIO-clock-edge pin cascade, none of it in DMA.

### Measurement pitfall (recorded because it wasted a full pass)

The `rp2040py bench` firmware loop (`cli/__init__.py:_bench_firmware`) drives
`core.execute_instruction()` directly and **never calls `pio.step()`** — unlike the real
`_execute_batch` loop (`native/_simulator.pyx`). Profiling `main-cyw43.py` through `bench` therefore
shows a completely wrong picture: with the PIO never stepped, the RX DMA channel the firmware
busy-waits on never advances, so `dma.py`'s CTRL-register read looks like ~40% of the run (114M
`while(BUSY)` polls) and DMA looks like the bottleneck. It is not — that is a bench-mode artifact.
Driving the real `Simulator._execute_batch()` (with `simulator.stopped = False` first — the loop
no-ops otherwise) reboots correctly and reprofiles: DMA is ~1.6%, and **`gpio_pin.py` (~46%) +
`peripherals/pio.py` RPPIO (~29%)** dominate — the PIO→GPIO pin-transition simulation. **Do not use
`bench` to profile PIO/CYW43 workloads.**

## What landed

1. **Algorithmic fix — `RPPIO.check_changed_pins` (pure `peripherals/_pio.py`).** It scanned all 30
   GPIO slots every call even though, measured over a boot, only **1.18 pins change per call on
   average** (122.6M slots scanned vs 4.83M actual changes — 97% wasted bit-tests). Iterate the set
   bits of `changed_pins` directly (isolate-lowest / clear-lowest) instead of `range(30)`.
   Low-to-high visitation order preserved → behaviour identical. Also benefits PyPy (it's in the
   pure module). CYW43 boot-to-scan ~37.2s → ~30.5s (~1.22x).

2. **`GPIOPin` → native (`native/_gpio_pin.pyx` + facade `gpio_pin.py`, pure `_gpio_pin.py`).** The
   #1 cost was pure `@property`-call overhead: each `value` evaluation was ~9 property/helper calls
   (`function_select`, `raw_output_*`, the override getters, `_apply_override`). Collapsed into a
   single inline-C `cdef _value()`; full `@property` surface kept for external callers. ~30.5s →
   ~19.6s (~1.56x). Measured **not** an algorithmic problem — `check_for_updates` is 99.7%
   non-no-op, so it's genuine per-call overhead Cython removes.

3. **`RPPIO` → native (`native/_pio.pyx` + `_pio.pxd`, facade `peripherals/pio.py`, pure `_pio.py`).**
   A `cdef class` can't inherit the pure-Python `BasePeripheral`, so **RPPIO satisfies the
   `Peripheral` Protocol structurally** instead (the small base surface — `rp2040`/`name`/
   `raw_write_value` + read/write/atomic + log helpers — reproduced inline). `__cinit__` for
   construction (matches `_cortex_m0_core`). ~19.7s → ~14.2s (~1.39x).

4. **cimport wiring** (opened up by the new `.pxd` surfaces): `native/_pio.pyx` cimports the native
   `StateMachine` for direct C `machine.step()`/`.check_wait()`/`.enabled` in the hot loops, and
   cimports `GPIOPin` for a direct C `check_for_updates()` call in `check_changed_pins`. Note: a
   **mutual** `GPIOPin`↔`RPPIO` cimport was tried and reverted — it creates a circular *runtime*
   import (`_gpio_pin` → `pio` facade → `_pio` → cimport `_gpio_pin`), which made the `<RPPIO?>`
   cast see a different RPPIO type object depending on import order (passed tests, crashed the
   driver). Broke the cycle by keeping only the one-way `_pio → _gpio_pin` cimport and sourcing
   `WaitType` in `_gpio_pin` from `pio_registers` (its real home) instead of the `pio` facade, so
   `_gpio_pin` has no `_pio` dependency. The `_gpio_pin → RPPIO` pin_values cimport was dropped back
   to an object read (marginal — gpio_pin is already off the profile).

5. **annotate=True HTML pass.** Read the yellow (Python-C-API) lines still left in the hot path:
   replaced `(x & -x).bit_length()` (boxed the C uint every set-bit iteration) with a portable C
   count-trailing-zeros (`__builtin_ctz` + a `_BitScanForward` shim for MSVC/Windows wheels); and
   `GPIOPin.check_for_updates` now tracks the last state as a plain C int (`_last_state`), comparing
   it as an int and materialising the `GPIOPinState` objects only on an actual change.

6. **`.pyi` stubs for `rp2040py.native.*`.** The compiled modules had no type info (`mypy`'s
   `ignore_missing_imports` typed everything through the native facades as `Any`). Added one `.pyi`
   per native module re-exporting the pure sibling's public symbols. This surfaced — and this record
   fixes — two previously-masked issues: `clock.IClock` was missing `tick`/`nanos_to_next_alarm`/
   `has_scheduled_alarm` (driven every instruction, provided by every implementation), and
   `external/key_mock.py` called a non-existent `GPIOPin.drive_input()` (a latent `AttributeError`
   on `press()`/`release()`; the method is `set_input_value()`).

## Verification

- Full `pre-commit` green on **both** pure and native builds (mypy, ruff, pytest ×2) at every step.
- **Stable ABI**: local builds are CPython 3.10 (below the abi3 floor = full API), so the abi3 path
  was verified separately by building under CPython 3.14 with `Py_LIMITED_API=0x030B0000` — every
  new module compiles clean, including `async def run()` in a `cdef class` and the cross-module
  cimports.
- Measured on the CYW43 `pico_w` boot to `nic.scan()` via the native core (interleaved A/B, same
  window, since the shared container's run-to-run load varies up to ~2x): **~37.2s → ~14.2s (~2.6x
  end to end)**. No effect on non-PIO workloads (the pin cascade only runs while a state machine
  toggles pins), so the README's resident-script performance table is unchanged.

## Did it beat PyPy?

No — on the same CYW43 workload PyPy is still ~1.16x ahead of native CPython (~15.7s vs ~18.3s in a
busier window; the ratio is what's stable). PyPy's JIT handles the pure-Python cascade very well and
also gets the `check_changed_pins` algorithmic fix (it's in the pure module); the native `GPIOPin`/
`RPPIO` ports it skips only narrowed the gap. Consistent with the README's "PyPy is still the clear
winner for CPU-bound runs" — but the default native CPython path is now ~2.6x faster on WiFi.

## Not done (measured, deliberately left)

- `read_uint32`/`write_uint32` register dispatch stays if/elif rather than a dispatch table, in both
  pure and native: it's cold (MMIO is rare vs millions of PIO steps) and range-structured (SM
  register banks, `INSTR_MEM`) rather than a dense index, so a table buys nothing — unlike the
  hot, densely-indexed CPU `DISPATCH_TABLE` (0013).
- D-cache shrink of that CPU `DISPATCH_TABLE` (512 KiB): measured a 0.5 KiB hot working set (top-64
  opcodes cover 99%), L1-resident — not a cache problem, no win.
- `-falign-functions`/LTO/mypyc/nanobind on the core: each measured ~noise or a net loss vs the
  hand-tuned Cython (separate exploratory benches, not committed).
