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

**Unblocked is not the same as verified**: the script now exits 0 with no traceback, but that only
proves nothing crashed - it does not by itself confirm 3g's scripted scan/join event sequence
actually produced a real result (the script never prints `scan()`'s own return value or polls
`isconnected()` after `connect()`). That check is a separate, not-yet-started task - see
`docs/tasks/cyw43-3g-live-boot-verification.md`.

## Appendix: folded-in working note `docs/tasks/cyw43-post-data-header-freeze.md` (2026-08-16)

The working note this record was created from is reproduced verbatim below, then deleted from
`docs/tasks/` - per this repo's convention that a task file gets folded into a proper record once
it's actually resolved. Not rewritten to match what the investigation eventually found (the root
cause is in this record's own "Root cause" section above); this is the investigation history as it
stood while still open. Earlier sections of this record reference it by its old `docs/tasks/` path;
those references resolve here.

### Task: CYW43 live-boot freezes solid (0% CPU) after early outbound `DATA_HEADER` traffic

Not a `docs/records/` entry - a working note for whoever picks this up next. Found while doing
step 3g's own required live-boot verification (`docs/tasks/3g-scripted-scan-join.md`,
`docs/records/0027-cyw43-wifi.md`) - **not a 3g bug itself** (3g's own code is unit-verified
correct - 42/42 tests, `pre-commit run --all-files` clean), but it currently blocks that
verification from ever reaching script completion, so whoever resumes 3g's live-boot check next
needs this context, and whoever picks up the "raw throughput ceiling" work
(`docs/tasks/simulation-clock-cython-port.md`) should read this too - it's very likely the *same*
already-flagged-but-uninvestigated finding from 0027's own "Same-day follow-up" section, now with
a concrete, reliable repro. *(Both of those task notes have since been folded into
[0027](0027-cyw43-wifi.md)'s and [0039](0039-simulation-clock-native-port.md)'s own appendices.)*

#### Symptom, precisely - not "slow," genuinely frozen

0027's own documented "raw throughput ceiling" (`cyw43_delay_ms()`'s busy-wait costing far more
real wall-clock time than the simulated delay it represents) is a **busy-but-slow** problem: the
CPU's PC keeps varying throughout, real (if wasteful) work is happening the whole time. This is
different: `ps`-visible CPU time stops accumulating *entirely* (confirmed via repeated sampling,
zero increase across 90+ real seconds, then again across a full 10-minute window), the process
sits in `S` (interruptible sleep) state, and `/proc/<pid>/wchan` reads `ep_poll` - genuinely
blocked in an epoll wait, not executing anything. 0027's own "Same-day follow-up" section already
flagged something matching this shape once, under PyPy only, and explicitly did not chase it down:
"PyPy... in a 600s bounded run the CPU's PC sat completely frozen on one single address for the
last 130+ seconds - a different, more suspicious shape than the CPython+Cython baseline (which
kept visibly varying the whole time). Not root-caused; flagging as a possible PyPy-specific issue
worth a closer look, not just 'PyPy is slower here.'" **This session's finding: it is not
PyPy-specific** - the identical frozen/0%-CPU shape now reproduces on native CPython+Cython too.

#### Reliable repro (2026-08-13)

```
uv run rp2040py --log-level info micropython --board pico_w --image v1.28.0 tests/micropython/main-cyw43.py
```

(native CPython+Cython; also reproduces under `uv run --python pypy3.10 --no-dev -- rp2040py ...`)
using a cached real `v1.28.0` `RPI_PICO_W` UF2. Freezes solid roughly 30-35 real seconds in, at a
consistent (not perfectly identical - varied by ~100 lines across repeated runs, `34878`/`34848`/
`34982`/`33078`-ish output line counts, suggesting real-time-sensitive scheduling is involved, not
a pure deterministic-instruction-count deadlock) point still well inside firmware bring-up, *before*
`nic.active(True)` finishes printing - i.e. before any of step 3g's own scripted `escan`/
`WLC_SET_SSID` code is even reachable.

