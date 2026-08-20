# 0065. `test_a_queued_exec_erroring_does_not_stall_the_ones_behind_it` flaky on CI

- Status: **Not root-caused — closed as dormant.** Single occurrence (2026-08-14); not reproduced
  in any of the ~85 subsequent `Pre-commit` workflow runs through 2026-08-18, including the small
  number that needed a retry for unrelated reasons. Reopen if it recurs.
- Conceived: 2026-08-14 · Closed: 2026-08-18
- Related: none - confirmed unrelated to the same session's 0040/0044 work (see the folded-in note
  below)

## Context

`tests/test_device.py:156` drives its own mock serial responder on a background
`threading.Thread`, synchronized with the main thread via a busy-poll on
`device.cdc.on_serial_data` identity. Three sequential `device.exec_async(...)` calls run
concurrently against this one responder thread; the middle one is fed a deliberately malformed ack
to trigger `RawReplError`, and the test asserts the first and third still complete normally around
it. See the folded-in working note below for the full original investigation trail (the busy-poll
mechanism, both observed failure shapes, and where to look next if this recurs).

Both known failures are from the same `Pre-commit` run,
[31756474373](https://github.com/o-murphy/rp2040py/actions/runs/31756474373) (2026-08-14, branch
`cythonize/pio`):

- `windows-latest` (job 94633161769): `second.result(timeout=5)` raised a plain
  `concurrent.futures._base.TimeoutError` instead of the expected `RawReplError`.
- `ubuntu-latest` (job 94633161781): `Failed: DID NOT RAISE RawReplError`.
- `macos-latest` passed in the same run.

## 2026-08-18: swept CI history for a recurrence, found none

Asked whether this had happened again since. Checked, in order:

1. All `Pre-commit` workflow runs with a `failure` conclusion between 2026-08-14 and 2026-08-18
   (13 runs) - none of their job logs mention this test at all. The two most recent failures at the
   time of checking (`32064766772`, `32060826520`, both on `claude/eink-demo-board-structure-glrkwt`,
   2026-08-17) fail all three OS legs simultaneously on an unrelated break, not this test.
2. Runs that *ultimately* show `success` but needed a retry - `gh run list` only reports the final
   attempt's conclusion, so a first attempt that hit this flake and passed on rerun would be
   invisible to (1). Checked `run_attempt` on all 85 `Pre-commit` runs since 2026-08-14: only two
   had more than one attempt (`31977195300`, `31906097495`). Neither's earlier attempt mentions this
   test - one failed on a *different* timing flake
   (`test_batch_yields_within_budget_even_after_switching_from_idle_to_busy`,
   `tests/test_simulator.py:157`), the other's first attempt never ran any jobs at all
   (`action_required` - a workflow-approval gate, not a test failure).

No recurrence found anywhere in that window. Not root-caused - closing the open task and
downgrading tracker attention accordingly, but the mechanism described in the folded-in note below
(a 1ms-granularity busy-poll racing `exec_async()`'s own callback-swapping under CI-runner
scheduler jitter) remains the leading theory if it happens again.

## If this recurs

Pick up the folded-in note's "Where to look next" below - specifically, whether the busy-poll can
miss a handler transition against `exec_async()`'s internal callback-swapping has never actually
been checked against the real `RawReplDevice` internals, and a local repro under artificial load
(`stress-ng` or a tight loop) was never attempted.

## Appendix: folded-in working note `docs/tasks/queued-exec-erroring-flaky-test.md` (2026-08-18)

The working note this record was created from is reproduced verbatim below, then deleted from
`docs/tasks/` - per this repo's convention that a task file gets folded into a proper record once
it's actually resolved (here: resolved as "closed, not root-caused, dormant" rather than a fix -
see this record's own sections above for the 2026-08-18 CI sweep that led to closing it). Not
rewritten to match that later finding; this is the investigation history as it stood on 2026-08-14.

---

# Task: `test_a_queued_exec_erroring_does_not_stall_the_ones_behind_it` flaky on CI

Not a `docs/records/` entry - a working note for whoever picks this up next. Found while
investigating an unrelated CI failure (the `PyTime_t`/`Py_LIMITED_API` compile break documented in
[0040](../records/0040-time-monotonic-vs-cimport.md)) - **genuinely unrelated to that fix**: this
test (`tests/test_device.py`) touches no native/Cython code at all, and both observed failures ran
under `UV_PYTHON=3.10`, below this project's abi3 floor, so they never exercise the
`Py_LIMITED_API` code path either. Not yet root-caused.

