# 0040. `native/_simulator.pyx`'s hot loop: `time.monotonic()` and `float("inf")`, then `cimport`'d

- Status: `INFINITY` implemented — verified (2026-08-14). `cpython.time cimport monotonic`
  implemented, then reverted same day - broke real CI (see below); back to plain `time.monotonic()`.
- Recorded: 2026-08-13 · Implemented: 2026-08-14 · Reverted (monotonic only): 2026-08-14
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

## Reverted (2026-08-14) — `cpython.time` breaks real CI under this project's `Py_LIMITED_API` floor

The `monotonic()` swap above compiled and passed everywhere it was actually tested locally
(pure-Python and native-Cython `pytest`, CPython 3.10 - this project's dev default, see
`.python-version`) - but 3.10 is *below* this project's own abi3 floor (`_ABI3_FLOOR = (3, 11)` in
`setup.py`), so `USE_LIMITED_API` is `False` there and the build never actually exercises the
`Py_LIMITED_API` code path at all. Pushed anyway; real CI (`ci-micropython.yml`'s `cpython-3.14`
matrix entries, 9 of them) failed immediately:

```
src/rp2040py/native/_simulator.c:1156:32: error: unknown type name 'PyTime_t'
        #define __Pyx_PyTime_t PyTime_t
```

Root cause: `cpython/time.pxd`'s own generated C picks its branch based on the *actual compiler's*
Python version (`#if PY_VERSION_HEX >= 0x030d00b1 ... use PyTime_t/PyTime_MonotonicRaw`) - on
CPython 3.14 that condition is true, so it assumes the modern, *stable-ABI* `PyTime_t`/
`PyTime_MonotonicRaw()` symbols are available. They're not, here: this project compiles with
`-DPy_LIMITED_API=0x030B0000` (the 3.11 floor `setup.py`'s own docstring already explains, chosen
for typed-memoryview buffer-protocol support, unrelated to this) regardless of which real Python
version does the compiling - and `PyTime_t`/`PyTime_MonotonicRaw()` were only added to the
*limited* API surface in CPython 3.13, not 3.11. `Python.h` respects the requested
`Py_LIMITED_API` level, not the real interpreter version, when deciding what to declare - so 3.14's
own headers still hide those symbols when asked for the 3.11-level limited API, and Cython's
version check (based on the real compiler, not the requested API floor) has no way to know that.
`cpython-3.10` jobs in the same CI matrix never hit this: below the abi3 floor, `USE_LIMITED_API`
is `False` there too, so those compile against the *full* (non-limited) API directly, where the
symbols always exist for a 3.10+ compiler regardless of `PyTime_t`'s API-surface history.

This is the same *shape* of trap as the `posix.time`/Windows issue already caught before
implementing this (an assumption baked into upstream Cython code that doesn't hold for this
project's specific build configuration), just one layer deeper - portable *across platforms* isn't
the same guarantee as portable *across `Py_LIMITED_API` floors*, and nothing about `cpython.time`
signals that distinction up front.

Reverted `native/_simulator.pyx` back to `import time` / `time.monotonic()` at both call sites.
Verified against the *exact* failing condition, not just theory: built the extension locally
against a real CPython 3.14 interpreter with `Py_LIMITED_API=0x030B0000` (`setup.py build_ext`
directly, bypassing `uv sync`'s own Python-version selection) - failed with the identical
`PyTime_t` error before this revert, compiled clean after it. `INFINITY` (`libc.math`, a plain ISO
C99 macro with no CPython API-surface involvement at all) is unaffected and stays - not implicated
in the failure, not reverted.

A real fix that keeps the C-level `monotonic()` call *and* the `Py_LIMITED_API` floor at 3.11 would
need to branch at Cython-compile-time on `Py_LIMITED_API`'s own definedness (a genuine `cdef
extern from *:` verbatim-C case, unlike the plain arithmetic this file's own bit-twiddling
functions - see `_bit.pyx` - never needed one for) - not attempted, given the "not hot-path"
reasoning at the top of this record already established there's no measurable win being chased
here in the first place.

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

`from libc.math cimport INFINITY` added (originally alongside `cpython.time cimport monotonic`,
which was reverted above - this import stayed on its own); all three `float("inf")` sites in
`execute_batch()` replaced with the bare `INFINITY` constant. Unaffected by the revert above:
`libc.math` is a plain C99 header declaration, not a CPython C-API surface, so it carries no
`Py_LIMITED_API`-floor dependency at all.

## Verification

Final state (`time.monotonic()` restored, `INFINITY` kept): `uv run pre-commit run --all-files`
(mypy, ruff, pytest, both pure-Python and native-Cython builds - native extension rebuild confirmed
via `.so` mtime, not just a green pre-commit run, per 0044's own note about `uv sync` alone not
reliably rebuilding `.pyx` changes) passes clean, on CPython 3.10 (dev default) *and* built cleanly
against a real CPython 3.14 interpreter with `Py_LIMITED_API=0x030B0000` (see the revert section
above for how that was actually verified, not just asserted). Re-ran
`docs/tasks/main-spi-hang.md`'s own repro command as a smoke test - still exits 0.
