# 0041. CYW43 live-boot freeze (`docs/tasks/cyw43-post-data-header-freeze.md`) root cause + fix

- Status: Implemented — verified (2026-08-13)
- Conceived: 2026-08-13 · Implemented: 2026-08-13
- Related: 0027 (CYW43439/Pico W epic - this was blocking step 3g's own live-boot verification),
  0038 (`GSPIBus` ioctl-response zero-fill fix - the prior real correctness bug found on this same
  path), 0039 (`SimulationClock` native port) · working note:
  `docs/tasks/cyw43-post-data-header-freeze.md` (full investigation history, kept as-is)

## Context

`docs/tasks/cyw43-post-data-header-freeze.md` documented a genuine freeze (0% CPU, `wchan=ep_poll`
- not the known raw-throughput ceiling) reproducing ~30-35s into `tests/micropython/main-cyw43.py`
live-booting `v1.28.0`, on both CPython+Cython and PyPy. Its own "Not yet possible in this
environment" section identified the real blocker as no stack trace: `gdb -p`/`py-spy dump` both
refused (`ptrace_scope=1`, no passwordless `sudo`), leaving only two hypotheses undistinguished -
"awaiting an `asyncio.Event`/`Future` that nothing will ever set" vs. a real-but-absurdly-long
timer.

## Root cause (confirmed via a stack trace, gathered without ptrace)

`gdb`/`py-spy` still refused in this session (same `ptrace_scope`/no-`sudo` constraints). Worked
around entirely without ptrace: a throwaway wrapper script (never landed, lived only in a scratch
directory) importing the real `rp2040py.cli` unmodified and installing a `SIGUSR1` handler that
calls `faulthandler.dump_traceback(all_threads=True)` plus walks `asyncio.all_tasks()` printing
each pending task's suspended stack - both work from *inside* the target process's own signal
handler, no external attach/ptrace required at all. Reproduced the freeze, sent `SIGUSR1`, and got
an exact answer on the first try - confirmed identical across two independent repro runs.

**Confirmed: the first (undistinguished) hypothesis.** `Simulator.execute()`'s task
(`simulator.py`) had already ended - **not** merely slow - with an uncaught `RuntimeError`:
`[RP2040] read from address 2000208e, which is not 32 bit aligned`. That message is
`_rp2040.py`'s (and native `_rp2040.pyx`'s) own `read_uint32()` unaligned-read diagnostic - logged
via `self.logger.error(...)`, and `ConsoleLogger.error()` (`utils/logging.py`) **raises**
`RuntimeError` by default (`throw_on_error=True`), unlike its sibling `.warning()` calls right next
to it in the same function (`"Read from invalid memory address"`) which don't. `read_uint32()`
itself treats an unaligned read as recoverable - it logs and then still computes and returns a
value regardless - so the `.error()` call was simply the wrong severity for a condition its own
caller doesn't treat as fatal.

`execute()` had no `try`/`except` around its loop body, so this uncaught exception silently killed
the task - "silently" because `self._execute_task` stays referenced by the live `Simulator` for
the process's whole remaining lifetime, so asyncio's own default "Task exception was never
retrieved" handler (which only fires on garbage collection) never triggers either. With the engine
room dead, nothing could ever again advance simulated time or produce CDC output, so
`MicroPythonDevice._aexec()`'s `await asyncio.wait_for(done.wait(), timeout=None)` (the raw-REPL
completion `Event`, only ever `.set()` from inside `execute()`'s own call chain) blocked forever on
a single, timeout-less `epoll_wait` - exactly the observed shape, and identical on PyPy since the
cause is entirely host-side, not engine-specific (matching 0027's own "Same-day follow-up" note
that first flagged this shape under PyPy alone).

## Fix

**Narrow (the actual trigger)**: `_rp2040.py:232` and native `native/_rp2040.pyx:278` changed from
`.error()` to `.warning()` for the unaligned-read diagnostic, matching the severity of the
sibling "invalid memory address"/"undefined address" lines right next to each - all three describe
a condition the bus already recovers from, not a fatal one.

**Systemic (defense in depth - the same class of bug elsewhere would previously cause the same
silent hang)**: `Simulator.execute()` now catches any `Exception` from its loop body, records it
on a new `self.engine_room_error`, sets `self.stopped = True` (previously never set on this path -
`wait_for_shutdown()`/`_await_shutdown()`'s own poll loops would otherwise never notice execute()
had ended either), and re-raises. `start_execution()` installs a task-done callback that logs any
such crash immediately and loudly (`logging.critical` with the traceback), rather than leaving it
to a garbage-collection-gated default handler that in practice never fires. A new
`Simulator.wait_for(event, timeout)` races an `Event.wait()` against the engine-room task itself -
used by `device/mp_device.py`'s `_aconnect()`/`_aexec()` (previously plain
`asyncio.wait_for(event.wait(), timeout)`) - so any caller waiting on device-produced state now
unblocks immediately (a clear `RuntimeError`, wrapping the real cause via `__cause__` for a crash,
a distinct message for a deliberate `stop()` mid-wait) instead of hanging forever once execute()
has ended, for any reason. `Simulator.wait_for_shutdown()` (the older thread-poll model) and
`cli/__init__.py`'s `_await_shutdown()` (the asyncio one) both now check `engine_room_error` after
their poll loop exits and raise it, so a crash always surfaces as a real, loud process failure
(nonzero exit, real traceback) instead of silently looking like natural completion.

New regression coverage: `tests/test_simulator_engine_room_crash.py` (execute() crash sets
`engine_room_error`/re-raises/`wait_for()` surfaces it; a deliberate `stop()` mid-wait unblocks
`wait_for()` distinctly without `engine_room_error`; `wait_for()`'s normal success path is
unaffected; `wait_for_shutdown()` surfaces `engine_room_error`). Full `pre-commit run --all-files`
(mypy/ruff/pytest, pure-Python and native builds) passes clean.

## Verification

Same exact repro command from the working note, re-run after the fix - no longer freezes:

```
uv run rp2040py --log-level error micropython --board pico_w --image v1.28.0 tests/micropython/main-cyw43.py
```

Native CPython+Cython: completes in ~49-51s real time, exit code 0, full script output (`active:
True` / `Scan for networks` / `Connected False`, then `connect()`/`config()`/`ipconfig()` with no
further prints in the script itself, matching its own source) with no traceback. PyPy 3.10.16:
also completes, exit code 0, same output shape - confirms the fix is not CPython/Cython-specific,
consistent with the root cause being entirely host-side.

Caught along the way, not part of this record's own scope: a `uv run --python pypy3.10` verification
pass replaces the shared `.venv` in place (uv resolves `--python` against the same `.venv` path,
not a separate one) - restored via `uv venv --python 3.10 .venv --clear && uv sync --reinstall-package
rp2040py --no-cache` afterward. Whoever needs both environments side by side should use `.venv-pypy310`
(already present in this repo) explicitly rather than `--python pypy3.10` against the default `.venv`.

## Net effect

0027's step 3g live-boot verification (`docs/tasks/3g-scripted-scan-join.md`) is unblocked - the
freeze that prevented it from ever reaching script completion is fixed, not merely worked around.
Whoever picks up 3g's own live-boot check next no longer needs to read
`docs/tasks/cyw43-post-data-header-freeze.md`'s blocker context first; that file is left as-is
(investigation history, not rewritten) with this record as its resolution.
