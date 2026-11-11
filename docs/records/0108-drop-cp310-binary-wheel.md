# 0108 - no binary wheel for CPython 3.10

Status: **implemented (2026-11-11), branch `drop-cp310-wheel`, against `main`.** Asked for by the maintainer for the next release: stop publishing the compiled wheel for CPython 3.10. `requires-python` stays `>=3.10`; what changes is only which artifact a 3.10 interpreter gets.

## Why

- **CPython 3.10 reached end of life in October 2026.**
- **It is the one interpreter that gets its own non-abi3 wheel.** `cp311-*` builds once and tags itself `cp311-abi3` (it covers every 3.11+ interpreter, [0013]); the free-threaded builds are separate for the usual reason (`Py_LIMITED_API` and `Py_GIL_DISABLED` are incompatible). `cp310-*` cannot join the abi3 wheel: the buffer-protocol part of the limited API, which the typed memoryviews need, only exists from 3.11 (`setup.py`, `pyproject.toml`). So a 3.10 wheel is a fourth full build, plus its own run of the whole test suite, on every platform.
- **That fourth build is a quarter of each wheel job.** In the `publish.yml` run `42999201619` a Linux x64/x86 job built 12 wheels one after another (4 Python variants x manylinux x86_64, manylinux i686, musllinux x86_64), at about 3-4 minutes each (about 1.5 min to compile, 1-2 min for the 1733 tests, longer on i686). Dropping `cp310` removes one of the four variants everywhere.

## What changes for a 3.10 user

`pip install rp2040py` on 3.10 finds no compatible binary wheel and takes the **pure-Python wheel** the release already publishes (`build-pure`, `RP2040PY_SKIP_NATIVE_BUILD=1`, a `py3-none-any` wheel). Behaviour is identical (the pure-Python blocks are the reference the native ones are held to by the lockstep oracles), but the speed is not: the README's own figure for the MicroPython 1.28 boot is ~0.02 s with the native extension against ~1.9 s pure, and the long-run table puts the native extension at ~167x pure CPython.

**The maintainer's plan for the slow case is the wasm backend** ([0103]): a platform without a compiled wheel runs the C++ core as a `.wasm` module under `wasmhost` (`RP2040PY_BACKEND=auto` falls to it before the pure-Python one, when an engine - JavaScriptCore, Node, Bun, wasmtime, wasm3 - is on the machine). The expectation is that 3.10 will then be covered by it and the pure-Python slowness will not be what a 3.10 user sees. This is **an expectation, not something checked here**: 0103 is still in progress, and neither that a 3.10 install picks the wasm backend by default nor its speed there has been measured for this record (0103's own bench puts the module at about 60-70 % of Cython on a CPU-bound loop, on Node).

A 3.10 user who wants the native build can still get it: `pip install rp2040py --no-binary rp2040py` builds the sdist and needs a C compiler (and `ziglang` for the wasm module, which only warns when missing). That path is not in CI as a published artifact; CI keeps building the extension from source on 3.10 (`pre-commit`, AddressSanitizer, coverage and the firmware workflows run `uv sync` on `python-version: "3.10"`), so it stays tested.

## What was done

1. `pyproject.toml`: `build = "cp311-* cp314t-* cp315t-*"`, and the comment above it says why 3.10 is not in the list.
2. `setup.py`: the module docstring and the abi3 comment no longer say 3.10 gets a published wheel (it says it only gets the normal build from source), and the comment that said the wheel is tagged `cp310-abi3-*` now says `cp311-abi3-*`, which is what the code does (`_ABI3_FLOOR`).
3. `README.md`: the install section says that on 3.10 the pure-Python wheel is what pip picks.
4. `CHANGELOG.md`: an "Unreleased / Changed" entry (the same text, for the release notes).

## Not done, on purpose

- **`requires-python` and the `Programming Language :: Python :: 3.10` classifier are unchanged.** The package still works on 3.10; raising the floor is a separate decision.
- **The CI jobs that run on 3.10 are unchanged** (`python-version: "3.10"`, `cpython-3.10` runtimes): they test the source build, not a wheel, and `.python-version` is still the dev target.
- **No new wheel-vs-pure speed measurement.** The figures above are the README's (measured 2026-10, one machine).

## Progress log

- 2026-11-11: written and implemented in one go, on the maintainer's word; verified by `uv run pre-commit run --all-files`. The first `publish.yml` run on the branch (a "Build Only" pull-request run) is what shows the wheel matrix without `cp310`.

[0013]: 0013-cython-core.md
[0103]: 0103-wasm-backend.md
