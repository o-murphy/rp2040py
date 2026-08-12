# 0012. CDC (USB serial) performance

- Status: Implemented (root cause found and fixed; one contributor remains)
- Conceived: 2026-08-03
- Related: note 0017 (perf)

<!-- migrated verbatim from docs/BACKLOG.md lines 165-311 -->

## CDC (USB serial) performance investigation — root cause found and fixed, one contributor remains

**Goal:** figure out why waiting for text over the emulated USB-CDC REPL (`--expect-text` on
`micropython`/`kaluma`) takes wildly variable wall-clock time run to run - anywhere from well under
a minute to several minutes - even for the *same* firmware/image with nothing else changed.
Noticed while verifying the SSI flash-write fixes above; **not** caused by them - the variance
shows up on plain boots with no flash/littlefs activity at all, so it's a distinct, separate
problem from that work, not a regression from it.

**Root cause #1 (fixed) — `Simulator.execute()`'s idle-tick step accounting, in `src/rp2040py/simulator.py`.**
The loop bounds each `execute()` call to a ~1,000,000-unit budget before yielding back via
`threading.Timer(0, self.execute)` (a real OS-thread handoff, per that function's own NOTE). When
the core is WFI'd (`core.waiting`), it jumps straight to the next clock alarm - which costs
essentially nothing in real time no matter how far away that alarm is - but the old code weighted
that jump by `nanos_to_next_alarm / cycle_nanos` and added it to the same budget as real executed
instructions. USB SOF fires every 1ms of sim-time for the entire life of a connected device
(`RPUSBController._schedule_sof_packet`, `src/rp2040py/peripherals/usb.py`), and at 125MHz
(`cycle_nanos` ≈ 8ns) that 1ms jump alone was ~125,000 "units" - enough to exhaust the whole budget
after only ~8 SOF firings (~8ms of simulated time). Every real device sits WFI'd almost all the time
once booted (waiting on input at the REPL, waiting between interrupts, etc.), so this turned "USB
connected and idle" into a real thread handoff roughly every 8ms of simulated idle time - thousands
of them over the course of a boot-to-REPL wait, each one exposed to real OS-scheduler jitter
(thread-creation latency is small in isolation but highly variable under a loaded/shared host,
e.g. a CI runner), which is exactly the "wildly variable, run to run, nothing else changed" symptom
reported above. Confirmed via an isolated repro (bare `RP2040`/`Simulator`, core forced permanently
`waiting=True`, only a 1ms-recurring alarm active): before the fix, ~300 `execute()` calls /
`threading.Timer` creations were needed to advance 2 real seconds' worth of idle sim-time; after,
just 1. Cross-checked against `_bench_firmware`'s hand-rolled equivalent loop in
`src/rp2040py/cli/__init__.py` (used by `rp2040py bench --image ...`, doesn't go through
`Simulator.execute()` at all) - it already counts an idle tick as exactly one step, same as a real
instruction, so this wasn't a deliberate design choice that `Simulator.execute()` diverged from,
just an inconsistency/bug against the pattern already used elsewhere in this codebase.

**Fix:** `Simulator.execute()`'s idle branch no longer adds `nanos_to_next_alarm / cycle_nanos` to
the budget - it now costs the same 1 unit as everything else (the loop's existing unconditional
`i += 1`). Regression test: `tests/test_simulator.py` (fails with `fire_count == 8` against the old
code, passes - covering >1000 firings in one batch, exactly one `threading.Timer` construction -
after). Full suite: 436/436 (435 + this new test), no regressions.

(Later, post-`asyncio`-migration follow-up: that regression test's own firing-count floor turned
out to depend on real wall-clock CPU speed - since `_execute_batch()`'s successor to this budget is
itself time-bounded - and was observed flaky on CI as a result; see CHANGELOG.md's `[Unreleased]`
Fixed section for the fake-`time.monotonic()` fix that made it deterministic without weakening what
it actually checks.)

**Root cause #2 (separate, not fixed, not really fixable here) — raw Thumb-interpretation
throughput for CPU-bound guest code.** Once the guest is actively executing (not WFI'd) - e.g.
MicroPython's `time.sleep()` busy-polls a hardware timer register in a tight loop rather than
WFI-ing - every iteration is a real instruction through this project's pure-Python Thumb
interpreter, and that's just slow, with real (small but nonzero) run-to-run host-speed noise on top
via `RPUSBController.write_delay_microseconds`/etc. This is already documented in README's
MicroPython 1.21-vs-1.28 benchmark section ("identical instruction counts run-to-run... the ~45x
gap is a real difference in how much work 1.28 does per loop iteration, not an emulator bug") -
confirmed independently here by instrumenting a full boot-to-REPL-banner run of MicroPython 1.21:
sim-time-elapsed was identical (~13.6-13.8ms) across repeated runs, but wall-clock varied 0.96s-4.87s
for that identical work, and a follow-up run waiting through a `while True: print(...); time.sleep(1)`
resident script (`tests/micropython/main.py`) spent 61+ real seconds advancing only ~560ms of
sim-time with zero WFI - all real instruction execution, no thread-handoff churn at all (fix #1
doesn't touch this path, and isn't expected to). This piece is inherent to interpreting real
firmware instruction-by-instruction in Python and isn't something to "fix" here beyond what
README's existing PyPy/CPython-3.14-JIT guidance already covers - noted so it isn't mistaken for
leftover work from this investigation.

**Where the "SPI tests finish in seconds regardless of version" observation fits:** that's not
evidence of a distinct USB-specific code path bug (there wasn't one, beyond root cause #1 above) -
`tests/micropython_spi_run.py` watches SPI0 pin callbacks that fire early in boot, before the guest
reaches its CPU-bound resident-script loop, so it's simply exposed to far fewer total instructions
(and far less of root cause #2's noise) than a `--expect-text` test waiting for banner/resident-
script text after full boot.

**Follow-up (2026-08-05): with `rp2040py.native` in the picture, root cause #2's own "no
thread-handoff churn" claim above no longer holds, and thread-handoff overhead turns out to be the
dominant cost of a CLI-driven run - found while investigating a report that `rp2040py.native`
"shouldn't be 3x slower than expected" running real guest code (MicroPython 1.28 + littlefs,
`--expect-text "Hello, MicroPython!"`).** `CortexM0Core.execute_instruction()` under
`rp2040py.native` never appears anywhere in a `cProfile` trace, for any of ~65M calls across a full
boot - confirmed this is expected Cython behavior (no trace hooks are emitted unless built with
`profile=True`), not a bug, and it means native dispatch itself is *not* the bottleneck: if it were
slow, that time would still show up, attributed to whichever Python frame called it, and it doesn't.
`{built-in method time.sleep}` instead dominates the profile (~35s of a ~45s run) - not evidence of
waste on its own (it's `_wait_for_simulator`'s main-thread poll loop faithfully reporting that the
main thread spent nearly all its time waiting on the background thread doing the real work, which is
by design), but it obscured the actual answer for a while.

Three numbers settle it, all against the same ~65M-step MicroPython 1.28 + littlefs boot:

| Path | Time |
| --- | --- |
| `rp2040py bench` (headless, single tight loop, no `threading.Timer`/polling at all) | 24.95s |
| `rp2040py micropython` (normal CLI path, ~65 `threading.Timer` handoffs - `execute()`'s 1,000,000-step batch size) | ~45s |
| Same CLI path, `Simulator.execute()` patched to run the whole boot in one batch (no handoffs) | 19.45s |

Removing the handoffs took the CLI path from ~45s to 19.45s - actually *beating* the headless
benchmark, not just matching it. That ~25s gap is pure threading-model tax, orthogonal to
instruction-dispatch speed entirely: `execute()`'s `threading.Timer(0, self.execute)` reschedule
(a brand-new OS thread every batch) directly mirrors upstream rp2040js's
`setTimeout(() => this.execute(), 0)`, which in Node's single-threaded event loop is *necessary* so
other callbacks (stdin, GDB socket, USB) get a turn between bursts. CPython's GIL already
preemptively time-slices between real OS threads, so a tight loop on a background thread doesn't
starve the main thread's own polling the way single-threaded JS would - the yield-and-reschedule
dance this ported over doesn't buy Python anything, and each handoff (new thread creation/scheduling,
plus contending for the GIL against the main thread's own periodic 100ms wake-ups) has a real cost.
This cost was always there, in both the pure-Python and native builds - it just used to be a small
fraction of a much slower pure-Python run, and native dispatch being ~4x (or more, per this) faster
made a *fixed* per-batch cost dominate wall time instead.

**Update: done, via the full `asyncio` migration** (see
[docs/ASYNCIO_MIGRATION_BACKLOG.md](ASYNCIO_MIGRATION_BACKLOG.md)) - `execute()`'s
`threading.Timer` reschedule-per-batch chain is gone, replaced by `Simulator`'s persistent
engine-room thread + `await asyncio.sleep(0)` between batches, exactly the "one persistent worker
thread" follow-up this section originally scoped out. That migration's own measurements confirm
the per-batch thread-handoff removal accounts for the CLI-path gap noted above, with no
instruction-throughput regression (`await asyncio.sleep(0)` vs. `threading.Timer(0, fn)`: under 1%
difference, noise-level).

One new, *different* real-time cost this surfaced, not present in the analysis above: an idle
(WFI'd) batch is exactly as free to run the full 1,000,000-iteration ceiling as a busy one once
nothing weights it down (see this document's own idle-tick fix, right above) - and CPython takes
~0.9-1.8s wall-clock to clear that many idle iterations (upstream rp2040js hits the identical
ceiling but V8 clears it in low milliseconds). That's invisible to a headless `rp2040py bench` run
or to the old threading model (where stdin lived on its own real OS thread, decoupled from
whatever `execute()`'s batch was doing) - it only became externally visible once `asyncio`
migration put `StdioInteractiveRepl`'s `add_reader()` callback on this same engine-room loop,
turning "the shared loop is busy clearing an idle batch" into "a keystroke sits unread for up to
~1-2 real seconds." Fixed in the same migration (`_BATCH_YIELD_BUDGET_SECONDS`) - see
CHANGELOG.md's `[Unreleased]` entry for the full before/after numbers, including why the first fix
attempt (bounding only an uninterrupted idle run) shipped without actually fixing anything.

**New, found live-testing this branch post-"migration complete," not yet decided whether to
fix — `Simulator.wait_for_shutdown()` cannot currently distinguish "the target stopped because a
GDB client paused it" from "the target stopped because it's genuinely done and the process should
exit."** `wait_for_shutdown()`'s poll loop (`while self.executing: ...`, added well before the
`asyncio` migration, in the same PR as `ShutdownRequest`) exits - and the caller's `main()`
consequently returns, ending the whole process - the moment `self.executing` goes false for *any*
reason, not just `shutdown_request`/`KeyboardInterrupt`. A GDB client's raw Ctrl-C interrupt
(`gdb_connection.py`'s `feed_data()`) calls `target.stop()` exactly like a natural firmware halt
does; if it takes longer than one ~100ms poll tick for the human at the other end of the debugger
to send `c`/`continue`, `wait_for_shutdown()` has already concluded "done" and exited by then -
confirmed live, end to end, over a real GDB-remote-protocol connection against real firmware, on
**both** the threading model and this `asyncio` branch equally (this predates the migration
entirely, inherited from the same commit that added `wait_for_shutdown()` itself). Not fixed here:
the loop's "exit when the target naturally stops" behavior is load-bearing for `rp2040py run`
against bare-metal `.hex`/`.uf2` firmware that halts itself via a `bkpt`-based convention with no
debugger attached (likely what `ci-pico-sdk.yml` exercises) - a fix needs to distinguish "a GDB
session is actively attached and expected to resume this" from "nothing is ever going to resume
this," not just delete the check.

