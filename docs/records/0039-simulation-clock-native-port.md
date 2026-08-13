# 0039. `SimulationClock` native Cython port

- Status: Implemented — measured (2026-08-13)
- Conceived: 2026-08-13
- Related: follow-up of 0013 (Cython core), 0031 (PIO Cython + tick batching), 0034
  (`_execute_batch()` native port) · working note: `docs/tasks/simulation-clock-cython-port.md` ·
  motivated by 0038's own leftover finding

## Context

0034's own "Decision" section explicitly left `clock` (`SimulationClock`,
`clock/simulation_clock.py`) unported and untyped - even native `RP2040` held it as a plain Python
object, so `clock.tick()`/`nanos_to_next_alarm`/`has_scheduled_alarm` stayed ordinary Python calls
inside an otherwise fully-native `execute_batch()` loop, on every single simulated instruction.
0038 (2026-08-13, same day) is the concrete motivation for finally closing this gap: after fixing a
real correctness bug that was masking it, `nic.active(True)` on `v1.28.0` still cost ~450s real
time for what the driver's own logic bounds to ~1 *simulated* second per `STALL` retry - a ~450x
real-vs-simulated ratio, confirmed to be a pure per-instruction interpretation-throughput ceiling
(both scheduling and correctness were separately ruled out - see 0037 and 0038). `clock.tick()` is
called unconditionally on every instruction in that loop and was the last unported piece of it.

## Decision

Ported `SimulationClock`/`ClockAlarm` to native Cython, following the same
`state_machine.py`/`peripherals/_state_machine.py`/`native/_state_machine.pyx` facade split 0034
itself mirrored:

- `src/rp2040py/clock/_simulation_clock.py` - the pure-Python reference (the previous
  `clock/simulation_clock.py`, moved verbatim).
- `src/rp2040py/clock/simulation_clock.py` - the public facade (`try: from
  rp2040py.native._simulation_clock import ClockAlarm, SimulationClock except ImportError: from
  rp2040py.clock._simulation_clock import ...`). Every caller (peripherals, `simulator.py`,
  `mock_clock.py`) already imported from this path, so no call site needed to change.
- `src/rp2040py/native/_simulation_clock.pyx`/`.pxd` - the native port. `ClockAlarm.next` is typed
  as the concrete `ClockAlarm` (the pointer `SimulationClock.tick()` walks on the hot path);
  `ClockAlarm._clock` stays `object`-typed (only read from `schedule()`/`cancel()`, armed far less
  often than every instruction, and typing it would need a same-file mutual forward declaration for
  no hot-path benefit). `link_alarm`/`unlink_alarm` and `ClockAlarm`'s own
  `next`/`nanos`/`scheduled`/`callback` fields stay module-private (`cdef`, not `public`/`cpdef`) -
  confirmed via grep that nothing outside this one file ever touches them; every external caller
  only uses `create_alarm()`/`alarm.schedule()`/`alarm.cancel()` plus `SimulationClock`'s own
  `nanos`/`micros`/`tick()`/`nanos_to_next_alarm`/`has_scheduled_alarm`. `SimulationClock`
  deliberately does not subclass `clock.clock.IClock` (a `typing.Protocol`) - a `cdef class` can
  only extend `object` or another extension type; structural conformance is enough, matching every
  other native class in this package.
- **The other half of the win, not stopped at just compiling `SimulationClock` itself**:
  `native/_rp2040.pxd`/`.pyx` now hold `clock` as a `cdef public SimulationClock` typed field
  (cimported, not just imported) instead of a plain `__dict__` attribute, and
  `native/_simulator.pyx`'s `execute_batch()` binds it to a statically-typed local (`cdef
  SimulationClock clock = simulator.clock`) - so `clock.tick()` and the two properties resolve as
  direct C-level calls from the hot loop, not a Python attribute/method dispatch.
- `MockClock` (`clock/mock_clock.py`, a second `IClock` used only by tests) needed no changes - it
  already subclassed whatever `clock.simulation_clock.SimulationClock` the facade resolved to, and
  a plain Python class subclassing a non-`final` `cdef class` is standard, supported Cython/CPython
  behavior. Confirmed working with the native class active (tests/test_dma.py, tests/test_timer.py
  pass unchanged).

## Verified

- `uv run pre-commit run --all-files` (mypy, ruff, pytest on both pure-Python and native builds)
  clean - 572 passed / 1 skipped both `RP2040PY_SKIP_CYTHON=1` and native.
- Behavior identical between backends - the native port is a mechanical translation, not a redesign.

## Measured

**CPU governor was `powersave` for this whole session** (0027's own lesson about this exact trap -
no `sudo` access available to switch to `performance`, confirmed via `scaling_governor`). Absolute
throughput numbers below are therefore *not* directly comparable to any prior record's own numbers,
but the before/after pair was measured back-to-back on the same otherwise-idle machine under the
same governor, so the ratio is meaningful.

Synthetic benchmark (not the literal 0038 repro - see below for why): `Simulator._execute_batch()`
driven directly (no asyncio, per CLAUDE.md's own testing guidance), native `RP2040`, an infinite
self-branch instruction so every step hits `clock.tick()` on the exact hot path with `core.waiting`
always `False` - the same busy-spin shape 0038's `STALL` retry loop has, not an idle/waiting one.
Metric: simulated nanoseconds advanced per real second across 7 repeats of 40 batch calls each
(median):

- Before (native `RP2040`/`CortexM0Core`, plain-Python `SimulationClock` - i.e. 0034's own end
  state): **~71.4M simulated-ns/real-sec**.
- After (this port, `clock` typed through `_rp2040.pxd` and `_simulator.pyx`): **~190-192M
  simulated-ns/real-sec** - **~2.7x**.

**Not re-run this session: the literal 0038 repro** (`tests/micropython/main-cyw43.py`, real
`v1.28.0` UF2, `--board pico_w`, the ~450s/~450x baseline) - CI already bounds this at 15 minutes
(`.github/workflows/ci-micropython.yml`) and running it twice (before/after) plus rebuild time
didn't fit this session's budget. The ~2.7x synthetic figure above should not be read as "closes
0038's ~450x gap" - `clock.tick()` was *a* remaining Python-call cost on that path, not necessarily
the dominant one; whoever picks up the next round of this side quest should re-run the real repro
(same live-tracing/timing methodology 0038 itself used) to get the honest end-to-end number, ideally
after also fixing the `powersave` governor issue this record inherited.
