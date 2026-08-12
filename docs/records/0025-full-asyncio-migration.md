# 0025. Full asyncio migration (one engine room per long-lived component)

- Status: Implemented — phases 1-5 landed
- Conceived: 2026-08-12
- Related: supersedes 0014 (threading model) · 0026 (main-thread asyncio) · 0019 (idle-yield)

<!-- migrated verbatim from docs/ASYNCIO_MIGRATION_BACKLOG.md lines 1-491 -->

# Full asyncio migration

**Status:** PR 1 (`simulator.py` + `peripherals/pio.py`), PR 2 (`gdb/gdb_tcp_server.py`), PR 3
(`cli/stdio_repl.py` + `device/repl_runner.py`), PR 4 (`device/mp_device.py`), and PR 5
(`gdb/gdb_target.py` + `gdb/gdb_tcp_server.py`) landed - see "Phased migration order" below. That's
every item this document ever named. There is no more open migration work tracked here; new
findings (if any) get their own section rather than reopening this list.

**Post-"done" findings, from live-testing this branch (real firmware, real pty, real GDB-remote
connections, not just the unit-test suite) rather than from a bug report:**

- **A real, measured interactive-REPL latency regression, not caught by this migration's own
  testing.** `StdioInteractiveRepl` sharing `Simulator`'s engine-room loop (PR 3's design, see
  "Target shape" below) turned out to have a real cost the design discussion didn't account for: an
  idle batch is free to run its full 1,000,000-iteration ceiling in *real* time before yielding,
  which CPython (unlike V8, which hits the identical ceiling upstream) takes ~0.9-1.8s to clear -
  during which `add_reader()`'s callback, sharing that same loop, can't run at all. Two fix
  attempts; the first shipped without measuring against real hardware and turned out to fix
  nothing (see CHANGELOG.md's `[Unreleased]` entry and docs/BACKLOG.md's CDC section for the full
  story). Worth remembering for any future component added to this same shared-loop pattern: a
  synthetic/unit-level test of "does the bound exist" is not the same as verifying it actually
  fires under realistic load.
- **`GDBTCPServer.close()` could hang the whole process indefinitely** if `_aclose()` itself ever
  stalled past its timeout - confirmed on real CI (a ~6-hour wheel-build hang on a macOS +
  free-threaded-Python runner, force-cancelled by GitHub's own outer timeout, not this project's).
  `close()`'s `.result(timeout)` raising skipped `loop.stop()`/`thread.join()` entirely, leaving the
  deliberately non-daemon engine-room thread running forever. Fixed - see CHANGELOG.md. A reminder
  that every "own engine room, own non-daemon thread" component this migration introduced
  (`GDBTCPServer` is the only one so far) needs its shutdown path to degrade gracefully under a
  stuck cleanup, not just under the happy path a unit test exercises.
- **`Simulator.wait_for_shutdown()` cannot distinguish a GDB-initiated pause from a genuine "done,
  exit the process" signal** - not introduced by this migration (inherited from the same
  pre-migration commit that added `wait_for_shutdown()` itself, and reproduces identically on the
  pre-migration threading model too), but only found by live-testing GDB against this branch. See
  docs/BACKLOG.md's CDC section for the full writeup and why a fix isn't as simple as deleting the
  check. Left open, tracked there rather than here since it isn't specific to this migration.

## Why this document exists

Upstream rp2040js is single-threaded: Node's event loop makes `process.stdin.on('data', ...)`,
`setTimeout(fn, 0)`, and `net.createServer()` all just callbacks queued on the one thread that's
already running everything else. There is no second thread, so there is no question of "which
thread is allowed to touch this state" or "which thread is allowed to exit the process" — those
questions don't have a *wrong* answer in a single-threaded program, they don't exist at all.

Python has no equivalent implicit event loop, so every place upstream relied on that has been
ported to a real OS thread instead. That's not a hypothetical concern — it has caused actual,
independently-discovered bugs, more than once, in different subsystems:

- `src/rp2040py/peripherals/pio.py:802-814` (`RPPIO.run()`/`RPPIO._lock`): a background
  `threading.Timer(0, self.run)` reschedule chain touches machine/self state (`waiting`, `pc`,
  `irq`, ...) that `write_uint32()` can *also* touch from whatever thread is driving
  `Simulator.execute()` — a real, reproduced CI flake
  (`test_program_with_a_wait_irq_7_instruction`) where the two interleaved and lost an update.
  Fixed with an `RLock`, not by removing the thread. The code's own comment already says it
  outright: *"Python has no equivalent implicit event loop... Unlike the JS version this
  introduces genuine concurrency."*
- `src/rp2040py/simulator.py:70-78` (`Simulator.execute()`): the exact same pattern —
  `threading.Timer(0, self.execute)` standing in for `setTimeout(fn, 0)` — with the same comment,
  independently written, about the same tradeoff.
