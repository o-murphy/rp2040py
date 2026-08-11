# Main-thread asyncio: put the primary Simulator's engine room on the process main thread

**Status: in progress.** This is a plan document in the same spirit as
[docs/ASYNCIO_MIGRATION_BACKLOG.md](ASYNCIO_MIGRATION_BACKLOG.md) (which it directly follows on
from and assumes as background reading) - written up before any code changes, per this project's
own established practice of scoping a concurrency-model change as its own effort rather than
starting it mid-flight on top of something else.

**Sequencing, decided (2026-08-11): this must land on `main` as its own effort before a separate,
not-yet-merged CYW43439/Pico-W-WiFi-emulation effort (`ExternalDevice`s attached to `RP2040`)
continues.** That work is being built on a different branch and needs the concurrency-model shape
this document produces as a foundation, not the reverse - building CYW43439's bus/chip/NAT-bridge
layers against today's engine-room-thread model and then re-threading them onto whatever this
migration produces would mean doing that work twice.

## Why this document exists

Upstream rp2040js needs none of this, and needs no `async`/`await` either - its own
`Simulator.execute()` (`~/pyproj/rp2040js/src/simulator.ts`) is a plain synchronous function that
reschedules itself via `setTimeout(() => this.execute(), 0)`, nothing more. That works because
Node's single-threaded, callback-queue event loop exists automatically the moment the process
starts - not something Node code opts into or runs itself, just a free runtime property. Python
has no equivalent free lunch: there is no implicit event loop backing a plain `.py` script, so
reproducing "one thread, cooperative scheduling, no OS-level concurrency" here requires something
to actually *create and run* one - `asyncio.run()` (or an existing running loop) - explicitly.
`asyncio`/`async`/`await` in this project's port isn't extra complexity added *beyond* what
upstream needs; it's the explicit machinery Python needs to reproduce a runtime property JS gets
for free. (This project tried the more literal translation first - `threading.Timer(0,
self.execute)` standing in for `setTimeout(fn, 0)` - and it was a real, measured regression:
`docs/BACKLOG.md`'s CDC investigation found `threading.Timer(0, fn)` spins up a **brand-new OS
thread per reschedule**, unlike `setTimeout`, which reuses the same thread's queue - so the naive
port was actively worse than the `asyncio` rewrite it now is.)

`docs/ASYNCIO_MIGRATION_BACKLOG.md`'s own opening line makes the same "Node is single-threaded"
point, and that migration ported every one of upstream's implicit-single-thread assumptions onto
real `asyncio` - except one:
**`Simulator`'s engine-room loop still runs on a dedicated background thread, not the process's
actual main thread.** That migration's own "Resolved during PR 3" section names the reason and
leaves it open rather than closed:

> `loop.add_signal_handler()` cannot replace `signal.signal()` yet... `add_signal_handler()`
> still requires being called from the actual process main thread... Only phase 5 - if
> `cli/__init__.py`'s `main()` itself becomes the main-thread event loop - would make this viable.

That hypothetical "phase 5" never happened - the PR actually numbered 5 turned out to be about
`gdb_target.py`/`gdb_tcp_server.py` bridging instead, and "is a single shared front-door loop
worth building" was answered "no" for a *different* question (whether to invent a *third* kind of
loop) than "should the engine-room loop itself be the main thread" (never revisited either way).
This document picks that specific, still-open thread back up.

**Motivation, restated plainly (2026-08-11 discussion):** the current design still needs a
background thread for the single most common case - one instance, running in the foreground,
exactly the shape upstream needs zero threads for. That's backwards from "port upstream's model,
add capabilities on top" - it should be the other way around: **the common case matches upstream
exactly (one thread, one loop, real signal handling, no bridging), and a background/multi-instance
thread only exists as an explicit, opt-in capability for someone who actually wants to run more
than one instance concurrently** - not baked into every `Simulator`'s construction.

This isn't just a purity argument. A parallel effort building on the older, thread-based model hit
two *real* bugs directly caused by it:
- A leftover engine-room thread could still call real `time.monotonic()` after a test function
  returned, corrupting an unrelated *later* test's global monkeypatch (worked around at that
  test's own boundary - make its fake generator tolerant of stray extra calls - but that doesn't
  close the underlying hazard, only one symptom of it).