## Repro / where observed

Both from the same `Pre-commit` workflow run:
https://github.com/o-murphy/rp2040py/actions/runs/31756474373

- `windows-latest` (job 94633161769): `second.result(timeout=5)` raised a plain
  `concurrent.futures._base.TimeoutError` instead of the expected `RawReplError` -
  `with pytest.raises(RawReplError): second.result(timeout=5)` never got the exception it was
  waiting for within the 5s budget.
- `ubuntu-latest` (job 94633161781): `Failed: DID NOT RAISE RawReplError` - the same
  `pytest.raises(RawReplError)` block completed without any exception at all this time, a
  different failure shape than the Windows timeout.
- `macos-latest` passed in the same run.

Not re-run locally yet - only seen via these two CI job logs so far.

## What's confirmed so far

- Two different failure *shapes* (a timeout vs. a clean non-raise) from the same assertion, on two
  different OSes, in the same CI run - consistent with a genuine timing race rather than a single
  deterministic bug, but not confirmed either way.
- The test (`tests/test_device.py:156`) drives its own mock serial responder on a background
  `threading.Thread`, synchronized with the main thread via a busy-poll:
  ```python
  def _next_handler():
      nonlocal last_handler
      while device.cdc.on_serial_data is last_handler:
          time.sleep(0.001)
      last_handler = device.cdc.on_serial_data
  ```
  waiting for `device.cdc.on_serial_data` (a callback reference) to change identity before feeding
  the next canned reply. Three sequential `device.exec_async(...)` calls run concurrently against
  this one responder thread; the middle one is fed a deliberately malformed ack (`b"XY"`) to
  trigger `RawReplError`, and the test asserts the first and third still complete normally around
  it (the test's own point: one queued exec erroring shouldn't stall the ones behind it).
- Not observed on `macos-latest` in the same run - could mean macOS scheduling happened to avoid
  whatever window causes this, or could just mean it didn't get unlucky this one time; one data
  point either way, not enough to conclude a platform-specific cause.
- Not related to this session's other work: doesn't touch `native/`, doesn't touch DMA/SPI/clock
  code (0044/0040's own areas), and runs under a Python version (3.10) that isn't affected by
  0040's `Py_LIMITED_API` finding.

## Where to look next

- Whether the `_next_handler()` busy-poll (1ms sleep granularity) can miss a handler transition or
  race against `device.exec_async()`'s own internal callback-swapping under real CI-runner
  scheduler jitter (shared runners are noisier than a local dev machine) - not yet checked against
  the actual `exec_async()`/`RawReplDevice` internals it's synchronizing with.
- Whether this is new (never noticed before) or a pre-existing flake this session's changes simply
  happened to surface by triggering more CI runs than usual - no attempt yet to check git blame/CI
  history on this specific test for prior failures.
- A local repro under load (e.g. `stress-ng` or just running the test in a tight loop) to see if
  it reproduces off CI, before concluding it's CI-runner-specific.

## Don't re-derive

- The two observed failure shapes (Windows timeout vs. Ubuntu clean non-raise) and their job
  links above - no need to re-fetch those CI logs unless investigating further invalidates them.

## Sighting - 2026-08-20 (local, under load)

One failure on a developer machine, which answers this record's own third "where to look next"
(*"a local repro under load ... to see if it reproduces off CI"*): **it does.**

`RP2040PY_SKIP_CYTHON=1 uv run pytest` failed `test_a_queued_exec_erroring_does_not_stall_the_ones
_behind_it` with the Windows-CI failure shape (a `concurrent.futures TimeoutError` out of
`future.result(timeout=5)`) while several live-firmware boots were running in parallel on the same
machine. The native run in the same `pre-commit` invocation passed, and three consecutive re-runs
of `tests/test_device.py` plus a full re-run of the suite were all green.

So: not CI-runner-specific, and not Windows-specific - it is a real-time-budget flake that surfaces
whenever the machine is busy enough, which is exactly what a shared CI runner is. Nothing about the
2026-08-20 work (docs/records/0089-one-reset-for-every-trigger.md's Phases 0-2) touches this test's
path; the load it ran under is the whole difference. Still not root-caused, and still closed
dormant - this is one more data point for whoever picks it up, plus a cheap local repro recipe:
run the suite while a couple of `rp2040py micropython --image <tag>` boots are in flight.