- `src/rp2040py/cli/stdio_repl.py` (pre-PR-3 state): a whole session's worth of bugs chased
  through this file (see
  [docs/BACKLOG.md](BACKLOG.md#unified-process-shutdown-coordinator-ctrlx---expect-text-sigterm--done)
  and the two fixes landed just before this document was written) — a stdin-reader thread that
  wasn't joinable, then wasn't joined correctly, a SIGTERM handler that only works from the main
  thread, a `_wake_r`/`_wake_w` self-pipe whose entire job is working around Python not having a
  way to cancel a blocked `os.read()` from another thread. Fixed in PR 3 - see "Resolved during
  PR 3" below, including a real, previously-undetected `USBCDC.tx_fifo` race this same thread
  caused.
- `src/rp2040py/gdb/gdb_tcp_server.py` (pre-PR-2 state): a non-daemon accept thread (so a
  listening GDB server keeps the process alive, matching Node's `net.Server.listen()`) that can't
  be unblocked by closing its socket out from under it — confirmed the hard way, an earlier
  close()-only version hung forever — so it was polled with a 0.2s `socket.settimeout()` instead.
  Fixed in PR 2 - see "Resolved during PR 2" below.

Every one of these is a workaround for the same missing primitive: a way to run many logical
"processes" (the CPU-execution loop, a PIO state machine, a stdin reader, a GDB connection) on top
of one real thread, cooperatively, with a scheduler that already knows how to wait on a socket, a
file descriptor, or a timer without a dedicated OS thread per source. That primitive is `asyncio`.
This document sketches what actually replacing the threads (not just patching each one further)
would look like.

## What doesn't change

Worth stating up front, because it bounds the scope: **the CPU-emulation hot path itself does not
become async.** `CortexM0Core.execute_instruction()` (and its Cython-native equivalent,
`rp2040py.native`) stays exactly what it is — a synchronous, CPU-bound function call. `asyncio`
doesn't parallelize CPU-bound work; it multiplexes I/O-bound waiting onto one thread. The only
thing that changes about the execute loop is *how it periodically yields* so other things (a GDB
connection, stdin) get a turn — `await asyncio.sleep(0)` instead of rescheduling itself via
`threading.Timer(0, ...)`. Instruction throughput is unaffected either way; this migration and the
native backend's ~4x speedup (see BACKLOG.md) are unrelated axes.

Also unchanged: `USBCDC`'s internal callback wiring
(`on_serial_data`/`on_device_connected`/`on_endpoint_write`/...,
`src/rp2040py/usb/cdc.py:70-71`, `src/rp2040py/peripherals/usb.py:251-254`) and the FIFO it reads
from (`src/rp2040py/utils/fifo.py`). These already are plain synchronous function calls made
in-line from whatever's driving `execute()` — that's precisely why `mp_device.py`'s own comment
(`src/rp2040py/device/mp_device.py:86-96`) is emphatic that pacing an upload must go through a
*simulated clock alarm*, never a real-time `threading.Timer`, against this exact state. Under a
single-loop model this constraint doesn't relax, it becomes trivially true: there's only one
thread, so nothing *can* touch `USBCDC`/`RPUSBController` concurrently with `execute()` by
construction, and today's careful "which thread is this safe to call from" comments scattered
through `repl_runner.py`/`mp_device.py`/`pio.py` become unnecessary rather than replaced by
something else.

## Target shape: one engine room per long-lived component, not one shared loop (yet)

Revised twice now. PR 1 planning replaced the original one-loop sketch with "two fixed roles"
(one shared "front door" for I/O, one "engine room" owned by `Simulator`) - a plain `asyncio.run()`
per call works for a caller that blocks until done (`_cmd_run`), but not for `BaseDevice`/
`MicroPythonDevice`'s "boot, return control, keep running so a later `exec()` can talk to it"
contract, since `asyncio.run()` only returns once its coroutine *finishes* and `execute()` doesn't
finish until stopped.

Building PR 2 (`GDBTCPServer`) revised it again: rather than inventing the "front door" loop early
to host GDB's connection I/O, `GDBTCPServer` got its **own** independent engine room instead (same
`start_loop_thread()` helper, its own thread, never shared with `Simulator`'s). Reasoning: GDB's
connection handling (accepting a socket, reading bytes) has nothing to do with CPU/peripheral
state, so there's no shared-state reason to put it on the same loop as `execute()` - and keeping it
separate meant zero changes to `IGDBTarget`'s `Protocol` (`gdb/gdb_target.py`), so every existing
fake target in tests stayed exactly as simple as it was.

PR 3 (`StdioInteractiveRepl`) went the **other** way, on purpose - the first case that didn't get
its own engine room. Reasoning: unlike GDB connection I/O, forwarding stdin *is* touching
`Simulator`/`USBCDC` state, on every single byte typed (`cdc.send_serial_byte()`), not just
per-command. Giving it a separate loop would mean bridging into `Simulator`'s engine room on every
keystroke for no benefit; registering `loop.add_reader(stdin_fd, callback)` directly on
`Simulator`'s own engine-room loop means the callback *is already running on the right thread*, so
`send_serial_byte()` is a plain, safe, synchronous call - no bridge call needed per byte at all.
Only the one-time `add_reader`/`remove_reader` registration (which must run on the target loop's
own thread) needs `Simulator.call()`.

**So the actual shape: each long-lived async component picks whichever loop its own state-touching
pattern calls for** - a dedicated engine room when it's mostly decoupled I/O touching shared state
rarely (`GDBTCPServer`'s connection handling), sharing `Simulator`'s when it's touching that same
state on nearly every I/O event (`StdioInteractiveRepl`, `MicroPythonDevice`'s raw-REPL `exec()`
family - PR 4) - not a fixed rule either way, and not even a fixed rule *per component*: PR 5
refined this further by showing a component can split, keeping its own engine room for I/O while
bridging the specific calls that touch shared state onto `Simulator`'s instead of moving wholesale
(`GDBTCPServer` keeps its own loop for accepting connections/reading bytes, but bridges
`process_gdb_message()` - the one thing that actually touches `core`/`rp2040` state - via
`target.acall()` per message; see "Resolved during PR 5"). A single process-wide "front door" loop
never ended up getting built: PR 4 confirmed `cli/__init__.py` needed zero changes for phase 5,
since `simulator.submit()` returning a plain `concurrent.futures.Future` means the public sync
API's shape never had to change at all.

The shared building blocks (not duplicated per component):

- `rp2040py/utils/asyncio_loop_thread.py`'s `start_loop_thread(daemon=...)` (**built in PR 2**,
  factored out of PR 1's `Simulator._ensure_loop()`): creates a new loop + a thread running
  `loop.run_forever()`. `daemon=True` (the default, used by `Simulator`) just dies with the
  process; `daemon=False` (used by `GDBTCPServer`, matching Node's `net.Server.listen()` semantics
  - a listening server should keep the process alive by itself) means the owner must explicitly
  `loop.call_soon_threadsafe(loop.stop)` + join during its own teardown, or interpreter shutdown
  hangs forever joining it.
- `Simulator`'s bridge (**built in PR 1**, mirroring the shape `mp_device.py` already had with
  `_result()`/`_await()` around a `concurrent.futures.Future`, generalized here): `start_execution()`
  (fire-and-forget), `call(coro, timeout=None)` (blocking bridge), `acall(coro)` (async bridge),
  and `submit(coro)` (**added in PR 4**: `call()`'s non-blocking counterpart - hands back the
  `concurrent.futures.Future` `asyncio.run_coroutine_threadsafe()` already gives you, instead of
  blocking on `.result()` itself, for a caller that wants a Future of its own to return).
  `GDBTCPServer`'s own connection setup/`close()` doesn't reuse these (it has its own engine room,
  see above) and follows the identical pattern inline instead
  (`asyncio.run_coroutine_threadsafe(...).result(timeout)`) - but its **message processing** now
  does: `StdioInteractiveRepl` (**PR 3**) reuses `Simulator.call()` directly (for the one-time
  `add_reader`/`remove_reader` registration); `MicroPythonDevice` (**PR 4**) reuses
  `Simulator.submit()` for its whole `start_async()`/`exec_async()` API; `GDBTCPServer` (**PR 5**)
  reuses `Simulator.acall()` per `feed_data()` call, via a new `IGDBTarget.acall()` Protocol
  method - none of the three needed any change to `call()`/`acall()`/`submit()` themselves.

| Today (thread-based) | Becomes | File | Status |
|---|---|---|---|
| `threading.Timer(0, self.execute)` reschedule | `async def execute()`, `await asyncio.sleep(0)` to yield | `simulator.py` | **Done** |
| `threading.Timer(0, self.run)` + `RLock` | `async def run()` (continuation only - the first ~1000-step batch stays a synchronous `_step_batch()` call, preserving today's "CTRL write already shows a result" contract); lock deleted entirely | `peripherals/pio.py` | **Done** |
| `connect_blocking()`'s bootstrap thread + `threading.Event` | `simulator.start_execution()`; the `threading.Event`/`connected.wait(timeout)` wait itself is unchanged - still safe, `on_device_connected` fires from the engine-room thread and `Event.set()` is cross-thread-safe | `device/base_device.py` | **Done** |
| `gdb_server.py`'s two direct `self.target.execute()` calls (`c`/`vCont;c`) | `self.target.start_execution()` | `gdb/gdb_server.py` | **Done** |
| `wait_for_shutdown()`'s `time.sleep(0.1)` poll loop | Unchanged - it only reads a plain bool and a `threading.Event`, neither needs to run on the engine-room loop | `simulator.py` | N/A, correctly left alone |
| stdin reader thread + `_wake_r`/`_wake_w` self-pipe | `loop.add_reader(stdin_fd, callback)`, registered on **`Simulator`'s own engine-room loop** (not a separate one - see "Target shape" above) | `cli/stdio_repl.py` | **Done** (POSIX) |
| Non-tty/Windows fallback (`sys.stdin.read()`, no real fd to `add_reader()`) | Kept a small dedicated thread for the blocking read itself (no portable alternative) - the fix was routing each chunk's *send* through `simulator.call()` instead of calling `send_serial_byte()` directly from that thread | `cli/stdio_repl.py` | **Done** |
| `signal.signal(SIGTERM, handler)` + main-thread-affinity checks | **Not `loop.add_signal_handler()`** - see "Resolved during PR 3": that still requires the actual process main thread. PR 4 confirmed no phase ever puts an event loop on the main thread (no front-door loop got built), so this isn't "deferred to phase 5" anymore - it's just not happening under this design. Stays `signal.signal()`, simplified only by losing the "am I the reader thread" branch (no reader thread left) | `cli/stdio_repl.py` | **Done** (simplified, not migrated) |
| GDB accept thread (non-daemon) + 0.2s `socket.settimeout()` poll | `asyncio.start_server()` on `GDBTCPServer`'s own engine room | `gdb/gdb_tcp_server.py` | **Done** |
| GDB per-connection thread + `client_socket.recv()` | `asyncio.StreamReader`/`StreamWriter` from the same `start_server()` connection callback | `gdb/gdb_tcp_server.py` | **Done** |
| `GDBServer.add_connection()`'s `_on_break` (fires on `Simulator`'s engine-room thread) writing a stop-reply directly | Per-connection `on_response` closure does `gdb_loop.call_soon_threadsafe(writer.write, ...)` instead of writing directly - the one new cross-thread hop this phase needed, localized to one closure | `gdb/gdb_tcp_server.py` | **Done** |
| `ThreadPoolExecutor(max_workers=1)` (boot/exec queueing) | `Simulator.submit()` (new, PR 4) + one `asyncio.Lock` per device for the FIFO queueing the executor gave for free | `device/mp_device.py` | **Done** |
| `InteractiveRepl.send()`'s blocking per-byte spin-wait (`_send_byte_blocking()`/`time.sleep(_FIFO_TIMEOUT)`) | Deleted. `send()` now just `_queue(data); return pump()` - the same non-blocking pacing `RawReplRunner` already used, reused instead of a second mechanism | `device/repl_runner.py` | **Done** |
| `_handle_connection()` calling `connection.feed_data(data)` inline, on `GDBTCPServer`'s own engine room (touches `core`/`rp2040` state unbridged) | `await self.target.acall(_feed(data))` - `feed_data()`/`process_gdb_message()` themselves unchanged, just invoked on `Simulator`'s engine room now, per read | `gdb/gdb_tcp_server.py` | **Done** |

The public synchronous API surface (`MicroPythonDevice.start()`/`.exec()`, `BaseDevice` as a
context manager, `KalumaDevice`) still doesn't get to assume its caller is running an event loop -
`mp_device.py`'s own docstring says explicitly who uses this as a library: "a test runner, or a
Thonny-style tool." **PR 4** switched `MicroPythonDevice` over to `Simulator.submit()` (`call()`'s
non-blocking counterpart, built for this) for its whole `start_async()`/`exec_async()` family; the
`_async`/`a`-prefixed variants (already `Future`/`async def`-shaped before this PR) needed no
changes at all - `submit()` already returns the same `concurrent.futures.Future` type, so
`_result()`/`_await()` map onto the engine-room loop exactly as before, just backed by a coroutine
now instead of a `ThreadPoolExecutor` callable. `BaseDevice`/`KalumaDevice` (no raw-REPL `exec()`
to queue behind) still call `connect_blocking()` directly, unchanged - see "Resolved during PR 4".

## Phased migration order

**1 and 2 below landed together as PR 1** (`refactor: give Simulator its own asyncio engine-room
thread`) - the original plan had `pio.py` going alone first, "smallest blast radius." That turned
out to be wrong: `RPPIO.run()`'s reschedule is triggered synchronously from `write_uint32()`,
called from `RP2040`'s bus dispatch, called from `execute_instruction()`, which only ever runs
inside `Simulator.execute()`. An async `RPPIO.run()` has nowhere to schedule its continuation
(`asyncio.get_running_loop()`) unless `Simulator.execute()` is already running on a loop - so they
had to move together. Left here for the record, since the same kind of hidden coupling is worth
checking for before starting phase 3+ too.

1. ~~**`peripherals/pio.py`** (`RPPIO.run()`)~~ **Done, combined with 2.** `RPPIO._lock` deleted.
   The first ~1000-step batch stays a synchronous `_step_batch()` call (preserves "CTRL write
   already shows a result" for short PIO programs, which is most of what `test_pio.py` checks);
   only the continuation - if a program is still going after that, e.g. it wraps/waits forever -
   is scheduled as a task via `asyncio.get_running_loop().create_task(self.run())`.
2. ~~**`simulator.py`** (`Simulator.execute()`)~~ **Done.** `execute()` is now `async def`,
   `Simulator._ensure_loop()` lazily creates the permanent engine-room thread on first real use
   (not eagerly in `__init__` - plenty of callers construct a `Simulator` and never run it).
   `wait_for_shutdown()`/`ShutdownRequest` turned out **not** to need any change at all - see "Two
   fixed roles" above; only things that actually touch execute()/RPPIO/USBCDC state need to go
   through the engine room.
3. ~~**`gdb/gdb_tcp_server.py`**~~ **Done** (`refactor: GDBTCPServer on asyncio.start_server()`).
   Rewritten on `asyncio.start_server()`, on its **own** engine room (not `Simulator`'s - see
   "Target shape" above for why). `gdb_server.py`/`gdb_connection.py`'s actual protocol logic
   (`process_gdb_message()`, `feed_data()`) is untouched, exactly as planned - zero changes.
   Deleted the accept thread, the per-connection thread, and the 0.2s poll workaround in one pass.
   The `_on_break` cross-thread hop (Simulator's engine room → GDB's own loop) is real and was
   needed, resolved by having the per-connection `on_response` closure itself do
   `gdb_loop.call_soon_threadsafe(writer.write, ...)` rather than writing directly - `gdb_server.py`
   doesn't know or care that this is cross-thread. Two bugs found and fixed during this phase, both
   real, both worth remembering for phase 4/5's own use of `asyncio.start_server()`-style code -
   see "Resolved during PR 2" below.
4. ~~**`device/repl_runner.py`, `cli/stdio_repl.py`**~~ **Done** (`refactor: stdio_repl.py on
   add_reader(), sharing Simulator's engine room`). `device/raw_repl.py` ended up untouched -
   `RawReplRunner`'s `feed()`/pacing already used `_queue()`/`pump()` correctly, nothing to
   migrate there. Found and fixed a real bug along the way, not just thread-count cleanup: the old
   reader thread called `send()` → `_send_byte_blocking()` → `cdc.send_serial_byte()` directly,
   from a different thread than `Simulator.execute()` - the same unguarded `USBCDC.tx_fifo` race
   that once corrupted raw-REPL uploads (`mp_device.py:86-96`'s comment), just never reproduced
   for interactive stdin specifically. See "Resolved during PR 3" for the fix and two more
   *related but out-of-scope* races found while tracing this.
5. ~~**`device/mp_device.py`'s sync facade**~~ **Done** (`refactor: mp_device.py on
   Simulator.submit()/asyncio.Lock`). The highest-risk step for external callers turned out to need
   no `cli/__init__.py` changes at all: `cli/__init__.py`'s calls into `device.start()`/
   `device.exec()` only ever use the already-public, already-blocking sync API, whose `Future`-
   returning shape (`_result()`/`_await()`) didn't change - confirming "Target shape" above's
   updated read that a front-door loop was never actually needed. `ThreadPoolExecutor(max_workers=1)`
   became `Simulator.submit()` (new, built this phase) + one `asyncio.Lock` per device for the same
   FIFO queueing. Closed one of the two races PR 3 found but deliberately didn't fix -
   `mp_device.py`'s own `Ctrl-C`/`Ctrl-A` kickoff bytes, previously sent from the executor's worker
   thread, now genuinely run on the engine room - see "Resolved during PR 4". The other
   (`gdb_server.py`'s `process_gdb_message()` touching `rp2040` state from `GDBTCPServer`'s own loop
   with no bridge at all) stayed out of scope on purpose, deferred to its own phase - see item 6
   below.

6. ~~**`gdb/gdb_server.py`'s `process_gdb_message()` cross-thread race**~~ **Done** (`refactor:
   bridge process_gdb_message() onto Simulator's engine room`). Found during PR 3, explicitly
   scoped out of PR 4 for being bigger and riskier than mp_device.py's (revisits PR 2's "GDB gets
   its own engine room" call). Turned out **not** to actually revisit that call once built - PR 2's
   reasoning ("connection I/O has nothing to do with CPU/peripheral state") is still true;
   `process_gdb_message()` itself does, so only *that* got bridged
   (`IGDBTarget.acall()`, new), while `GDBTCPServer` keeps its own engine room for
   accept/read/write exactly as PR 2 built it. `GDBServer.process_gdb_message()`/
   `GDBConnection.feed_data()` stayed **completely unchanged**, zero lines touched, matching PR 2's
   own "protocol logic untouched" precedent. See "Resolved during PR 5" for the batch-latency
   reasoning (why bridging every register read doesn't reintroduce the "front door" loop's latency
   worries) and the new test coverage this phase added (nothing previously drove
   `process_gdb_message()` through the real TCP/async path at all).

This closes every migration target this document ever named. Each phase landed as its own PR with
its own full test-suite-green checkpoint - this was explicitly *not* a stop-the-world rewrite; the
codebase stayed shippable after every phase (confirmed after every PR: 450+/450+ tests × 10 runs,
mypy/ruff clean, live pty-based SIGTERM/Ctrl+X and GDB continue/Ctrl-C/continue smoke tests, PR 4's
real-firmware exec/interactive/large-paste smoke tests, PR 5's real-firmware "interrupt a live
target and immediately read registers" stress smoke test - see each PR's own commit message for
exact numbers), with threads and asyncio coexisting in between throughout.

## Resolved during PR 1

- **`await asyncio.sleep(0)` yield cost vs `threading.Timer(0, fn)`.** Measured (real MicroPython
  UF2 boot via `rp2040py bench --image ... `, git-stashed A/B): 2,700,777 instr/sec after vs.
  2,717,614 instr/sec before — under 1% difference, noise-level. No throughput regression.
- **Testing didn't end up needing `pytest-asyncio` at all.** For `test_simulator.py`: rather than
  fight `execute()`'s coroutine-ness in the test, it now calls the new synchronous
  `Simulator._execute_batch()` helper directly (same one `execute()`'s own loop calls between
  `sleep(0)` yields) — deterministic, no event loop involved, no dependency added. For
  `test_pio.py`: see the next bullet, a bigger and more interesting finding than "add a
  dependency."
- **A background `run_forever()` loop-thread for a test fixture is actively harmful, not just
  unnecessary.** First attempt at `test_pio.py`'s fixture gave it its own persistent
  `run_forever()` background thread (mirroring `Simulator`'s engine room) and bridged every driver
  call through `run_coroutine_threadsafe(...).result()`. Correct, but made the suite 30-90x slower
  (0.7s → up to 65s) and wildly variable run to run: several PIO test programs legitimately never
  stop on their own (`wait irq`, an infinite `jmp`-to-self) — in production that's *correct*, a
  real Simulator should run those as fast as possible — but in a test harness nothing else needs
  the loop's attention between two adjacent Python statements, so the continuation task span at
  full speed for however long it took some later bridged call to win its turn in a queue the
  continuation kept refilling, burning real CPU for zero behavioral benefit (confirmed:
  `irq_updated()` already re-checks a waiting machine *synchronously* on every IRQ register write,
  so the background task wasn't even needed for correctness). Fixed by switching the fixture to
  `loop.run_until_complete()` per call instead of a persistent background thread - bounds a
  continuation's progress to exactly what the awaited call needs, then stops. Worth remembering for
  phase 3/4's own test fixtures if anything there has a similarly never-self-stopping loop (a GDB
  connection kept open, an interactive REPL session).
- **Version floor.** `requires-python = ">=3.10"` (`pyproject.toml`) already covers everything used
  in PR 1 (`asyncio.run_coroutine_threadsafe`, `asyncio.new_event_loop`) — no floor bump needed;
  expected to hold for the rest of the migration too (`add_reader`/`add_signal_handler`,
  `start_server`, `Event`/`Lock`/`Condition` are all far older than 3.10).

## Resolved during PR 2

- **`host=""` in `asyncio.start_server()` is not the same as `socket.bind(("", port))`.** The
  original raw-socket implementation was always a single explicit `AF_INET` socket. An unset/empty
  host string lets `asyncio`'s own `getaddrinfo()` resolve to *multiple* addresses (observed: both
  an IPv6 `::` and an IPv4 `0.0.0.0` socket), each bound independently - with `port=0` (OS-assigned,
  what every test uses), each separate `bind()` call can get a *different* port number.
  `self.port`/`server.sockets[0]` only ever reflected one of them, so an IPv4 test client
  connecting to that port intermittently hit the wrong socket's port instead. Confirmed as a real,
  reproducible flake (~50% of full-suite runs) before pinning `family=socket.AF_INET` explicitly.
  Worth remembering for phase 5 too if `cli/__init__.py`'s eventual front-door loop ever binds a
  listening socket of its own.
- **A loop-thread stopping abruptly while a task is still `await`ing something orphans it, loudly.**
  `close()` stopping the loop right after closing the listening socket left an in-flight
  `_handle_connection()` task (still blocked in `reader.read()` for a client that hadn't
  disconnected) parked mid-`await` forever - harmless in effect (matches the old per-connection
  daemon thread just being abandoned at shutdown), but asyncio reports it loudly at interpreter
  shutdown ("Task was destroyed but it is pending!"), unlike the old design's silent thread
  abandonment. Fixed by tracking in-flight connection tasks in a set and explicitly `cancel()`ing
  (then `gather(..., return_exceptions=True)`-awaiting) them as part of `close()`, before stopping
  the loop. Worth the same check for any phase 4/5 code that stops a loop with tasks possibly still
  pending.
- **`RP2040.on_break`/GDB's synchronous breakpoint callback chain didn't need to change**, exactly
  as expected - `gdb_server.py:233-239`'s `_on_break` still just mutates `core.pc` and iterates
  connections synchronously from inside `execute()`'s own call chain (a real breakpoint hit, on
  `Simulator`'s engine-room thread). The one new thing needed was entirely on the `GDBTCPServer`
  side: the per-connection `on_response` closure now always schedules the actual socket write via
  `call_soon_threadsafe`, regardless of which thread calls it - safe and correct whether that's
  `feed_data()` (already on GDB's own loop thread) or `_on_break` (a different thread entirely).
- **No `pytest-asyncio` needed here either**, matching PR 1's finding. `test_gdb_tcp_server.py`'s
  fakes just construct a real `GDBTCPServer` (which always spins up its own real engine-room
  thread) and make plain synchronous assertions against it - no test needed to itself run inside a
  coroutine.

## Resolved during PR 3

- **The `USBCDC.tx_fifo` race, found while designing this phase, not while debugging a symptom.**
  `StdioInteractiveRepl`'s reader thread called `send()` → `cdc.send_serial_byte()` directly, racing
  whatever thread drives `execute_instruction()`'s own FIFO pull
  (`RPUSBController._on_endpoint_read`) - structurally identical to the raw-REPL-upload corruption
  `mp_device.py:86-96` already documents fixing, just never reproduced for interactive stdin.
  Closed as a consequence of registering `add_reader()` directly on `Simulator`'s engine-room loop
  (see "Target shape" above) rather than as a separate fix - once the callback runs on the right
  thread, `send_serial_byte()` needs no bridging at all. Verified live: a 601-byte pasted line (over
  `USBCDC`'s 512-byte `TX_FIFO_SIZE`) round-trips through a real MicroPython session with no
  truncation.
- **`loop.add_signal_handler()` cannot replace `signal.signal()` yet.** The original migration
  sketch assumed it could, for this phase. It can't: `add_signal_handler()` still requires being
  called from the actual process main thread (a CPython `signal`-module restriction, not something
  asyncio lifts), and `Simulator`'s engine-room loop runs on a background thread, not the main one.
  Only phase 5 - if `cli/__init__.py`'s `main()` itself becomes the main-thread event loop - would
  make this viable. `_on_sigterm` stays on `signal.signal()` this phase, just loses the "am I the
  reader thread" branch since there's no reader thread left to accidentally join from within
  itself.
- **Two more instances of the exact same tx_fifo-class race found while tracing this, left
  unfixed on purpose:** `mp_device.py`'s `_exec_blocking()` sends its initial `Ctrl-C`/`Ctrl-A`
  bytes from the `ThreadPoolExecutor` worker thread, not the engine room - squarely phase 5's
  problem (that's the whole point of migrating `mp_device.py`'s sync facade). `gdb_server.py`'s
  `process_gdb_message()` reads/writes `core.registers`/memory directly from whatever thread calls
  `feed_data()` - after PR 2, `GDBTCPServer`'s own engine-room thread, never bridged into
  `Simulator`'s - pre-existing since before PR 2 even, not a regression from this migration, but
  not yet assigned to a phase. Added as an open question below rather than fixed inline; fixing it
  means every `process_gdb_message()` call would need `Simulator.call()`/`.acall()`, likely
  reopening the "does GDB need `Simulator`'s engine room after all" question PR 2 closed the other
  way.
- **No `pytest-asyncio` needed here either**, matching PR 1 and PR 2's finding. `test_stdio_repl.py`
  still drives a real `Simulator()` + a real pty synchronously; nothing needed to run inside a
  coroutine itself.

## Resolved during PR 4

- **The `mp_device.py` Ctrl-C/Ctrl-A `tx_fifo` race PR 3 found but deferred, closed the same way PR
  3 closed its own.** `RawReplRunner.start()` (called from `_aexec()`, now a coroutine genuinely
  running on the engine room) sends its initial bytes on the same thread that drives
  `execute_instruction()`'s own USBCDC FIFO pull, by construction - no bridging needed, same shape
  as PR 3's stdin fix. Verified live: a 1604-byte script (over `USBCDC`'s 512-byte `TX_FIFO_SIZE`)
  exec'd correctly end to end against a real MicroPython image.
- **`asyncio.Lock` replacing `ThreadPoolExecutor(max_workers=1)` is a drop-in for FIFO queueing,
  but only if every caller of the queued state actually runs on the lock's own loop.** Discovered
  the hard way: `tests/test_device.py`'s existing fixtures simulated "the device replying" by
  calling `cdc.on_serial_data(...)` directly from a plain background `threading.Thread` - fine
  under the old design, since `_exec_blocking()`'s `done` was a `threading.Event` (thread-safe by
  construction from any thread). Once `done` became an `asyncio.Event` (needed so `_aexec()` can
  `await` it without blocking the engine room), calling `.set()` on it from a foreign thread no
  longer reliably wakes the coroutine awaiting it - `asyncio.Event`/`Condition`/etc. are only safe
  to touch from the loop thread they're bound to, unlike their `threading` counterparts. Fixed by
  routing the test fixtures' synthetic replies through `simulator.call()` (built in PR 1) instead
  of calling `on_serial_data()` directly - which also makes the tests match real production
  topology more closely than before (`on_serial_data` only ever fires on the engine-room thread for
  real firmware; the tests' old direct-call pattern was already a simplification the old
  `threading.Event`-based design happened to tolerate). Worth remembering for any future test
  fixture that feeds a `Simulator`-driven callback from outside the engine room.
- **`_result()`/`_await()` (the module-level `Future`/`TimeoutError`-normalizing helpers) needed
  zero changes.** `simulator.submit()` returns the exact same `concurrent.futures.Future` type
  `ThreadPoolExecutor.submit()` did - confirms the doc's own prediction ("the `_async`/`a`-prefixed
  variants... will map onto the engine-room loop directly with no bridging needed at all").
- **`cli/__init__.py` needed zero changes.** Grepped every `_executor`/`ThreadPoolExecutor`
  reference in `src/` before starting - all were inside `mp_device.py` itself. Answers "Target
  shape"'s open question about a shared front-door loop for real: no, phase 5 never needed one
  either, matching what PR 1-3 already found independently for their own components.
- **No `pytest-asyncio` needed here either**, matching every prior PR's finding.

## Resolved during PR 5

- **Bridging `process_gdb_message()` doesn't reopen PR 2's "GDB gets its own engine room" decision
  - it just adds one bridge point on top of it.** PR 2's reasoning ("connection I/O has nothing to
  do with CPU/peripheral state") is still correct; what changed is recognizing that
  `process_gdb_message()` *itself* (not the connection handling around it) does touch that state.
  Fixed by wrapping the one call that needs it (`connection.feed_data(chunk)`, invoked from
  `_handle_connection()`) in `await self.target.acall(_feed(chunk))` - `GDBTCPServer` keeps its own
  engine room for accept/read/write exactly as PR 2 built it; only message *processing* moved.
  `GDBServer.process_gdb_message()`/`GDBConnection.feed_data()` needed zero changes, matching PR
  2's own "protocol logic untouched" precedent for the second time.
- **`acall()`, not `call()`, is the only correct bridge from inside `_handle_connection()`.**
  `Simulator.call()` blocks the calling thread until the bridged coroutine finishes -
  `_handle_connection()` already runs as a coroutine on `GDBTCPServer`'s own loop, so `call()`
  there would freeze that whole loop, stalling every other connection on the same server while one
  waits. `acall()` (built in PR 1, never actually used until now) is the non-blocking, cooperative
  bridge for a caller that already has its own running loop - exactly this case.
- **The "front door" discussion's batch-latency worry doesn't apply here in practice.**
  `Simulator.execute()` only yields between batches of up to 1,000,000 instructions - if `g`/`p`/`m`
  had to wait for a batch boundary, that's up to ~370ms of GDB latency per register read (at PR 1's
  measured ~2.7M instr/sec). Not actually paid: GDB only sends register/memory queries while the
  target is *stopped*, and `stop()` ends `execute()`'s task (its `while not self.stopped` loop
  exits) almost immediately, leaving the engine room idle until the next bridged call - picked up
  on the very next loop iteration, no batch to wait through. Confirmed live: 30 iterations of
  "interrupt a live target with `\x03`, immediately read all registers via `g`, resume with `c`"
  against a real, actively-running MicroPython image completed cleanly with plausible, varying PC
  values each time - no hang, no exception, no stale/torn state.
- **`on_response()`'s existing `call_soon_threadsafe` (built in PR 2 for `_on_break`) needed zero
  changes**, despite `feed_data()` - and therefore every `on_response()` call inside it - now
  running on a different thread than before. It already treated "called from a foreign thread" as
  the normal case, not a new one; PR 5 just added a second caller that happens to also be foreign.
- **No coverage gap left unaddressed**: `tests/test_gdb_tcp_server.py` never actually drove
  `process_gdb_message()` through the real TCP/async path before this phase (`_FakeTarget`'s
  `rp2040` was never driven) - a bridge that silently no-op'd wouldn't have been caught by the
  existing suite. Added two new tests against a **real** `Simulator()`: a `g` register read and a
  `P`-write-then-`p`-read round trip, both over a real socket connection.
- **No `pytest-asyncio` needed here either**, matching every prior PR's finding.

## Open questions / not decided

- **Windows stdin.** `loop.add_reader()` on `ProactorEventLoop` (asyncio's default on Windows)
  only supports sockets, not arbitrary file descriptors or pipes — matches today's situation
  exactly (no raw mode there either), so `cli/stdio_repl.py`'s non-tty/Windows fallback (PR 3) kept
  a small dedicated thread for the blocking `sys.stdin.read()` call itself rather than trying
  `add_reader()`/`run_in_executor()` there - untested whether `run_in_executor()` would actually be
  better than what's there now, since the fallback already isn't the primary interactive path.
- **~~Is a single shared "front door" loop worth building?~~ Answered: no.** All four migration
  targets (`Simulator`, `GDBTCPServer`, `StdioInteractiveRepl`, `MicroPythonDevice`) picked
  whichever loop suits their own state-touching pattern - two with their own engine room, two
  sharing `Simulator`'s - with zero cross-loop coordination problems, and `cli/__init__.py` never
  needed to become an `asyncio.run()`-hosting entry point at all. See "Resolved during PR 4".
- **Could `GDBTCPServer` drop its own engine-room thread entirely and run
  `asyncio.start_server()` directly on `Simulator`'s loop, the same way `StdioInteractiveRepl`
  shares it via `add_reader()`?** Raised while building PR 5, not resolved there on purpose (PR 5's
  scope was bridging `process_gdb_message()`, not re-litigating PR 2's loop-ownership choice).
  Would remove one background thread (down to one shared engine room for everything) and the
  `IGDBTarget.acall()` bridge PR 5 just added, since `feed_data()` would already be running on the
  right thread directly. The blocker isn't state-touching, it's lifecycle: `GDBTCPServer`'s thread
  is deliberately non-daemon (a listening GDB server keeps the process alive by itself, matching
  Node's `net.Server.listen()`), while `Simulator`'s engine room is daemon - merging them means
  deciding how a `Simulator` with a GDB server attached keeps the process alive without a
  non-daemon thread of its own, which isn't a one-line change. Worth a dedicated look if another
  reason to touch `GDBTCPServer`'s loop ownership comes up; not proposed as its own phase for now.

## Considered and rejected (for now)

- **Partial migration: asyncio only for `stdio_repl.py`, threads everywhere else.** This is what
  today's two fixes already approximate as far as they can *without* touching `Simulator`, and
  it's exactly why they still need a self-pipe, a reader thread, and thread-affinity checks —
  `stdio_repl.py` alone can't stop being thread-based while the thing it's coordinating with
  (`Simulator.execute()`, driven by its own thread) still is. A half migration doesn't remove a
  concurrency model, it adds a second one to reason about at the boundary. Rejected as a permanent
  end state; the phased plan above still produces intermediate states with both models present,
  but only as waypoints toward all-async, not as the destination.
- **Doing this now, inside the current CI-stabilization work.** The last two commits on
  `stdio_repl.py` exist to make the *current*, thread-based design internally consistent (one
  shutdown rule instead of two). Swapping the whole concurrency model out from under that at the
  same time would make it impossible to tell which change fixed or broke what. This document
  exists so the option is scoped and ready to pick up as its own effort, not to be started
  mid-flight.
