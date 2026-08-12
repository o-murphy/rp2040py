# 0031. PIO Cython port and opt-in clock.tick() batching

- Status: Implemented — measured (2026-08-12)
- Conceived: 2026-08-12
- Related: follow-up of 0013 (Cython core)

<!-- migrated verbatim from docs/BACKLOG.md lines 965-1084 -->

### Follow-up: PIO Cython port + opt-in `clock.tick()` batching — both done, measured (2026-08-12)

**Status: implemented, verified dual-mode, not yet committed at time of writing (staged on branch
`cythonize/pio`).** Found while profiling *why* a real cyw43 firmware boot (see
`docs/CYW43_WIFI_BACKLOG.md`) was taking 8+ minutes to get partway through firmware download - the
bottleneck turned out to be entirely shared simulator infrastructure, not cyw43 code at all
(`GSPIBus`'s own GPIO listeners were ≈0.4% of profiled time). A bounded-wall-clock cProfile harness
(constructs `MicroPythonDevice`, boots real cached firmware, runs `nic.active(True)`/`nic.scan()`
inside `asyncio.wait_for(..., timeout=25)` so it always exits cleanly and cProfile gets to write its
stats regardless of how far the boot got) found three roughly-comparable-sized costs: `simulator.py`
`_execute_batch()`'s own per-instruction Python dispatch loop (~43%, unaffected by the CPU core
already being native - the *loop itself* was never ported, only `core.execute_instruction()`),
`peripherals/pio.py` PIO stepping with **no Cython port at all**, unlike the CPU core/RP2040
bus/bit-ops (~34%), and `clock/simulation_clock.py`'s `tick()` (~18% - 24.6M calls in a 25s window,
almost entirely per-call overhead on an already-lean body). Two of the three got tackled here;
`_execute_batch()`'s own loop is a separate, explicitly out-of-scope follow-up - see its own
paragraph at the end of this section.

**A real environment gotcha hit while profiling, worth remembering for next time:** `uv run`
without an explicit `--python cpython-3.10` pin can silently resolve to a PyPy interpreter for this
project - which never gets native extensions built at all, by design (`setup.py`'s
`sys.implementation.name != "cpython"` skip, see "PyPy: compiling for it was actively harmful"
above) - so a profiling run can *look* like it's testing the native path while actually running
pure Python, with no error either way. `.venv/bin/python3` directly (after `uv sync --python
cpython-3.10` at least once) sidesteps it. Checking `SomeClass.__module__` is the real way to
confirm which backend is active - checking `some_module.__file__` proves nothing, since a facade
module's own `__file__` is always its own path regardless of which backend it re-exports.

**Part 1: `StateMachine` → Cython, `RPPIO` stays pure Python.** `peripherals/pio.py` has two
classes - `StateMachine` (the actual per-cycle interpreter: `step()`, `execute_instruction()`,
`check_wait()`, `jmp_condition()`, shift-register math) and `RPPIO` (a `BasePeripheral` - register
dispatch, DMA/interrupt wiring, the async `run()` continuation). The profile's hot functions were
almost entirely `StateMachine`'s own self-contained bit/register arithmetic, so only it got ported -
matching the precedent already set by `native/_rp2040.pyx` itself (bus hot paths typed, the ~30
peripheral objects including PIO stay plain Python), not a full-peripheral rewrite. New three-way
split, exactly mirroring this section's own `_cortex_m0_core.py`/`cortex_m0_core.py`/
`native/_cortex_m0_core.pyx` pattern:

- `peripherals/_state_machine.py` - the pure-Python reference, moved verbatim (unchanged
  behavior other than the one bugfix below).
- `peripherals/state_machine.py` - the facade (`try: from rp2040py.native._state_machine import
  StateMachine except ImportError: ...`), reusing the existing shared `rp2040py._native_gate.
  native_disabled()` gate directly rather than reimplementing it.
- `native/_state_machine.pyx` + `.pxd` - the native port. Fields as genuine C types
  (`unsigned int x/y/pc/input_shift_reg/output_shift_reg/exec_ctrl/shift_ctrl/pin_ctrl/...`,
  `bint enabled/waiting/exec_valid/...`); `rp2040`/`pio`/`rx_fifo`/`tx_fifo` deliberately stay
  `object`-typed - `RP2040.gpio`/`.dma` live in `RP2040`'s own `cdef dict __dict__`
  (`native/_rp2040.pxd`), not its typed `.pxd` surface, so nothing would get faster by cimporting
  the concrete class for this specific purpose, and no cross-native-module coupling is needed as a
  result. `WaitType` (a Python `IntEnum` in the reference) becomes plain `unsigned int` module
  constants matching its values (`WAIT_TYPE_NONE=0`, ..., `WAIT_TYPE_OUT=5`) - nothing outside
  `StateMachine` ever reads the field itself (confirmed by grep), so this is a pure internal
  representation choice with no external effect. Methods only ever called from within the class
  (`jmp_condition`, `in_source_value`, `out_instruction`, `wait`, `next_pc`, ...) are `cdef`, not
  `cpdef`; methods `RPPIO` calls from outside (`execute_instruction`, `step`, `check_wait`,
  `read_uint32`/`write_uint32`, ...) stay `cpdef`. `peripherals/pio_registers.py` is a new
  constants-only module (register offsets, bit flags, `WaitType`, `bit_reverse`/`irq_index`) - split
  out specifically to break a circular import (`pio.py` needs `StateMachine` from the facade;
  `StateMachine`'s own implementation needs the same register constants `pio.py` used to own
  directly), not scope creep.

