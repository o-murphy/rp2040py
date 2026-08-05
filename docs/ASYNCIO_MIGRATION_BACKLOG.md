# Full asyncio migration — not started

**Status:** design sketch only. Nothing below is implemented or committed to. Written up because
it's a real, credibly-scoped answer to a question that keeps recurring in practice (see "Why this
document exists"), not because it's been decided on.

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
- `src/rp2040py/gdb/gdb_tcp_server.py:22-36`: a non-daemon accept thread (so a listening GDB
  server keeps the process alive, matching Node's `net.Server.listen()`) that can't be unblocked
  by closing its socket out from under it — confirmed the hard way, an earlier close()-only
  version hung forever — so it's polled with a 0.2s `socket.settimeout()` instead.

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

## Target shape: one thread, one loop, everything else is a coroutine

One thread runs `asyncio`'s event loop. Everything that is a separate OS thread today becomes a
coroutine, a task, or a loop callback on that one loop instead:

| Today (thread-based) | Becomes | File |
|---|---|---|
| `threading.Timer(0, self.execute)` reschedule | `async def execute()`, `await asyncio.sleep(0)` to yield | `simulator.py` |
| `threading.Timer(0, self.run)` + `RLock` | `async def run()`, `await asyncio.sleep(0)`; lock deleted entirely (nothing left to race with) | `peripherals/pio.py` |
| stdin reader thread + `_wake_r`/`_wake_w` self-pipe | `loop.add_reader(fd, callback)` (POSIX); `loop.run_in_executor()` wrapping the existing blocking fallback (Windows/non-tty) | `cli/stdio_repl.py` |
| `signal.signal(SIGTERM, handler)` + main-thread-affinity checks | `loop.add_signal_handler(signal.SIGTERM, callback)` | `cli/stdio_repl.py` |
| `wait_for_shutdown()`'s `time.sleep(0.1)` poll loop | `await shutdown_event.wait()` (`asyncio.Event`) | `simulator.py` |
| GDB accept thread (non-daemon) + 0.2s `socket.settimeout()` poll | `asyncio.start_server()` | `gdb/gdb_tcp_server.py` |
| GDB per-connection thread + `client_socket.recv()` | `asyncio.StreamReader`/`StreamWriter` from the same `start_server()` connection callback | `gdb/gdb_tcp_server.py` |
| `connect_blocking()`'s bootstrap thread + `threading.Event` | `await connected_event.wait()` directly on the loop thread — no thread needed at all, since `execute()` now runs as a task on the same loop instead of a separate thread | `device/base_device.py` |
| `ThreadPoolExecutor(max_workers=1)` (boot/exec queueing) | `asyncio.Lock()` (or just natural task ordering — the loop already serializes coroutines that don't `await`) around the raw-REPL exec path | `device/mp_device.py` |
| `BaseReplRunner._send_byte_blocking()`'s spin-wait (`time.sleep(_FIFO_TIMEOUT)`) | `await asyncio.sleep(_FIFO_TIMEOUT)`, or better, an `asyncio.Condition` the FIFO signals on drain | `device/repl_runner.py` |

The public synchronous API surface (`MicroPythonDevice.start()`/`.exec()`, `BaseDevice` as a
context manager, `KalumaDevice`) is the one thing that does **not** get to assume its caller is
running an event loop — `mp_device.py`'s own docstring says explicitly who uses this as a library:
"a test runner, or a Thonny-style tool." For those, the loop still runs on a dedicated background
thread (started once, not per call), and the sync methods become thin bridges via
`asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)` — replacing today's
`ThreadPoolExecutor(max_workers=1)` with the loop itself as the single serialization point. This
is *one* thread instead of zero, which sounds like it contradicts "one thread, no threads" above —
but it's one thread total, hosting every "process" from the table, instead of five-plus
independent ones each solving their own coordination problem. The `_async`/`a`-prefixed variants
(`start_async`/`astart`, `exec_async`/`aexec`, already `Future`/`async def`-shaped today) map onto
the loop directly with no bridging needed at all — those callers benefit immediately.

## Phased migration order

Bottom-up: start with the piece that has no public API of its own and the smallest blast radius,
finish with the piece every external caller actually touches.

1. **`peripherals/pio.py`** (`RPPIO.run()`). Self-contained, no external API, existing test
   coverage (`test_pio.py`) already exercises `run()`/`stop()` synchronously and would need the
   least adaptation (call `asyncio.run()` around a test body, or drive the loop manually a fixed
   number of iterations). Proves the `threading.Timer(0, fn)` → `async def` + `sleep(0)` pattern
   works before applying it to the higher-stakes `Simulator.execute()`. Deletes `RPPIO._lock`
   entirely as part of this step — that's the concrete, checkable payoff for doing this one first.
2. **`simulator.py`** (`Simulator.execute()`, `wait_for_shutdown()`, `ShutdownRequest`). The
   central piece everything else depends on. `execute()` becomes a task the loop runs; anything
   that used to call `simulator.stop()` from another thread now calls it from a coroutine, or via
   `loop.call_soon_threadsafe()` if it's still genuinely off-loop (during the transition period
   before step 5). `ShutdownRequest` swaps its `threading.Event` for `asyncio.Event`.
3. **`gdb/gdb_tcp_server.py`** + `gdb/gdb_connection.py`. Rewrite on `asyncio.start_server()`.
   `gdb_server.py`'s actual protocol logic (`process_gdb_message()`) is pure and synchronous
   already (string in, string out) — untouched. Deletes the accept thread, the per-connection
   thread, and the 0.2s poll workaround in one pass, since all three exist for the same reason.
4. **`device/base_device.py`, `device/repl_runner.py`, `device/raw_repl.py`,
   `cli/stdio_repl.py`**. The REPL/stdin layer, now that it has an `execute()` task to talk to
   instead of a thread. `StdioInteractiveRepl` in particular gets simpler in the same direction
   today's two fixes already pushed it (`on_quit` as a required, non-blocking signal) — under
   asyncio that signal is naturally `shutdown_event.set()` from an `add_reader` callback, no
   thread-affinity question to even ask.
5. **`device/mp_device.py`'s sync facade + `cli/__init__.py`**. Last, because it's the highest-risk
   step for external callers: the `ThreadPoolExecutor`-backed sync API becomes the
   `run_coroutine_threadsafe()` bridge described above, and `cli/__init__.py`'s `main()` becomes
   the thing that actually calls `asyncio.run()`.

Each phase should land as its own PR with its own full test-suite-green checkpoint — this is
explicitly *not* a stop-the-world rewrite; the codebase should be shippable after every phase,
with threads and asyncio coexisting in between phases 1-4 (`pio.py` and `simulator.py` can be
async while `stdio_repl.py` is still thread-based, since they don't share mutable state directly —
only phase 5's bridge needs to exist before *any* async piece is reachable from the still-sync CLI
entry point, so phase 5's bridging mechanism should probably be scaffolded early, even if it has
nothing but `simulator.py` behind it at first).

## Open questions / not decided

- **Windows stdin.** `loop.add_reader()` on `ProactorEventLoop` (asyncio's default on Windows)
  only supports sockets, not arbitrary file descriptors or pipes — matches today's situation
  exactly (no raw mode there either), so the existing `sys.stdin.read()` fallback
  (`cli/stdio_repl.py`'s `else` branch) would move into `loop.run_in_executor()` rather than
  `add_reader()`. Not a regression, but confirm `SelectorEventLoop` isn't a better fit there before
  committing to `run_in_executor` — untested either way.
- **Testing.** None of the current test suite awaits anything (`pytest-asyncio` is not a
  dependency today — checked `pyproject.toml`). Every test that drives `execute()`, `run()`, the
  GDB server, or a REPL synchronously (`test_simulator.py`, `test_simulator_shutdown.py`,
  `test_gdb_tcp_server.py`, `test_pio.py`, `test_stdio_repl.py`, `test_repl_runner.py`,
  `test_raw_repl.py`, `test_device.py`, `test_kaluma_device.py` — most of the suite, honestly)
  needs either an `asyncio.run()` wrapper or a switch to `pytest-asyncio`. This is likely the
  single largest line-count cost of the whole migration, not the production code itself.
- **`await asyncio.sleep(0)` yield cost vs `threading.Timer(0, fn)`.** Believed cheaper (no OS
  thread handoff, no scheduler jitter — `simulator.py:53-64`'s own comment already documents how
  costly avoidable real-thread handoffs were for a *different* idle-tick issue), but not
  benchmarked. Should be measured against the existing synthetic/MicroPython-boot benchmarks
  (`_cmd_bench` in `cli/__init__.py`) before/after phase 2, the same way the native backend and
  the HLE memcpy hook were both measured rather than assumed (see BACKLOG.md).
- **Does `RP2040.on_break`/GDB's synchronous breakpoint callback chain need to change?**
  `gdb_server.py:233-239`'s `_on_break` mutates `core.pc` and iterates connections synchronously,
  called from inside `execute()`'s own call chain (a real breakpoint hit). Under asyncio this stays
  a plain synchronous callback — same reasoning as USBCDC above — but worth an explicit look during
  phase 3, since it's the one place GDB and the execute loop touch each other directly.
- **Version floor.** `requires-python = ">=3.10"` (`pyproject.toml`) already covers everything
  used above (`asyncio.run_coroutine_threadsafe`, `loop.add_reader`/`add_signal_handler`,
  `asyncio.Event`/`Lock`/`Condition` are all far older than 3.10) — no floor bump needed.

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
