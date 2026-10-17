# 0103 - a wasm backend: toolchain, packaging, backend selection and testing

Status: in progress (opened 2026-10-17). **The headless proof of concept is built (2026-10-17); the Python layer (`RP2040PY_BACKEND`, `--backend`) is not.** The decisions below are the maintainer's. Companion to [0102](0102-c-abi-for-the-core.md) (the C surface the module exports) and the Phase 6 of [0096](0096-cpp-mcu-core.md). Per `CLAUDE.md` a note about what should eventually happen is not a go-ahead to implement it; the proof of concept below needs its own "yes".

## What is proposed

A third backend of the Python package, next to the Cython one and the pure-Python one: the C++ core compiled once to a `.wasm` module, run from Python by `wasmhost`, so that a platform without a compiled wheel still gets the fast core (as far as a WebAssembly engine is there). Backend order, with an override: **native (Cython) -> wasm -> python**.

## Decisions (the maintainer's, 2026-10-17)

1. **Toolchain: `ziglang`** (`zig c++ -target wasm32-wasi`), as `tiny-bclibc-wasm` does. It is a **build** requirement (`[build-system].requires`), not a runtime one: the `.wasm` is compiled during the wheel build.
2. **Target: `wasm32-wasi` with `-mexec-model=reactor -Wl,--no-entry`** (a library, no `_start`), not `wasm32-freestanding`. See "Measured" - the sizes are within 1-4 % of each other and reactor needs no header shim.
3. **Packaging: the `.wasm` goes into every wheel** (`package-data`), built by a `build_wasm.py` called from `setup.py`'s `build_py` - the layout of `tiny-bclibc-wasm` (`build_wasm.py`, `setup.py`, `MANIFEST.in` with `global-exclude *.wasm` so the sdist stays source-only). `publish.yml` is **not** changed by this record; any edit to it needs its own confirmation.
4. **Host: `wasmhost`** (the maintainer's own, `ballistics-lab/py-wasmhost`, pure Python, no dependencies; backends JavaScriptCore, Node, Bun, and wasmtime / wasm3 when installed).
5. **Scope of the Python API: full parity is the goal, a headless subset is the proof of concept.** "Headless" = load a firmware, run, UART in/out, pins, ADC, reset, time - what `Simulator`, the external devices and the boards use (the chip attributes they touch: `clock`, `gpio[]`, `spi[]`/`qspi[]`, `schedule_threadsafe`, `run_pin_low`, `on_break`, `simulator`, `reset`). Parity is expected to come in under the ~10,000 lines of the Cython shells (their C-pointer layer and trampolines do not exist on this side).
6. **Backend selection: `RP2040PY_BACKEND=auto|native|wasm|python`**, default `auto`. `RP2040PY_SKIP_CYTHON=1` stays as an alias of `python` and `RP2040PY_REQUIRE_NATIVE` as it is (about fifty uses, and both pre-commit runs hang on them). `auto` falls down quietly (an import of `_rp2040` that fails -> wasmhost with a usable engine -> pure Python); an explicit `native` or `wasm` fails loudly.
7. **Testing per backend through `tests/conftest.py`**, like the `--oracle` tiers: `--backend` (or `RP2040PY_BACKEND`) with the loaded backend asserted (a requested backend that did not load *fails*, not skips - the generalisation of `RP2040PY_REQUIRE_NATIVE`), a marker `@pytest.mark.backend("native")` for what only one backend can show (the Cython shells), the active backend in the report header. The backend is chosen at import, so the whole suite runs once per backend (a hook each in pre-commit, a matrix entry in CI - the wasm one with `--oracle=smoke`); a second level crosses backends inside one process (`chip_pair.py` and the `*_diff.py` oracles get the wasm chip as a third side). The C++ unit tests (`tests/cpp`) are to be built for `wasm32-wasi` and run under `wasmhost`'s WASI too: it is the cheap check that the core is right on a 32-bit address space.

## Measured (2026-10-17, `zig 0.17.0` from PyPI `ziglang`, then re-checked with the pinned 0.16.0 - see risk 5; no source changed)

- The core compiles to wasm32. With `clang --target=wasm32-unknown-unknown -ffreestanding` it needs a five-file shim (`cstdint cstddef cstring cmath limits`); `<cstdint>` is not found without it. With `zig c++ -target wasm32-wasi` it needs nothing: zig brings its own libc and libc++ headers.
- A real link of `Mcu::build` + `Mcu::run` (nearly the whole core), `-Wl,--gc-sections -s`, one `.cpp` with two exports:

  | | freestanding + shim | wasi reactor |
  |---|---|---|
  | `-O2` | 148,390 bytes | 154,194 bytes |
  | `-Oz` | 112,346 bytes | 113,451 bytes |

  With the pinned `zig 0.16.0`, `-O2`: 148,781 bytes freestanding, 156,378 wasi reactor (the same ~5 %; both link, zig builds its libc++ on the way).

  **Both import nothing** (the import section is empty; what reactor brings of WASI is garbage-collected). Freestanding also needed `-fno-use-cxa-atexit` and a stub `atexit` (the static `Mcu`'s destructor). The reactor build has none of that, hence decision 2.
- The core uses `<cstdint>`, `<cstring>` (memcpy), `<cmath>` (`floor`, `ceil` - single wasm instructions), `<limits>` and `<cstddef>`; no malloc, no stdio, no exceptions, no threads, no `long double`.
- `tiny-bclibc-wasm` (read, not built): `build_wasm.py` compiles with `python -m ziglang cc -target wasm32-wasi -O2 -mexec-model=reactor -Wl,--no-entry -s`, **checks that the module imports nothing**, and `setup.py` runs it from `build_py` on every wheel build, sdist -> wheel and editable install alike; one `py3-none-any` wheel; `ziglang>=0.16.0` in `[build-system].requires`; `wasmhost` a dependency and `wasmtime` an extra.

## What differs from `tiny-bclibc-wasm`

Its module is pure computation and imports nothing. This core must call the host: the UART byte, the pins, the time, the logger, a failure. The invariant here is **"imports only our own small, fixed set of host calls and nothing from `wasi_snapshot_preview1`"** - so no engine needs a WASI implementation to run it. How those calls look (one trampoline import with a kind, or one import per callback) is the open question 1 of 0102.

## Risks and what is not known

1. **The Python layer is the work, not the compile.** The 27 Cython shells (about 10,800 lines) are views on C++ objects by pointer; the wasm side needs views over exports and linear memory. The headless subset first keeps that honest.
2. **`auto` must not hand out a smaller API silently.** On a wasm backend with only the headless subset, code that reaches `uart._ctrl_register` fails with `AttributeError` where native works. Until parity, the subset is the documented contract of every backend and the rest is marked native / python only (the `backend` marker above).
3. **Speed is unmeasured.** The instruction loop runs inside the module; what crosses the boundary is the edges (a UART byte, a pin, time). With Node as the engine a crossing may be a process hop. To be measured with the existing CoreMark script before any API decision is built on it.
4. **Engines are not everywhere.** `wasmhost` needs JavaScriptCore, Node, Bun, wasmtime or wasm3 on the machine; without one `auto` goes to pure Python. Pyodide cannot nest an engine, but has its own Cython wheel ([0097](0097-pyodide-wasm-wheel.md)).
5. **Cost of the build dependency, and a pin.** `ziglang` is a ~80 MB wheel per platform in every build isolation (every cibuildwheel job). It is in the dev group only and unused; when the `build_wasm` hook lands it moves to `[build-system].requires` in the same commit (the maintainer's option B). **Pin it below 0.17:** `ziglang 0.17.0` on PyPI (2026-10-08) ships six wheels and none for Windows, so `uv sync` failed on `windows-latest` (`Pre-commit` on `e576fa0`, which had `ziglang>=0.17.0`); 0.16.0 and the releases before it have `win32`, `win_amd64` and `win_arm64`. The dev dependency is `ziglang>=0.16.0,<0.17` until a release has a Windows wheel again. `tiny-bclibc-wasm` says `ziglang>=0.16.0`, which resolves to 0.17.0 and would fail the same way on Windows.
6. **Threads.** The module is single-threaded; `request_stop` across threads needs shared memory and atomics (0102) and is not required for the first version.

## Order, once there is a go-ahead (nothing started)

1. **Proof of concept (headless):** the C ABI of 0102 reduced to what a boot needs, `build_wasm.py`, and a test "MicroPython prints over UART0 under `wasmhost`" (gate G1 on wasm), with the import check.
2. **Measure:** CoreMark on the three backends with `scripts/bench/`.
3. **Then decide** the Python layer (parity plan), `RP2040PY_BACKEND`, `--backend` in `conftest.py`, the packaging hook, and the move of `ziglang` into `[build-system].requires`.

## Progress log

- 2026-10-17: proposed (documentation only), from the maintainer's concepts and the measurements above.
- 2026-10-17: **The headless proof of concept, with the packaging hook.** *The module:* `src/rp2040py/native/wasm/rp2040_wasm.cpp` is a flat C ABI around `Mcu` (the reduced form of 0102: `rp2040_rom/flash/uart` pointers into the module's own memory, `rp2040_erase_flash`, `rp2040_boot`, `rp2040_run`, the UART0 bytes and the warning count out); `uart_byte` is answered inside the module, so it **imports nothing** (169 KB, zig 0.16.0, `-O2`, wasm32-wasi reactor). Its place in the tree (`native/wasm/`, a sibling of `core/`) is a call to confirm: 0102 open question 4. *The build:* `build_wasm.py` (plain Python: the core's flags, `python -m ziglang c++`, `RP2040PY_WASM_CXX` to override, a check that the result imports nothing - the pattern of tiny-bclibc-wasm). `setup.py`'s `build_py` runs it before packaging, so the module is package data (`rp2040py.native.wasm/*.wasm`, git-ignored) of every wheel; `ziglang>=0.16.0,<0.17` moved into `[build-system].requires` (it stays in the dev group for the tests); `[tool.uv].cache-keys` rebuild an editable install when the sources change. Unlike tiny-bclibc-wasm the hook is **soft** by default (a missing `ziglang` or a failed build warns and the wheel is made without the module, as for the Cython extension); `RP2040PY_REQUIRE_WASM=1` makes it fail, `RP2040PY_SKIP_WASM_BUILD=1` skips it. *The tests:* `tests/test_wasm_core.py` (a hand-assembled Thumb program writes `HI!\n` to UART0; the module imports nothing; no warning) and `tests/test_gate_g1_wasm.py` (gate G1 on wasm: the stock MicroPython 1.29.0 prints the same line as `test_gate_g1.py`, 2.0 million instructions, ~0.2 s on Node), both on whatever engine `wasmhost` finds; the module comes from the installed package, else from `build_wasm.py`. *Measured here:* the wheel builds with the new `requires` under cibuildwheel for **Pyodide** (cp313) and **Android** (cp313, x86_64) - `ziglang` is installed on the Linux build host in both and the module is in the wheel (checked in the Pyodide wheel once installed); `tests/test_wasm_core.py` **passes inside Pyodide** (2 passed). *Not measured:* the Android test phase (it runs in an emulator; this sandbox has no KVM) - and it is not needed there: Android runs the Cython extension, the wasm module is only a fallback in the order `cython -> wasm -> python`, and `wasmhost` has no engine on Android. Neither platform's `test-requires` carries `wasmhost`, so the wasm tests skip there in the normal suite. Next: the CoreMark comparison, then the Python layer.
