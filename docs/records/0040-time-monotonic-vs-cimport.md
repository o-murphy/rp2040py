# 0040. Note — `time.monotonic()` left as a plain Python call in `native/_simulator.pyx`, not `cimport`'d

- Status: Note
- Recorded: 2026-08-13
- Related: 0031 (PIO Cython + `clock.tick()` batching), 0034 (`_execute_batch()` native port), 0039
  (`SimulationClock` native Cython port)

`native/_simulator.pyx`'s batch loop calls the ordinary Python `time.monotonic()` (`import time`,
plain attribute call) for its real-time yield-budget check, rather than `cimport`-ing a C-level
clock (e.g. Cython's own bundled `posix/time.pxd` declaring `clock_gettime`/`CLOCK_MONOTONIC`)
the way `RP2040`/`CortexM0Core`/`SimulationClock` are themselves `cimport`'d as native types in
the same file. Raised as a question during unrelated work on this file; answered and recorded
here rather than left to be re-asked.

Two reasons this stayed a plain Python call, not a swap to a C-level one:

- **Not hot-path.** Unlike `clock.tick()` (called unconditionally on every single simulated
  instruction - the reason 0034/0039 exist), this check is gated behind `_TIME_CHECK_INTERVAL`
  (256) - `_execute_batch.py`'s own reference implementation says as much in its module docstring:
  "time.monotonic() itself must not become the hot-path cost this is trying to avoid" is already
  the design constraint, met by calling it rarely rather than by making each individual call
  cheaper. At one call per 256 iterations, the per-call Python-level overhead this would shave off
  is not a measurable fraction of a batch's cost, unlike `clock.tick()` where per-instruction
  overhead is the entire point.
- **Portability.** This repo builds native Cython extensions for non-POSIX targets too
  (`build/lib.emscripten_*-wasm32-*`, `build/lib.android-*` are both present in-tree) where a
  direct `posix.time` `cimport` either doesn't apply or needs a separate code path. `time.monotonic()`
  is the portable abstraction CPython itself already maintains per-platform (`clock_gettime` on
  Linux, `mach_absolute_time` on macOS, `QueryPerformanceCounter` on Windows, and whatever
  Emscripten/Android provide) - reimplementing that dispatch via a raw `cimport` for a call this
  infrequent would add platform-conditional code for no measurable benefit.

Not a closed door: if `_TIME_CHECK_INTERVAL` is ever lowered enough to make this check
meaningfully hot, the calculus above changes and a `cimport`'d clock would be worth relitigating -
but as of this note, doing so would be premature optimization against a cost that isn't real yet.
