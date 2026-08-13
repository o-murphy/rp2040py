# Task: native/Cython port of `SimulationClock`

Not a `docs/records/` entry - a working note for whoever picks up the next round of the
"Performance side quest" (0013 → 0031 → 0034, most recently motivating 0038's own leftover
finding). See those records for the historical wins this follows the same pattern as.

## Why this, why now

`native/_simulator.pyx`'s own module docstring already flags this explicitly as the one piece of
the per-instruction hot path never natively ported: `clock` (`clock/simulation_clock.py`) stays a
plain Python object even under the otherwise-fully-native `execute_batch()` - `clock.tick()` and
its properties (`nanos_to_next_alarm`, `has_scheduled_alarm`) are ordinary Python calls on every
single simulated CPU instruction, paying full interpreter call overhead for a body that's a handful
of attribute reads/comparisons.

0038 (2026-08-13) is the most recent concrete motivation: after fixing a real correctness bug that
was masking this, `nic.active(True)` on `v1.28.0` still costs ~450s real time for what the driver's
own logic bounds to ~1 *simulated* second per `STALL` retry - i.e. real-vs-simulated time is off by
roughly 450x for that specific loop, entirely a per-instruction interpretation-throughput problem,
not a scheduling or correctness one (both of those were separately ruled out - see 0037 and 0038).
`SimulationClock.tick()` is called unconditionally on every instruction in that loop and is the
last unported piece of that path.

## Shape of the change

`clock/simulation_clock.py` is small and mostly self-contained (107 lines): `SimulationClock`
(`nanos`, `micros`, `create_alarm()`, `link_alarm()`, `unlink_alarm()`, `tick()`,
`nanos_to_next_alarm`, `has_scheduled_alarm`) and `ClockAlarm` (`schedule()`, `cancel()`, plus
internal `next`/`nanos`/`scheduled`/`callback` fields only ever touched from inside
`simulation_clock.py` itself - confirmed via `grep` this session, no external code pokes
`ClockAlarm` internals directly).

External callers only ever use the `IClock`/`IAlarm` protocol shape (`clock/clock.py`):
`create_alarm(callback) -> IAlarm`, `alarm.schedule(delta_nanos)`, `alarm.cancel()`, plus
`SimulationClock`'s own extra `nanos`/`micros`/`tick()`/`nanos_to_next_alarm`/`has_scheduled_alarm`
that `_execute_batch.py`/`native/_simulator.pyx` call directly (not through the `IClock` protocol,
which only requires `nanos`/`create_alarm()`). Every peripheral that schedules alarms
(`peripherals/dma.py`, `timer.py`, `adc.py`, `usb.py`, `utils/timer32.py`, `device/mp_device.py`,
`cli/process_repl.py`) only calls `create_alarm()`/`schedule()`/`cancel()` - none of them reach into
`ClockAlarm`'s internals. This makes the port lower-risk than it might look: the alarm linked-list
state is genuinely private to this one file.

Follow the pattern `native/_state_machine.py`/`.pyx`/`.pxd` already established (see
`docs/records/0013-cython-core.md`/`0031-pio-cython-tick-batching.md`): a `cdef class` with `cdef
public` typed fields, paired `.pxd` declaration, `setup.py` already globs `native/*.pyx`
automatically (no manual registration needed - confirmed this session).

## Real complications, not hypothetical

- **`MockClock`** (`clock/mock_clock.py`) is a second `IClock` implementation used by tests - stays
  pure Python regardless (it's not on the hot path), but make sure nothing about the port assumes
  `SimulationClock` is the *only* `IClock`, since `RP2040.__init__(clock: IClock | None = None)`
  accepts either.
- **`native/_rp2040.pyx`** currently imports `SimulationClock` directly and holds it as a plain
  `object` field (`self.clock = clock if clock is not None else SimulationClock()`,
  `native/_rp2040.pyx:116`) - a native `SimulationClock` would let this become a properly `cdef
  public SimulationClock clock` typed field instead, which is the other half of the win (native
  `RP2040`/`CortexM0Core` calling into native `SimulationClock` directly, not through a Python
  object boundary) - don't stop at just compiling `SimulationClock` itself, wire the typed field
  through too or most of the benefit is left on the table.
- **Measure honestly** - 0027's own "Same-day follow-up" section has a whole non-technical lesson
  about a CPU frequency governor (`powersave`) silently invalidating a comparison for hours. Check
  `scaling_governor`/`scaling_cur_freq` before trusting any before/after number.
- Use `rp2040py bench` and the exact 0038 repro (`tests/micropython/main-cyw43.py` against a local
  `v1.28.0` debug build, timed) as the concrete before/after - it's a real, reproducible, already-
  measured baseline (~450s native today) rather than a synthetic microbenchmark.
