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
