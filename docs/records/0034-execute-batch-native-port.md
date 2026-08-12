# 0034. `Simulator._execute_batch()` native Cython port

- Status: Implemented — measured (2026-08-12)
- Conceived: 2026-08-12
- Related: follow-up of 0013 (Cython core) and 0031 (PIO Cython + tick batching) · found while
  investigating a performance complaint about LittleFS, `utime.sleep_ms()`, and CYW43 boot time

## Context

0013's own "not yet tried" list identified `_execute_batch()`'s per-instruction dispatch/idle loop
(`simulator.py`) as the single biggest remaining interpretation cost - confirmed, not just
theorized, at ~43-47% of profiled time even with a fully native CPU core/bus: only the instruction
*handler* (`CortexM0Core.execute_instruction()`) was ever ported, not the `while` loop in
`Simulator._execute_batch()` that drives it. Picked up here after a live profiling session (pure
Python, `RP2040PY_SKIP_CYTHON=1`) reproduced this exact shape on three real user complaints
(LittleFS flash read/write, `utime.sleep_ms(200)`, CYW43 boot-to-REPL) - `cProfile` on
`tests/micropython/main-sleepms.py` showed cost spread evenly across `execute_instruction`,
`read_uint16/32`, `write_uint32`, `u32()`, `struct.unpack_from`, the same interpreter-loop shape
0013/0031 already characterized, not anything specific to LittleFS or `sleep_ms`.

**Ruled out before starting, from the tracker's own history:** a second round of pure-Python
*runtime-check* tricks in the hot loop. 0015 (HLE memcpy hook) and 0016 (JIT/basic-block fusion,
three separate attempts) all measured **net negative**, because a per-instruction check's fixed
cost isn't repaid unless the accelerated code is a huge share of total execution - confirmed
independently three different ways in 0016 alone (inlined check, branch-only check, targeting a
different loop). What worked instead, consistently: whole-loop/whole-module **ahead-of-time Cython
compilation with zero runtime branching** (0013, 0031). This record follows that same lever, not
the ruled-out one - "pure Python first" was honored as the *investigation methodology* (profile and
confirm the bottleneck in pure Python before touching Cython), not as "ship a pure-Python-only
fix."

## Decision

Port `_execute_batch()`'s own loop control flow to native Cython, mirroring the existing
`peripherals/state_machine.py`/`peripherals/_state_machine.py`/`native/_state_machine.pyx` facade
split exactly (not an ad hoc inline `try`/`except` in `simulator.py` - an earlier draft of this
work did that and was corrected mid-session):

- `src/rp2040py/_execute_batch.py` - the pure-Python reference, the exact previous body of
  `Simulator._execute_batch()` moved verbatim into a standalone `execute_batch(simulator,
  tick_batch)` function (kept as the actual source of truth; the native version is a hand-kept-in-
  sync translation, same relationship `_state_machine.py`/`native/_state_machine.pyx` already have).
- `src/rp2040py/execute_batch.py` - the public facade (`try: from rp2040py.native._simulator import
  execute_batch except ImportError: from rp2040py._execute_batch import execute_batch`), reusing
  `rp2040py._native_gate.native_disabled()` directly. `simulator.py` imports `execute_batch` from
  here only - never from `_execute_batch.py` or `rp2040py.native` directly, matching every other
  facade's own stated contract.
- `src/rp2040py/native/_simulator.pyx` - the native port. `RP2040`/`CortexM0Core` are cimported and
  typed via their existing `.pxd`s, so `core.execute_instruction()`/`rp2040.core` resolve to direct
  C calls/field reads. **`clock` (`SimulationClock`, `clock/simulation_clock.py`) stays untouched
  and un-ported** - even native `RP2040` holds it as a plain Python object (`native/_rp2040.pyx`
  imports it directly from `clock.simulation_clock`) - so `clock.tick()` and its two properties
  stay ordinary Python calls from within the native loop, the same accepted boundary
  `StateMachine.rp2040.gpio`/`.dma` staying `object`-typed already established (0031's own "not yet
  tried" #4). `simulator.stopped` is likewise read via plain Python attribute access every
  iteration, for the same reason: it must be observable mid-batch from another thread calling
  `stop()`, and Simulator itself is (deliberately) not natively typed.
- `Simulator._execute_batch()` is now a two-line wrapper calling the facade; the constants and long
  design-rationale comments (`_BATCH_YIELD_BUDGET_SECONDS`, `_TIME_CHECK_INTERVAL`, the WFI-jump/
  idle-batching discussion) moved to `_execute_batch.py`, next to the code they actually describe.

## Verified

- `uv run pre-commit run --all-files` (mypy, ruff, pytest on both pure-Python and native builds)
  clean.
- Behavior identical between backends (full 563/564-test suite passes both `RP2040PY_SKIP_CYTHON=1`
  and native) - the native port is a byte-for-byte control-flow translation, not a redesign.

## Measured (native, `RP2040PY_SKIP_CYTHON=0`, same machine, before vs. after this port)

- `utime.sleep_ms(200)` (`tests/micropython/main-sleepms.py`, MicroPython 1.21 + littlefs):
  **4.4s → 3.6s**.
- LittleFS flash read/write (`tests/micropython/main-flash-rw.py`, one write + one read through a
  pre-built littlefs image): **1.2s → 0.35s (~3.4x)**.

## Not resolved by this port - CYW43 boot-to-REPL

Re-ran `tests/micropython/main-cyw43.py` boot-to-REPL (real `v1.28.0` UF2, `--board pico_w`) with
this port active: still did not reach the REPL within a 9-minute bound (`real 9m0.038s`), no
improvement over the pre-port baseline. Investigated why - **this uncovered a separate, unresolved
issue, not a throughput problem this port could have fixed**. See 0027's own dated entry
(2026-08-12, "Performance side quest, continued") for the finding: profiling and address-tracing
showed the CPU going into what looks like wild/invalid execution (987K distinct addresses touched
in 5 seconds, spanning `0x0041fd80` to `0xffffffff`) reproducibly on *any* script on the `pico_w`
board with this exact `v1.28.0` UF2 - including a trivial `print("hi")` with no `network` import at
all, and reproducing identically whether `Cyw43439`/`GSPIBus` is attached in `boards.py` or not.
Narrowed by elimination to: specific to the `RPI_PICO_W` (not plain `RPI_PICO`) `v1.28.0` firmware
image itself - the same trivial script against the plain `RPI_PICO` `v1.28.0` UF2 does not
reproduce it. Root cause not yet identified (would need symbol-matched disassembly of the firmware
around the crash PC, e.g. via a local build of the matching `micropython` v1.28.0 checkout - not
done here). **Not something this record's own change caused** - reproduces identically
(proportionally, since pure-Python executes fewer total instructions in the same window) in
`RP2040PY_SKIP_CYTHON=1` mode too, which this port never touches.
