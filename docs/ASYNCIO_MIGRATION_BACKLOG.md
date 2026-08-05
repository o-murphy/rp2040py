# Full asyncio migration

**Status:** PR 1 (`simulator.py` + `peripherals/pio.py`) and PR 2 (`gdb/gdb_tcp_server.py`) landed -
see "Phased migration order" below. Everything else is still a design sketch, not implemented or
committed to.

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
- `src/rp2040py/cli/stdio_repl.py`: a whole session's worth of bugs chased through this file (see
  [docs/BACKLOG.md](BACKLOG.md#unified-process-shutdown-coordinator-ctrlx---expect-text-sigterm--done)
  and the two fixes landed just before this document was written) — a stdin-reader thread that
  wasn't joinable, then wasn't joined correctly, a SIGTERM handler that only works from the main
  thread, a `_wake_r`/`_wake_w` self-pipe whose entire job is working around Python not having a
  way to cancel a blocked `os.read()` from another thread.
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
fake target in tests stayed exactly as simple as it was. **So the actual current shape is: every
long-lived async component gets its own independent engine room, bridged into from synchronous
callers via the same small pattern - not one shared loop everything lives on.** Whether a single
process-wide "front door" loop ever gets built is now an open question for phase 5 (`cli/__init__.py`'s
`main()`) rather than a foregone conclusion - see "Open questions" below.

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
  (fire-and-forget), `call(coro, timeout=None)` (blocking bridge), `acall(coro)` (async bridge).
  `GDBTCPServer` doesn't reuse these specifically (it has its own engine room, see above) but
  follows the identical pattern inline (`asyncio.run_coroutine_threadsafe(...).result(timeout)`
  in `__init__`/`close()`) - worth promoting into a small shared bridge class if a third component
  needs the same shape.

| Today (thread-based) | Becomes | File | Status |
|---|---|---|---|
| `threading.Timer(0, self.execute)` reschedule | `async def execute()`, `await asyncio.sleep(0)` to yield | `simulator.py` | **Done** |
| `threading.Timer(0, self.run)` + `RLock` | `async def run()` (continuation only - the first ~1000-step batch stays a synchronous `_step_batch()` call, preserving today's "CTRL write already shows a result" contract); lock deleted entirely | `peripherals/pio.py` | **Done** |
| `connect_blocking()`'s bootstrap thread + `threading.Event` | `simulator.start_execution()`; the `threading.Event`/`connected.wait(timeout)` wait itself is unchanged - still safe, `on_device_connected` fires from the engine-room thread and `Event.set()` is cross-thread-safe | `device/base_device.py` | **Done** |
| `gdb_server.py`'s two direct `self.target.execute()` calls (`c`/`vCont;c`) | `self.target.start_execution()` | `gdb/gdb_server.py` | **Done** |
| `wait_for_shutdown()`'s `time.sleep(0.1)` poll loop | Unchanged - it only reads a plain bool and a `threading.Event`, neither needs to run on the engine-room loop | `simulator.py` | N/A, correctly left alone |
| stdin reader thread + `_wake_r`/`_wake_w` self-pipe | `loop.add_reader(fd, callback)` (POSIX); `loop.run_in_executor()` wrapping the existing blocking fallback (Windows/non-tty) | `cli/stdio_repl.py` | Not started |
| `signal.signal(SIGTERM, handler)` + main-thread-affinity checks | `loop.add_signal_handler(signal.SIGTERM, callback)` | `cli/stdio_repl.py` | Not started |
| GDB accept thread (non-daemon) + 0.2s `socket.settimeout()` poll | `asyncio.start_server()` on `GDBTCPServer`'s own engine room | `gdb/gdb_tcp_server.py` | **Done** |
| GDB per-connection thread + `client_socket.recv()` | `asyncio.StreamReader`/`StreamWriter` from the same `start_server()` connection callback | `gdb/gdb_tcp_server.py` | **Done** |
| `GDBServer.add_connection()`'s `_on_break` (fires on `Simulator`'s engine-room thread) writing a stop-reply directly | Per-connection `on_response` closure does `gdb_loop.call_soon_threadsafe(writer.write, ...)` instead of writing directly - the one new cross-thread hop this phase needed, localized to one closure | `gdb/gdb_tcp_server.py` | **Done** |
| `ThreadPoolExecutor(max_workers=1)` (boot/exec queueing) | `Simulator.call()`/`.acall()` (built, unused by this file yet) instead of a second, separate executor-based serialization point | `device/mp_device.py` | Not started |
| `BaseReplRunner._send_byte_blocking()`'s spin-wait (`time.sleep(_FIFO_TIMEOUT)`) | `await asyncio.sleep(_FIFO_TIMEOUT)`, or better, an `asyncio.Condition` the FIFO signals on drain | `device/repl_runner.py` | Not started |

