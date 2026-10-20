# 0107 - less CI load without less coverage: build once, loop the firmware versions in a script

Status: **implemented (2026-10-20), branch `ci/reduce-load`, verified on real runs (all seven branch workflows green on `150266e`, the official-suite groups equal to the baseline, `publish.yml` "Build Only" green on every platform).** Asked for by the maintainer: CI took too long, most of it packages built again and again and a pure-Python run repeating what the oracles already do; the rule was *no loss of what is tested*. Written after the fact from the commits `44a29d5` .. `1dac5f8` and the runs below; the timings are the runs' wall clock as read from the Actions UI, not a benchmark.

## What was spending the time

- **The package was built once per firmware version.** A MicroPython job per release built the wheel (Cython, C++, the wasm module) before running a boot that takes seconds; about 74 % of the MicroPython workflow's runner time was package builds (read from the job logs).
- **The pure-Python backend was tested twice.** `pre-commit`'s pure-Python pytest hook and the coverage job ran the lockstep oracles on the pure build, but every oracle builds its native and its pure side itself, so the second run tested the same pair again.
- **The littlefs test images were built with the whole extension**, though `mklittlefs` is pure Python.
- **A pushed tag re-ran every branch workflow** next to `publish.yml`.

## What was done (the net state, not the order of the commits)

1. **One package build per Python runtime, the firmware versions looped in `scripts/ci/*.sh`** (`lib.sh` with `ci_step` / `ci_soft_step` / `ci_finish`, then `kaluma.sh`, `micropython.sh`, `circuitpython.sh`, `pico_sdk.sh`). A script takes the runtime and a list of versions (`MICROPYTHON_VERSIONS="1.16 ... 1.23.0:pico_w ..."`, `CIRCUITPYTHON_RELEASES="9.2.9:hard_reset,wlan ..."`), runs each, writes a row per step to `$GITHUB_STEP_SUMMARY` and fails the job at the end if any step failed - a failing version does not hide the ones after it. `timeout` cannot run a shell function, so a step is a word list or a `sh -c`.
2. **`build-littlefs` jobs** (Kaluma, MicroPython) build the images with `RP2040PY_SKIP_NATIVE_BUILD=1 RP2040PY_SKIP_WASM_BUILD=1`, `uv sync --no-dev --extra fs` and `uv run --no-sync rp2040py mklittlefs`.
3. **The official MicroPython suite builds the wheel once** (`build-wheel`, `uv build --wheel`, uploaded) and each group installs it into a venv instead of syncing the project.
4. **Pico SDK: the firmware is built once per SDK release** (`build-firmware`, the SDK cache kept) and one job per runtime boots every release; Gate G2 became one parametrized pytest run over all the releases (`RP2040PY_PICO_SERIALS`, so the C++ harness is compiled once instead of once per release).
5. **The pure-Python pytest of `pre-commit` and of coverage runs `--oracle=off`**; the oracles run in the native pytest hook. The coverage job runs the native pass first and the pure pass second, into `.coverage.native` and `.coverage.pure`. On non-Linux `pre-commit` the pure hook is skipped (`SKIP=uv-pytest-pure`).
6. **Workflows trigger on `push: branches: ["**"]`**: every branch is tested, tags are not (a tag is `publish.yml`'s). `publish.yml` is unchanged.

## What was tried and rejected (the maintainer's calls)

- **`push: branches: [main]` plus `pull_request`**: a branch without an open PR then gets no CI, which is not acceptable here.
- **One concurrency group per branch for push and pull_request together**: a push can cancel the pull_request run of the same commit (or the reverse), which kills a result at random. The original groups were restored.
- **A path filter on `publish.yml`** (skip the wheel build for a change under `tests/` only): the tests run inside the wheel build (`cibuildwheel`'s `test-command`), so a tests-only change is exactly one that should build wheels.
- **Splitting the Pico SDK and the official suite in the first round**: the maintainer held both back until the Kaluma / MicroPython / CircuitPython pattern had been checked on real runs; they were done afterwards (items 3 and 4).

## Results

Wall clock of the branch workflows on `150266e` (all green): Kaluma ~2 min, Pico SDK ~2 min, CircuitPython ~3 min, Pre-commit ~7 min, MicroPython releases ~8 min, AddressSanitizer ~9 min, official suite ~10 min. The maintainer's reference before the Kaluma change was ~18 min for that workflow. The official suite per group (basics a-l 366/0/8/0, m-z 177/0/24/0, thread 24/0/9/0, float/inlineasm 72/0/16/0 as passed/failed/skipped/error) equals the baseline, so what is tested did not change. `publish.yml` "Build Only" (workflow_dispatch) on `2ea363e`: every platform green, the longest the Linux x64/x86 QEMU job at ~58 min.

## Not done

- `test-board-spec` in `ci-micropython.yml` still does a full `uv sync` (it was not part of the ask).
- The official suite's `basics a-l` and `m-z` groups are unbalanced; rebalancing them would shorten the longest group.
- The Linux x64/x86 wheel job (QEMU for i686) dominates `publish.yml`; nothing was changed there.

## Progress log

- 2026-10-20: written; the changes had been pushed in order `44a29d5`, `80a609b`, `5f05cad`, `bf77424`, `afa5bd1`, `acf315e`, `1dac5f8` and verified run by run.
