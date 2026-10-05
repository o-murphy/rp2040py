# 0097. A Pyodide/WASM wheel for rp2040py: root-caused and fixed

- Status: Implemented - `[tool.cibuildwheel.pyodide]` (`pyproject.toml`) and the "Pyodide
  WebAssembly" row in `publish.yml`'s build matrix are both enabled, running the full test suite.
- Conceived: 2026-10-05
- Related: [0096](0096-cpp-mcu-core.md) (the C++ MCU core and its thread-free engine - what this
  record actually exercises under Emscripten; its own design note had already named the leak this
  record found as a risk, "a dropped chip with alarms pending would leak 16 MiB of flash")

## Context

`pyproject.toml` had a long-commented-out `[tool.cibuildwheel.pyodide]` stub ("pthread is not
allowed"), dating from before record 0096's thread-free engine existed - at the time, the
Simulator's engine room needed a real OS thread, and Emscripten/WASM has none. 0096 fixed that
generally (the engine now runs thread-free by default, driven by `pump()`/`await` instead of a
background thread - see `docs/reference/threading-model.md`), which raised the question of whether
the old "pthread is not allowed" blocker was still the reason pyodide was disabled, or whether it
was now stale. The user asked to actually try building and running under pyodide rather than
reason about it.

py-ballisticcalc (`~/proj/py-ballisticcalc`, cited elsewhere in this repo's `setup.py` as the
pattern this project's native-extension build mirrors) already ships a working
`[tool.cibuildwheel.pyodide]` section with no test-skip override, which is why this investigation
followed its exact shape: `build = "cp313-pyodide_* cp314-pyodide_* cp315-pyodide_*"`,
`test-extras = ""`, and cibuildwheel's own pyodide test runner (not a hand-rolled one - an earlier
hand-rolled node+micropip smoke script hit two self-inflicted bugs first: writing the wheel under
a generic filename broke micropip's PEP 427 filename parsing, and piping `pytest` through `tee`
swallowed its real exit code via the shell's last-command-wins pipeline status).

## What was measured

All of this is on `cp314-pyodide` (Pyodide 314.0.7, Node v24, `cibuildwheel` 4.3.0):

1. **The build itself works.** `cp313-pyodide`, `cp314-pyodide`, and `cp315-pyodide` (pyodide
   315.0.0a2 - alpha, unverified beyond compiling) all compile the C++17 core and every Cython
   module for `wasm32-emscripten` with no build errors. Confirmed via cibuildwheel's own
   `resources/build-platforms.toml` pins (real `python.org`/chaquopy URLs + sha256, not a guess) -
   the same check used to confirm `cp315-android_*` (also added this session) is real rather than
   speculative.
2. **The native extension actually loads under wasm, and the thread-free engine runs correctly.**
   A standalone node+Pyodide smoke test (not part of the test suite) confirmed: `rp2040py.native`
   imports with no pure-Python fallback warning; a hand-written Thumb program (`MOVS r0,#0x55`;
   `MOVS r1,#0x07`) executes correctly through the native C++ core; `Simulator()` defaults to
   `threadless=True` here as everywhere else; and a real `bind_loop()` + `pump()` cycle drives
   `execute()` with zero new OS threads spawned (checked via `threading.enumerate()` before/after) -
   i.e. record 0096's actual fix for "WASM has no pthreads" demonstrably works in a real,
   thread-incapable sandbox, not just in theory.
3. **The full suite initially crashed with no Python traceback, reproducibly, always at the same
   position.** `python -m pytest {project}/tests` (minus `test-extras = "fs"` - see below) died
   around 15% in, at the same point (`test_cli.py`'s 5th test) across three separate runs on two
   code revisions, regardless of `-v`/`-rA`/buffered/unbuffered output. Two things ruled out
   simpler explanations before the real cause was found:
   - **Not a cibuildwheel artifact.** Reproduced identically running pytest directly inside a raw
     node+Pyodide session (`pyodide.mountNodeFS` + micropip, no cibuildwheel involved at all) - the
     same crash, at the same position.
   - **Not that specific test's content.** `--deselect`ing the exact crashing test did not get past
     it - the suite still died at the same *position*, on whichever test happened to land there
     instead. That pointed at an accumulating resource limit rather than a bug in one test.
4. **Root cause (found in parallel by another Claude session working the same branch,
   commit `4c6f913`): a native reference cycle the Python garbage collector could not see.** Each
   block held its `Clock` through a raw `Py_INCREF` invisible to the cyclic GC, while the clock
   reached back into every block with an armed alarm - so a block with a pending alarm, and the
   ~16 MB chip around it, could never be collected. One leaked chip per test exhausted the 32-bit
   address space `wasm32` shares with CI's other 32-bit targets (ARMv7, Windows x86) long before a
   64-bit build would ever notice. Fixed by having each alarm remember its clock and the clock
   release its alarms on destruction, so neither side needs to keep the other alive. A companion
   fix (`c68f408`) added an autouse `gc.collect()` between tests specifically on 32-bit builds
   (`tests/conftest.py`), since even an *ordinary*, collectible cycle only frees on the next cyclic
   GC pass, which 64-bit builds can afford to defer and 32-bit/wasm builds cannot.
5. **After the fix, the full suite passes.** `[tool.cibuildwheel.pyodide]`'s `test-command` is now
   `python -m pytest {project}/tests`, matching every other platform, in the same commit that
   re-enabled it (`740652c`).

## Decision

Both `[tool.cibuildwheel.pyodide]` (`pyproject.toml`) and the "Pyodide WebAssembly" row
(`publish.yml`'s `build-wheels` matrix) are enabled, running the whole test suite like every other
platform - no narrowed `test-command`, no `test-skip`. `littlefs-python` stays excluded via
`test-extras = ""` (see Known gaps); that is a real, separate dependency gap, not a sign anything
else here is untrustworthy.

## Known gaps / Still open

- **`littlefs-python` has no wasm32/emscripten wheel.** It is architecturally no different from
  this project's own native extension (Cython wrapping a small vendored C library) - proven
  buildable for wasm32 in principle - but upstream doesn't configure or publish that build. Getting
  it would mean a patch/PR upstream, not something owned here. `tests/test_cli_mklittlefs.py`
  already guards correctly against its absence (`pytest.importorskip("littlefs", ...)`).
- **`cp315-pyodide` is unverified beyond "it compiles".** Pyodide 315.0.0a2 is alpha; the
  measurements above (points 2-3) predate the fix and were taken on `cp314-pyodide` specifically -
  worth a fresh pass on `cp315-pyodide` once it leaves alpha.
- **Phase 6 of record 0096 (the WASM *host* - `core_run(budget)` driven from JS) is a different,
  unrelated piece of work.** This record is about whether rp2040py's own package builds and runs
  as an ordinary (non-host) wasm32 wheel; it says nothing about Phase 6 and does not advance it.