The public synchronous API surface (`MicroPythonDevice.start()`/`.exec()`, `BaseDevice` as a
context manager, `KalumaDevice`) still doesn't get to assume its caller is running an event loop -
`mp_device.py`'s own docstring says explicitly who uses this as a library: "a test runner, or a
Thonny-style tool." `Simulator.call()`/`.acall()` (built in PR 1) is exactly the bridge those need;
`mp_device.py` just hasn't been switched over to use it yet (phase 5). The `_async`/`a`-prefixed
variants (`start_async`/`astart`, `exec_async`/`aexec`, already `Future`/`async def`-shaped today)
will map onto the engine-room loop directly with no bridging needed at all once that lands.

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
4. **`device/repl_runner.py`, `device/raw_repl.py`, `cli/stdio_repl.py`**. The REPL/stdin layer,
   bridging to the engine room via `Simulator.call()`/`.acall()` (already built) instead of
   inventing its own thread-coordination. `StdioInteractiveRepl` in particular gets simpler in the
   same direction this session's two prior fixes already pushed it (`on_quit` as a required,
   non-blocking signal) — under asyncio that signal is naturally `shutdown_event.set()` from an
   `add_reader` callback, no thread-affinity question to even ask.
5. **`device/mp_device.py`'s sync facade + `cli/__init__.py`**. Last, because it's the highest-risk
   step for external callers: the `ThreadPoolExecutor`-backed sync API becomes
   `Simulator.call()`/`.acall()` (already built, just not wired up here yet), and
   `cli/__init__.py`'s `main()` becomes the thing that actually calls `asyncio.run()` for the
   front-door loop.

Each remaining phase should land as its own PR with its own full test-suite-green checkpoint —
this is explicitly *not* a stop-the-world rewrite; the codebase is shippable after every phase
(confirmed after 1+2 and again after 3: 450/450 tests × 10 runs, mypy/ruff clean, a live GDB
continue/Ctrl-C/continue smoke test - see each PR's own commit message for exact numbers), with
threads and asyncio coexisting in between. `stdio_repl.py` staying thread-based for now already
bridges into `Simulator`'s engine room fine via the one-word `start_execution()` swap PR 1 already
made in `gdb_server.py` - phase 4 doesn't block on phase 5.

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

## Open questions / not decided

- **Windows stdin.** `loop.add_reader()` on `ProactorEventLoop` (asyncio's default on Windows)
  only supports sockets, not arbitrary file descriptors or pipes — matches today's situation
  exactly (no raw mode there either), so the existing `sys.stdin.read()` fallback
  (`cli/stdio_repl.py`'s `else` branch) would move into `loop.run_in_executor()` rather than
  `add_reader()`. Not a regression, but confirm `SelectorEventLoop` isn't a better fit there before
  committing to `run_in_executor` — untested either way.
- **Is a single shared "front door" loop worth building in phase 5, given phases 1-3 each ended up
  with their own independent engine room instead?** Two independent components (`Simulator`,
  `GDBTCPServer`) doing this with zero coordination problems so far suggests a shared loop might
  not be needed at all - `cli/stdio_repl.py` (phase 4) could plausibly get its own third engine
  room the same way, and phase 5's `mp_device.py` sync facade only ever needed to bridge into
  `Simulator`'s anyway. Revisit once phase 4 is built and it's clear whether anything actually
  needs to coordinate *across* two of these independent loops (right now nothing does).

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