**A real bug found via the port, not by reasoning about it in the abstract - same pattern this
section's own "Correctness verification" already documents (2 bugs caught that way for the CPU
core, not by inspection).** `StateMachine.in_pins`'s rotate-right formula
(`(gpio_values << (32 - in_base)) | (gpio_values >> in_base)`) was never masked to 32 bits in the
pure-Python original - invisible with Python's arbitrary-precision ints, since nothing ever
overflowed a fixed-width type to complain. The native port's typed `unsigned int` return raised a
genuine `OverflowError` on realistic GPIO values (any `in_base` > 0 with a wide-enough
`gpio_values`). Fixed with a trailing `& 0xFFFFFFFF` in **both** implementations, not just the
native one - a real, if narrow, latent bug (the `IN` instruction's own `bit_count==0` case stores
whatever `in_source_value()` returns straight into `input_shift_reg` with no masking of its own),
not a native-port-only workaround.

**Measured, not assumed** (same profiling harness, `RP2040PY_SKIP_CYTHON=0`, 25s bound): **~6.45x
more RPPIO-level PIO steps completed in the same wall-clock window** (956K → 6.17M `RPPIO.step()`
calls across comparable ~25-26s runs), and PIO's own share of total profiled time *dropped*
(~31% → ~28%) despite doing far more actual work - the honest reason a naive before/after
line-for-line cProfile diff looks odd: native `StateMachine` internals don't get their own cProfile
entries at all once compiled (Cython calls don't trigger per-line profiler hooks without the
`profile=True` compiler directive, which this project doesn't set), so their cost simply folds into
whichever Python-level frame called them (`RPPIO.step()`) - "steps completed in the same window" is
the fair comparison metric, not a vanished-looking `pio.py` entry in the profile output.

**Part 2: opt-in `clock.tick()` call-volume reduction, default behavior unchanged.** `tick()`
(`clock/simulation_clock.py`) is called once per executed instruction from `_execute_batch()`'s
busy branch - its own body is already lean (a few attribute reads, a `while` loop that usually
doesn't iterate), so its cost is overwhelmingly Python function-call overhead (~194ns/call measured,
consistent with call overhead dominating a near-trivial body), not wasted internal work.
**Hypothesis-checked before implementing, not assumed:** a throwaway monkeypatch-instrumented
variant of the same profiling harness (sampling `nanos_to_next_alarm` right before every real
`tick()` call across a real boot) confirmed alarms are typically tens of thousands of instructions
away in practice (median ~32,212, mean ~43,324; only 3.85% of calls had a horizon under 64
instructions) - real headroom, not assumed, before writing any batching logic.

Deliberately **opt-in, default off, not a default behavior change** - hardware timer alarms
(`peripherals/timer.py`) and USB SOF/reset/endpoint-completion alarms (`peripherals/usb.py`) fire at
an *exact* simulated nanosecond today, and real firmware can reasonably depend on that precision;
any scheme calling the real `tick()` less often than once per instruction risks firing an alarm up
to a whole batch late in simulated time (never early). New `RP2040PY_CLOCK_TICK_BATCH` env var
(`_clock_batch_gate.py`, same style as `_native_gate.py` - read fresh via `os.environ.get`, not
cached), unset or `1` taking the exact same one-`tick()`-call-per-instruction path as always.
`_execute_batch()`'s busy branch accumulates `cycles * cycle_nanos` into a local `pending_nanos`
instead, tracking a locally-cached `nanos_budget` (refreshed from `clock.nanos_to_next_alarm` on
every real flush) that's decremented per instruction; flushes (the real `tick(pending_nanos)`,
firing any due alarm exactly as it would have anyway) the moment the budget would go non-positive
**or** the configured batch size is reached, whichever first - so a scheduled alarm never fires
later than it does today. A new `SimulationClock.has_scheduled_alarm` property was needed alongside
the pre-existing `nanos_to_next_alarm`: that property already returns `0` both when an alarm is
genuinely due right now *and* when none is scheduled at all - fine for its own two pre-existing
callers (both just want "how far to jump," and jumping by 0 when nothing's scheduled is already
correct), but genuinely ambiguous for deciding whether it's safe to batch unboundedly.

**Measured combined with Part 1** (`RP2040PY_CLOCK_TICK_BATCH=64`, native, same 25s harness):
`tick()` call count dropped ~98% (417K vs. ~25M), its share of total profiled time from ~18% to
~1.3%. PIO stepping increased again on top of Part 1 alone: **8.77M `RPPIO.step()` calls in the
same window - ~9.2x the pre-either-change baseline (956K)**. Verified dual-mode (pure-Python +
native) at `RP2040PY_CLOCK_TICK_BATCH` unset, `1`, `64`, and `256` - full 563-test suite passes at
every value in both backends; full `pre-commit run --all-files` (mypy, ruff, both pytest runs)
passes after both parts, including after the `in_pins` bugfix rebuild.

