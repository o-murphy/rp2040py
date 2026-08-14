# 0040. `native/_simulator.pyx`'s hot loop: `time.monotonic()` and `float("inf")`, then `cimport`'d

- Status: Implemented — verified (2026-08-14)
- Recorded: 2026-08-13 · Implemented: 2026-08-14
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

## Implemented (2026-08-14)

The portability objection above assumed the only `cimport`-able option was a raw platform syscall
(`posix.time`'s `clock_gettime`/`CLOCK_MONOTONIC` - POSIX-only, would not even compile on the
Windows targets `publish.yml` actually builds wheels for, confirmed by checking that workflow's
own build matrix before implementing this). That's not the only option: Cython bundles
`cpython.time`, a thin `cimport`-able wrapper around **CPython's own internal**
`_PyTime_GetMonotonicClock()`/`PyTime_MonotonicRaw()` C API - the exact same per-platform
dispatch `time.monotonic()` itself already uses (`clock_gettime(CLOCK_MONOTONIC)` on Linux,
`mach_absolute_time` on macOS, `QueryPerformanceCounter` on Windows, whatever Emscripten/Android
provide), just reached as a direct `nogil` C call instead of a Python-level attribute lookup and
call. No platform-conditional code needed - the dispatch CPython already maintains is inherited
for free, resolving the "Portability" objection above without reintroducing it.

`native/_simulator.pyx` now does `from cpython.time cimport monotonic` and calls `monotonic()`
directly in place of the two `time.monotonic()` call sites (batch-start timestamp, yield-budget
check). The "not hot-path" reasoning above is still correct on its own terms - at one call per 256
iterations this was never going to be measurable - so this was implemented as a genuinely free
correctness/consistency win (matching how `RP2040`/`CortexM0Core`/`SimulationClock` are already
`cimport`'d in the same file) once the portability blocker turned out to have a real fix, not
because the performance calculus changed.

## Implemented (2026-08-14) — `INFINITY` from `libc.math`

Found while reviewing the `monotonic()` diff above: `execute_batch()`'s `nanos_budget`
(`cdef double`) used `float("inf")` as its "no scheduled alarm, no ceiling" sentinel, at three call
sites - one on every loop iteration where `core.waiting` and `tick_batch > 1` (a CPU-idle/WFI spin
can hit this every single iteration for a long idle stretch) and one on every `tick_batch`-threshold
rollover in the busy-instruction path. Unlike `time.monotonic()` above (gated behind
`_TIME_CHECK_INTERVAL`, correctly judged not hot-path), these three sites are unconditionally on
the per-instruction hot path 0034/0039 already exist to keep C-native - `float("inf")` is a
Python-level call (attribute lookup on the `float` builtin, object construction) immediately
narrowed back down to the `cdef double`, pure overhead for a compile-time-constant value.

`libc.math.INFINITY` is Cython's bundled declaration of the ISO C99/C++11 `<math.h>` macro - not a
POSIX-only extension (unlike the `posix.time` trap above), so no platform-conditional code is
needed here either: every target this repo builds wheels for (`publish.yml`: Linux, macOS,
Windows, Android) has a C99-conforming `<math.h>`.

`from libc.math cimport INFINITY` added alongside `cpython.time cimport monotonic`; all three
`float("inf")` sites in `execute_batch()` replaced with the bare `INFINITY` constant.

## Verification

`uv run pre-commit run --all-files` (mypy, ruff, pytest, both pure-Python and native-Cython
builds - native extension rebuild confirmed via `.so` mtime, not just a green pre-commit run, per
0044's own note about `uv sync` alone not reliably rebuilding `.pyx` changes) passes clean. Re-ran
`docs/tasks/main-spi-hang.md`'s own repro command as a smoke test - still exits 0.