**Isolated the trigger precisely** via a targeted `print(..., file=sys.stderr)` added temporarily
to `GSPIBus._write_wlan()`'s `DATA_HEADER` branch (removed again before landing - see
`docs/records/0027-cyw43-wifi.md`'s own 3g entry for the `DATA_HEADER`/flow-control-response fix
this branch is part of, a real, separate, already-fixed correctness bug found along the way): real
firmware sends *two* early outbound Ethernet frames well before `active(True)` completes - the
first (64 bytes) gets answered and drained cleanly (some other, later poll consumes it fine); the
freeze happens immediately after the **second** (104 bytes, decodes as an IPv6 packet -
`ethernet_frame[12:14] == 0x86dd`, consistent with Router Solicitation / Duplicate Address
Detection traffic from lwIP's own automatic IPv6 link-local setup, not application code). Confirmed
`self._selected == True` and nothing already queued (`rx_packet_pending=0`, `rx_queue_len=0`) at
the moment this second frame is handled - rules out a `GSPIBus`-side queuing bug specifically (the
FIFO activates/responds exactly the same way it did for the first frame, which drained fine).

**Confirmed NOT caused by `GSPIBus` answering `DATA_HEADER` at all**: temporarily reverting that
branch back to pure silent-ignore (the pre-3g, pre-fix behavior) makes the *same* script progress
past 127,000+ output lines without freezing (still climbing steadily when killed) - i.e. whatever's
freezing only manifests once firmware actually gets a flow-control response to react to. This
doesn't mean the fix is wrong (see the 0027 entry - the credit-desync deadlock it fixes is real,
confirmed via an independent, freeze-free Python-level `GSPIBus` reproduction of 200+ mixed
ioctl/data sends) - it means answering `DATA_HEADER` correctly is what lets firmware's execution
reach *this* already-existing freeze for the first time, the same relationship 3g's own scripted
scan/join responses have to it (letting execution get further than the old generic-ack-only
baseline ever reached).

**Ruled out**: CPU frequency governor artifact (0027's own documented gotcha from a previous
session) - `scaling_governor` was `powersave` but `scaling_cur_freq` was ~3.9GHz of a 4.1GHz max,
not throttled. Not a resource-contention artifact either - `uptime` load average was unremarkable
(~1.5-1.8 on a 6-core machine) with no other competing `rp2040py`/`micropython` processes running.

#### Not yet possible in this environment - real blocker for whoever picks this up

**No stack trace obtained** - `gdb -p <pid>` and `py-spy dump --pid <pid>` (via `uvx py-spy`) both
refused: `/proc/sys/kernel/yama/ptrace_scope` is `1` (restricted - only a direct parent process may
ptrace) and there's no passwordless `sudo` in this environment. Whoever picks this up with a
friendlier ptrace policy (or willing to `sudo`) should start there - a single `py-spy dump` while
it's frozen would very likely settle this in minutes instead of hours: is the engine-room task
awaiting an `asyncio.Event`/`Future` that nothing will ever set (a real, fixable concurrency bug),
or a real `asyncio.sleep()` for a computed real-world duration that's merely absurdly, but
finitely, long (a scaling/units bug somewhere in the clock/alarm fast-forward path - `nanos` vs
`micros` vs `millis` mixups are exactly the kind of thing that produces a 1000x-too-long sleep)?
*(Resolved without ptrace in the end - see this record's own "Root cause" section for how the
stack trace was actually obtained.)*

#### Where to look, if picking this up without a stack trace

- `clock/simulation_clock.py`'s alarm-scheduling/`nanos_to_next_alarm` path, and whatever computes
  a real-world `asyncio.sleep()` duration from it in `simulator.py`/`_execute_batch.py` - a unit
  mismatch there would produce exactly this shape (genuinely idle, blocked on a real timer that's
  correct in *kind* but wrong in *magnitude*).
- Whatever RP2040 timer/alarm real firmware's lwIP IPv6 stack uses to schedule its own Router
  Solicitation retry / Duplicate Address Detection timeout (`pico-sdk`'s `alarm_pool`/hardware
  timer, or MicroPython's own `mp_hal_ticks_ms()`-based scheduling) - if real firmware is waiting
  on a *correctly-computed* several-second retry timer and the freeze is simply that (not yet
  ruled out - only confirmed frozen through ~10 real minutes, not proven never to resume), that's
  arguably not a bug at all, just something to let run longer or bound with `--expect-text`/a
  wrapping timeout for CI purposes. Distinguishing "correct but slow" from "genuinely will never
  fire" is exactly what the missing stack trace would resolve quickly.
- CLAUDE.md's own note that a leaked engine-room thread with `core.waiting=True` busy-loops (100%
  CPU) is *not* what's observed here (0% CPU) - so if this is a `core.waiting`-adjacent condition
  at all, it's a different one than that existing documented case, not the same leak.

#### Don't re-derive

- The credit-desync `DATA_HEADER` fix (`_build_flow_control_response()`) is separately confirmed
  correct and necessary - not what this task is about, don't revert it to "fix" this freeze
  without addressing the credit-desync it exists for instead.
- 3g's own code (`escan`/`WLC_SET_SSID` scripting) is unit-verified correct and was never reached
  in any of the freezing runs (the freeze happens during firmware bring-up, well before
  `active(True)` even finishes) - this is not evidence against 3g's own correctness, just an
  earlier, unrelated blocker in the path to observing 3g's own behavior live.