- A test whose assertion failed before reaching its own `stop()` call leaked a `Simulator` that
  busy-loops a full CPU core for the rest of the process, because `core.waiting=True`'s "idle"
  branch is a tight CPU-bound Python loop, not a real sleep - `try`/`finally` around every such
  test's cleanup is required, easy to get wrong once, and this project has already gotten it
  wrong once for exactly this reason.

Neither of these is a hypothetical "what if" - they're the same class of bug
`ASYNCIO_MIGRATION_BACKLOG.md`'s own "Why this document exists" section catalogued for the
*previous* round of threading (`pio.py`'s `RLock`, `stdio_repl.py`'s reader thread, `gdb_tcp_
server.py`'s accept thread) - confirming the pattern repeats wherever a background OS thread is
load-bearing, not just in the places already migrated.

## What doesn't change

Same boundary `ASYNCIO_MIGRATION_BACKLOG.md` drew for its own scope, still true here: **the
CPU-emulation hot path itself does not become async.** `_execute_batch()` stays a synchronous,
batched, CPU-bound call; `execute()` still yields via `await asyncio.sleep(0)` between batches.
Nothing about instruction throughput changes. What changes is *which thread* hosts the loop that
`execute()`'s task runs on, and what that does to everything currently bridging onto it from "the
CLI's own thread."

## Target shape

**Decided (2026-08-11): `Simulator` does not own a thread or a loop of its own at all - the
caller does, following the ordinary async-library convention.** No async library calls
`asyncio.run()` on its own behalf (`aiohttp`'s `ClientSession`, database drivers, etc. all expose
`async def` APIs and leave *running* the loop to whoever's using them) - `Simulator` should be no
different. `execute()` is already a plain coroutine; that's the entire public API needed. This
directly resolves what was an open question in the first draft of this document (see "Open
questions" below for what that draft got wrong) and simplifies "target shape" considerably from
that draft:

- **The primary/foreground instance's engine-room loop *is* the process main thread** because the
  CLI, as the caller, chooses to run it there: `cli/__init__.py`'s `main()` becomes an
  `asyncio.run(...)`-hosting entry point for any subcommand that runs a `Simulator`, not a plain
  synchronous function that constructs one and blocks on `wait_for_shutdown()`'s poll loop.
  Concretely, today's three `wait_for_shutdown()` call sites (`_cmd_run`, `_cmd_micropython`,
  `_cmd_kaluma` - `cli/__init__.py:218-221,458,519`) and their `Simulator()`/`start_execution()`
  pairing become `async def` coroutines run via `asyncio.run()`, with `execute()` awaited directly
  (or raced against a shutdown signal) instead of started-then-polled-from-outside.

This one change cascades into removing several existing workarounds, not just relocating the
loop:

- **`Simulator.wait_for_shutdown()`'s `time.sleep(0.1)` poll loop goes away.** It exists
  specifically because the calling (CLI) thread and the engine-room thread are different threads
  today. Once they're the same thread, "wait for shutdown" is just `await shutdown_event.wait()`
  inside the same coroutine already driving `execute()`.
- **`signal.signal()` + the main-thread-affinity checks scattered through `stdio_repl.py` become
  `loop.add_signal_handler(signal.SIGTERM, ...)`/`SIGINT`, called directly** - no longer blocked
  by the CPython main-thread restriction, since the loop *is* the main thread now. This is the
  literal thing PR 3's "Resolved" note said phase 5 would unlock.
- **`StdioInteractiveRepl._on_start()`'s `self._simulator.call(self._register_reader())` bridge
  (`stdio_repl.py:127`) becomes a direct `asyncio.get_running_loop().add_reader(...)` call** - the
  bridge exists only because `_on_start()` currently runs on the CLI's (different) thread; once
  the CLI's thread *is* the engine-room thread, there's nothing to bridge across.
- **`GDBTCPServer`'s separate non-daemon engine-room thread (`gdb_tcp_server.py:26`, `start_
  loop_thread(daemon=False)`) - and the whole "keep the process alive like Node's
  `net.Server.listen()`" justification for it - becomes unnecessary.** `asyncio.run()` already
  keeps the process alive for as long as its top-level coroutine hasn't returned; a GDB server
  attached to the primary instance can just be `asyncio.start_server()`'d as a task on the *same*
  main-thread loop, same as `StdioInteractiveRepl` already does today by sharing `Simulator`'s
  loop rather than getting its own. This directly answers the open question
  `ASYNCIO_MIGRATION_BACKLOG.md` left unresolved ("Could `GDBTCPServer` drop its own engine-room
  thread entirely... blocker isn't state-touching, it's lifecycle") - the lifecycle blocker
  (non-daemon thread needed to keep the process alive) disappears once `asyncio.run()` is doing
  that job instead. `IGDBTarget.acall()` (added in PR 5 specifically to bridge `process_gdb_
  message()` onto `Simulator`'s engine room from GDB's *separate* one) becomes a plain direct call
  too, for the same reason.
- **`schedule_threadsafe()` stops being needed for anything CLI-driven or same-process.** It
  still exists (see "What still needs a real thread" below) for the genuinely cross-thread case,
  but against a main-thread engine room, work that only ever needed to reach the CLI's own
  thread can usually just be `await`ed directly as a task on the same loop instead.

## What still needs a real thread

Not zero - two concrete cases stay real, and this document doesn't try to argue them away:

1. **Deliberately running an instance in the background, or more than one concurrently, from one
   process.** This is the only legitimate reason to keep a background-thread option at all - and,
   once "the caller owns `asyncio.run()`, not `Simulator`" (see "Target shape" above), it turns
   out `Simulator` doesn't need to provide anything special for this at all. It's an ordinary
   application-level choice the *caller* makes with plain stdlib `threading`, the same way any
   Python code decides to run something in the background:
   `threading.Thread(target=lambda: asyncio.run(sim.execute()), daemon=True).start()` (reusing
   `utils/asyncio_loop_thread.py`'s existing `start_loop_thread()` helper, or just this inline)
   for each instance that should run off the main thread, however many of those the caller wants.
   No `Simulator`-side API, no new "run in background" method, no `SimulatorGroup` type - the
   flexibility already exists in the standard library, `Simulator` doesn't need to wrap it.
   Whichever thread ends up running a given instance's loop, reaching it *from a different
   thread* still needs a real bridge - that's what `schedule_threadsafe()`/`call()`/`acall()`/
   `submit()` are for, and they stay exactly as useful as they are today for that specific case
   (a GDB connection on one thread reaching a `Simulator` whose loop the caller chose to put on
   another) - what changes is only that the *primary* CLI-driven instance no longer needs them
   for its own normal operation, since caller and engine room are the same thread there.
2. **Windows/non-tty stdin fallback** (`stdio_repl.py`'s existing dedicated thread for the
   blocking `sys.stdin.read()` call, `ProactorEventLoop` not supporting `add_reader()` on
   arbitrary file descriptors). Already an accepted, narrow exception before this document;
   nothing here changes that.

## Migration risk

Larger surface than the original asyncio migration touched, precisely because that migration
deliberately treated "which thread hosts the loop" as fixed and out of scope
(`ASYNCIO_MIGRATION_BACKLOG.md`'s own "Considered and rejected" section: doing a full model swap
at the same time as unrelated stabilization work was explicitly rejected as too hard to attribute
regressions to). This one *is* that swap. Concretely at risk, needing careful phased verification
against real firmware/GDB/REPL sessions the way each prior PR did (not just the unit suite):

- Every one of the ~500 existing tests that constructs a `Simulator` and assumes today's
  threading model in some way (real background thread, `simulator.call()`/`.acall()`/`.submit()`
  bridging, `RP2040PY_SKIP_CYTHON` × threading interactions). Expect many call-site updates, not
  a drop-in.
- `Simulator`'s own public API (`start_execution()`, `call()`, `acall()`, `submit()`,
  `schedule_threadsafe()`, `wait_for_shutdown()`) is used directly by `device/base_device.py`,
  `device/mp_device.py`, `device/kaluma_device.py`, `cli/pty_repl.py`, `cli/socket_repl.py`,
  `gdb/gdb_server.py` - a real public-surface audit is needed before deciding what of that API
  even still makes sense unchanged.
- **`MicroPythonDevice`/`BaseDevice`'s "boot, return control, keep running so a later `exec()` can
  talk to it" contract - resolved differently than the first draft of this section proposed
  (2026-08-12).** The first draft assumed the synchronous facade (`BaseDevice.start()`,
  `MicroPythonDevice.exec()`) had to be preserved unchanged, which would mean `BaseDevice`
  spinning up its own background thread internally just to keep blocking calls working - quietly
  reintroducing a background thread for the single most common case, the exact thing this whole
  migration exists to remove. **Decided instead: drop the synchronous facade.** It turns out
  `MicroPythonDevice` already has a full async-native API from `ASYNCIO_MIGRATION_BACKLOG.md`'s
  own PR 4 (`astart()`/`aexec()`/`aexec_file()`, `start_async()`/`exec_async()`/
  `exec_file_async()` returning `Future`s, `__aenter__`/`__aexit__`) - `start()`/`exec()`/
  `exec_file()` are thin blocking wrappers around those (`_result()`/`_await()`), not a separate
  implementation. Removing just the blocking wrappers costs nothing functionally (the async API
  underneath already does everything they do) and pushes every caller - the CLI included - onto
  the same "you own `asyncio.run()`" contract `Simulator.execute()` itself already has, instead of
  making two different rules for two different classes.

  This isn't only a facade change, though - `MicroPythonDevice._aconnect()` (and the equivalent
  exec-side internals) currently call `self.simulator.start_execution()`, which *always* spins up
  its own dedicated background thread via `_ensure_loop()`, regardless of whether the caller is
  already running its own loop. The real fix pairs with dropping the facade: `_aconnect()`/exec
  internals call `simulator.bind_loop()` and drive `execute()` as a task on *whichever* loop the
  async caller is already on (the CLI's `main()`-hosted `asyncio.run()`, or a library caller's own
  loop) - the same pattern `_cmd_run` already established in phase 1 - instead of asking for a
  dedicated thread every time. Net result: `micropython`/`kaluma` end up needing **zero** extra
  background threads for the ordinary foreground CLI case, same as `run` - not "one thread per
  device class, just moved from the CLI into `BaseDevice`."

## Phased plan

Mirroring `ASYNCIO_MIGRATION_BACKLOG.md`'s own "land one component at a time, verify against real
firmware/GDB/REPL sessions, record what broke" shape rather than one big-bang PR:

1. **`cli/__init__.py`'s `main()` becomes `asyncio.run()`-hosting for `run`.** `Simulator` grows
   `bind_loop()` (registers whichever loop `execute()` is being driven on directly, so
   `_ensure_loop()`'s cross-thread bridges - used by e.g. `GDBTCPServer`'s own engine room -
   reach the right loop instead of spinning up a redundant second one). `_cmd_run` becomes an
   `async def _run_async()` coroutine run via `asyncio.run()`: `execute()` raced against real
   `SIGINT`/`SIGTERM` handlers (`loop.add_signal_handler()`, with a `KeyboardInterrupt` fallback
   for platforms where that's unavailable) instead of `start_execution()` +
   `wait_for_shutdown()`'s poll loop. `micropython`/`kaluma` deliberately left on the older model
   for this step (they go through `BaseDevice`, which is phase 4's problem, not this one) - `run`
   was chosen first specifically because it constructs `Simulator` directly, with no
   `BaseDevice` "boot then keep running for a later `exec()`" contract in the way.
2. **Reordered (2026-08-12) - `BaseDevice`/`MicroPythonDevice`/`KalumaDevice` next, not
   `StdioInteractiveRepl`/`GDBTCPServer`.** The first draft put those two components next, but
   both are used by `micropython`/`kaluma`, which are still on the *old* model until this step
   lands - `StdioInteractiveRepl._on_start()`'s `simulator.call()` bridge, or `GDBTCPServer`'s own
   engine-room thread, genuinely can't be dropped yet for those call sites without breaking them
   (the bridge is exactly what makes `_on_start()` correct regardless of which thread called it;
   removing it silently assumes something not yet true). So: **drop the synchronous facade,
   `bind_loop()` instead of `start_execution()`** (see "Migration risk" above for the full
   reasoning). Delete `start()`/`exec()`/`exec_file()` (and `BaseDevice`'s own `start()`) - keep
   the already-existing `astart()`/`aexec()`/`aexec_file()`/`start_async()`/`exec_async()`/
   `exec_file_async()`. `_aconnect()`/exec internals switch from `simulator.start_execution()` to
   `simulator.bind_loop()` + driving `execute()` as a task on the caller's own loop. `cli/
   __init__.py`'s `_cmd_micropython`/`_cmd_kaluma` become `async def`, calling `astart()`/
   `aexec()` directly via `asyncio.run()`, matching `_cmd_run`'s phase-1 shape - zero extra
   background threads for the ordinary foreground case, not "one thread per device class."
   Likely the biggest of these steps regardless, given how much of `mp_device.py`'s FIFO-queueing
   (`asyncio.Lock`, PR 4) and raw-REPL exec machinery assumes today's shape - needs its own careful
   pass, not a mechanical find-replace. **Unblocks steps 3/4 below** - only once every
   `Simulator`-driving CLI path is on the main-thread model does it become safe to change
   `StdioInteractiveRepl`/`GDBTCPServer` uniformly (they're shared by all three subcommands, not
   swappable per caller).
3. `StdioInteractiveRepl` (`cli/stdio_repl.py`) drops its `simulator.call()` bridge for reader
   registration - direct `add_reader()` calls, now already on the right thread.
4. `GDBTCPServer` drops its own engine-room thread, attaches to the primary `Simulator`'s loop
   directly - `IGDBTarget.acall()` (PR 5's bridge) becomes unnecessary, `process_gdb_message()`
   called directly.
5. `Simulator.schedule_threadsafe()`, `.call()`, `.acall()`, `.submit()` re-audited: which
   callers still need a genuine cross-thread bridge (an instance a caller deliberately put on its
   own thread, or a future external-device-style component reached from a truly external thread)
   vs. which were only ever bridging because of the old CLI-thread/engine-room-thread split and
   can become plain `await`s.

## Open questions

None remaining as of 2026-08-11 - see the two resolved items below, kept for the record rather
than deleted.

### Resolved (2026-08-11): `RP2040PY_SKIP_CYTHON`/native-vs-pure-Python does not interact with any of this

Verified directly, not just assumed: `setup.py`'s `_build_ext_modules()` compiles exactly three
files - `native/_bit.pyx`, `native/_cortex_m0_core.pyx`, `native/_rp2040.pyx` (bit-manipulation
utilities, instruction execution, and register/bus/peripheral construction). None of them touch
`asyncio`, `threading`, or signal handling - that whole layer lives in `simulator.py`,
`cli/*.py`, `gdb/*.py`, `device/*.py`, none of which are ever Cython-compiled.
`RP2040PY_SKIP_CYTHON` only gates which `RP2040`/`CortexM0Core` implementation `rp2040py/rp2040.py`
and `rp2040py/cortex_m0_core.py` import - `Simulator` itself is always the same plain-Python
class either way, and just holds a reference to whichever `RP2040` instance it was given (or
built), native or pure-Python, without caring which. Zero interaction, in both today's design and
the one this document proposes.

### Resolved (2026-08-11), kept for the record - what the first draft got wrong

The first draft of this document treated "how does a library caller run a `Simulator` without the
CLI's `main()` doing it for them" and "how do multiple background instances share a loop" as open
design questions needing a new `Simulator`-side API (a `Simulator.run()` method, a hypothetical
`SimulatorGroup` type). Both dissolve under the plain rule **`Simulator` never calls
`asyncio.run()` itself - the caller does, exactly like every other `async`-first Python library.**
`execute()` staying a plain coroutine *is* the whole API; a caller with no loop of their own calls
`asyncio.run(sim.execute())` directly (one instance) or wraps that call in `threading.Thread(...)`
themselves (a background instance, or several) - no wrapping method needed on `Simulator` for
either case. Left in this section, not deleted, because it's a useful worked example of the
principle "don't build the caller's own stdlib tools back into the library" for whoever reads
this document's history later.

## Progress log

- **2026-08-11/12: Phase 1, `run` only - done, verified live.** `Simulator.bind_loop()` added.
  `_cmd_run` rewritten as `async def _run_async()` run via `asyncio.run()`; real
  `SIGINT`/`SIGTERM` handling via `loop.add_signal_handler()`, `KeyboardInterrupt` fallback for
  platforms without it. Verified against three real scenarios (a synthetic `bkpt`-terminated
  image, a synthetic infinite-loop image interrupted by real `SIGINT`, the same interrupted by
  real `SIGTERM`): all three exit with the right code (`0`, `130`, `143` respectively) and close
  the GDB server cleanly - no interpreter-shutdown hang joining its non-daemon accept thread.
  `micropython`/`kaluma` deliberately untouched (phase 4's problem, via `BaseDevice`).

- **2026-08-12: Phase 2, full scope - done, verified.** `BaseDevice`/`MicroPythonDevice`/
  `KalumaDevice` lost their sync facade entirely (`start()`/`exec()`/`exec_file()`/sync
  `__enter__`/`__exit__` deleted - `astart()`/`aexec()`/`aexec_file()` and the
  Future-returning `start_async()`/`exec_async()`/`exec_file_async()` are the only way in now);
  `astart()` calls `simulator.bind_loop()` before driving `execute()` as a task on the caller's
  own loop. Turned out `StdioInteractiveRepl`/`SocketInteractiveRepl`/`PtyInteractiveRepl` (not
  just `StdioInteractiveRepl`, per this doc's original phase-3 scope) all had to move together in
  this same step, not after it: all three are only reachable through `micropython`/`kaluma`,
  which only become "same-loop-as-caller" once the device classes do - so `repl_runner.py`'s
  `BaseReplRunner.start()`/`.stop()`, `process_repl.py`'s `_on_start()`/`_on_stop()`, and all
  three transports' own hooks became `async def`, calling `add_reader()`/`asyncio.start_server()`/
  `pty.openpty()` registration directly instead of bridging through `simulator.call()` (bridging
  into your own already-running loop from itself deadlocks - see "What still needs a real
  thread"). `cli/__init__.py`'s `_cmd_micropython`/`_cmd_kaluma` became `async def
  _micropython_async()`/`_kaluma_async()` run via `asyncio.run()`, matching `_cmd_run`'s phase-1
  shape, with `_await_shutdown()` as the shared async equivalent of the old
  `wait_for_shutdown()` poll loop (`int | None` return - `None` meaning "let the process exit
  normally, nothing ever requested a shutdown," not just "exit code 0", so exec-mode's own
  always-explicit `sys.exit()` on every path, success included, keeps working the same as before
  this migration).

  Fallout: 41 tests across `test_device.py`, `test_kaluma_device.py`,
  `test_mpremote_integration.py`, `test_pty_repl.py`, `test_raw_repl.py`, `test_repl_runner.py`,
  `test_socket_repl.py`, `test_stdio_repl.py` called the now-removed sync API or called the new
  `async def start()`/`stop()` without awaiting them (silently a no-op coroutine, not an error -
  caught several tests quietly asserting against state that was never touched). Fixed by driving
  each test body through a single `asyncio.run()` call (blocking client-side I/O - real sockets/
  ptys/subprocesses - routed through `asyncio.to_thread()` so it doesn't stall the same loop the
  server side needs to keep running) rather than a background loop thread: `signal.signal()`
  (`ProcessInteractiveRepl._on_start()`/`_on_stop()`) only works from the main thread of the main
  interpreter, so a bg-thread-hosted loop breaks SIGTERM handler installation - confirmed by
  hitting exactly that `ValueError` on a first attempt.

  Verified: full test suite green on both native and `RP2040PY_SKIP_CYTHON=1` builds (503
  passed, up from 462 passed/41 failed at the start of this fallout pass), `ruff check`/`format
  --check`/`mypy` clean on `src/`. Live smoke: `micropython -c`/exec mode (success -> exit 0,
  uncaught exception -> exit 1 - the exact regression this phase introduced and then fixed:
  the sync wrapper's `if exit_code: sys.exit(exit_code)` silently skipped `sys.exit(0)` on
  success, since `0` is falsy - tests caught it as "DID NOT RAISE SystemExit"), `micropython`/
  `kaluma` interactive mode over `--tcp-port` under real `SIGINT`/`SIGTERM` (130/143, matching
  phase 1), `--gdb` alongside `--tcp-port`. Confirmed byte-for-byte identical against `main`
  (via a throwaway `git worktree`) that a synthetic firmware boot banner not arriving over a
  freshly-opened `--tcp-port` connection within a couple of seconds is a pre-existing firmware/
  CDC-timing characteristic, not something this phase changed.

  Not done in this phase (deliberately - out of the scope actually requested): phases 4
  (`GDBTCPServer` dropping its own engine-room thread) and 5 (re-auditing every remaining
  `Simulator.call()`/`.acall()`/`.submit()` caller) are still open - `GDBTCPServer` still bridges
  every GDB message through `target.acall()` onto its own dedicated thread, unchanged.
