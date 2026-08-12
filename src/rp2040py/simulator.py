import asyncio
import concurrent.futures
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from rp2040py._clock_batch_gate import clock_tick_batch_size
from rp2040py.clock.simulation_clock import SimulationClock
from rp2040py.rp2040 import RP2040
from rp2040py.utils.asyncio_loop_thread import start_loop_thread

__all__ = ("ShutdownRequest", "Simulator")

_T = TypeVar("_T")

# A purely-idle (WFI'd) batch can legitimately jump thousands of simulated-time units ahead per
# iteration for ~0 simulated-time cost, so the 1,000,000-iteration ceiling below stops bounding how
# much *real* wall-clock time one batch takes once nothing is stopping it from running all
# 1,000,000 idle iterations - upstream rp2040js hits the same ceiling per batch (see simulator.ts)
# but V8 clears 1,000,000 loop iterations in low milliseconds; CPython's per-iteration attribute-
# access/function-call overhead was measured (see docs/BACKLOG.md) taking ~0.9-1.8s for the same
# ceiling - long enough to stall anything sharing this Simulator's engine-room loop (e.g.
# StdioInteractiveRepl's add_reader() callback - a keystroke could sit unread for the entire
# batch). Bounding this isn't free either (a real WFI'd device should still advance as far as it
# can per batch for throughput), so this is checked only every _TIME_CHECK_INTERVAL iterations,
# not every one - time.monotonic() itself must not become the hot-path cost this is trying to
# avoid. Tracked from the start of the whole batch, unconditionally - not just during an
# uninterrupted idle run - since a real device idling at the REPL isn't purely WFI'd end to end
# (a periodic timer interrupt briefly flips core.waiting False for a handful of instructions
# before going back to waiting); an idle-run-only version of this check kept getting reset by
# those interludes and never fired in practice, see git history for the confirmed repro.
_BATCH_YIELD_BUDGET_SECONDS = 0.005
_TIME_CHECK_INTERVAL = 256


class ShutdownRequest:
    """Lets a background thread (a REPL's Ctrl+X handler, a --expect-text watcher, a SIGTERM
    handler - anything that isn't the thread running `Simulator.wait_for_shutdown` below) ask for
    a clean process exit instead of tearing the process down itself via `os._exit()`.
    `os._exit()` works, but unconditionally skips atexit/finally/normal cleanup wherever it's
    used - terminal state, a listening GDB server's socket, anything a caller needs torn down
    first. First request wins (a second trigger firing near-simultaneously shouldn't override the
    first exit code); `wait_for_shutdown` - always running on the thread driving the `Simulator` -
    is the only thing that acts on this, via a real `sys.exit()` once its own cleanup has run.
    """

    def __init__(self) -> None:
        self.event = threading.Event()
        self.code = 0

    def request(self, code: int) -> None:
        if not self.event.is_set():
            self.code = code
            self.event.set()


class Simulator:
    def __init__(self, clock: SimulationClock | None = None, rp2040: RP2040 | None = None):
        # `rp2040`, if given, is normally built via `boards.build_rp2040()` (or a bare `RP2040()`
        # for a caller with no board-registry needs) - its own clock is authoritative in that
        # case, since peripherals were already constructed against it before this Simulator ever
        # saw the instance.
        self.clock = clock if clock is not None else (rp2040.clock if rp2040 is not None else SimulationClock())
        self.rp2040 = rp2040 if rp2040 is not None else RP2040(self.clock)
        self.rp2040.on_break = lambda code: self.stop()
        # Lets an attached ExternalDevice reach this Simulator's engine-room loop via
        # `rp2040.schedule_threadsafe()` without needing a separate reference threaded through
        # `attach()` - see docs/CYW43_WIFI_BACKLOG.md's "Concurrency model" section.
        self.rp2040.simulator = self
        self.stopped = True
        # Owned here (rather than a separately-constructed, separately-passed-around object) so
        # anyone with a reference to this Simulator can request a shutdown - a REPL, a
        # --expect-text watcher, a SIGTERM handler - without also needing a reference to whatever
        # ShutdownRequest instance some particular caller happened to create.
        self.shutdown_request = ShutdownRequest()
        # The "engine room": exactly one asyncio event loop, for this Simulator's whole lifetime -
        # execute(), RPPIO.run() (peripherals/pio.py) and everything reachable from
        # execute_instruction()'s bus dispatch (USBCDC, GDB command execution) only ever run here.
        # Usually the caller's own loop now (via bind_loop() - docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's
        # "Target shape"), not a dedicated background thread this Simulator spins up itself - that
        # fallback (_ensure_loop()/start_loop_thread()) only kicks in lazily, for a caller that
        # never calls bind_loop() (tests exercising ShutdownRequest alone, a caller wanting a
        # second, independently-running instance, ...).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_init_lock = threading.Lock()
        self._execute_task: asyncio.Task[None] | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            with self._loop_init_lock:
                if self._loop is None:
                    self._loop, self._loop_thread = start_loop_thread()
        return self._loop

    def bind_loop(self, loop: "asyncio.AbstractEventLoop | None" = None) -> None:
        """Registers the loop this Simulator's `execute()` runs on, for a caller driving it
        directly (e.g. `asyncio.create_task(simulator.execute())` inside its own `asyncio.run()`
        - see docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape") instead of via
        `start_execution()`'s own thread+loop creation. Cross-thread bridges (`call()`/`submit()`)
        use whatever loop was registered here; without this, a
        caller reaching in from a genuinely different thread would make `_ensure_loop()` spin up a
        second, unrelated loop that nothing is actually running `execute()` on - scheduling work
        there via `run_coroutine_threadsafe` would just sit forever, never executed. `loop=None`
        (the default) resolves via `asyncio.get_running_loop()` - call this from inside the
        coroutine/loop context that will drive `execute()`, before anything else might try to
        bridge in from another thread."""
        self._loop = loop if loop is not None else asyncio.get_running_loop()

    def start_execution(self) -> None:
        """Schedules execute() to start running on this Simulator's own engine-room thread and
        returns immediately - the replacement for `threading.Thread(target=simulator.execute,
        daemon=True).start()`. execute() keeps running there until stop()."""
        loop = self._ensure_loop()

        def _start() -> None:
            self._execute_task = loop.create_task(self.execute())

        loop.call_soon_threadsafe(_start)

    def call(self, coro: "Coroutine[Any, Any, _T]", timeout: "float | None" = None) -> _T:
        """Runs `coro` on the engine-room thread and blocks the calling thread until it completes -
        the bridge a caller on a genuinely different, non-engine-room thread (e.g. a test
        simulating a device reply from its own `threading.Thread`, or `StdioInteractiveRepl`'s
        non-tty/Windows fallback reader thread - see their own docstrings) uses to safely touch
        execute()/RPPIO/USBCDC state. Not needed by a caller that already shares this Simulator's
        own loop (per docs/MAIN_THREAD_ASYNCIO_BACKLOG.md's "Target shape") - that caller just
        `await`s directly instead, no bridge required."""
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return future.result(timeout)

    def submit(self, coro: "Coroutine[Any, Any, _T]") -> "concurrent.futures.Future[_T]":
        """Runs `coro` on the engine-room thread, returning immediately with a Future rather than
        blocking - call()'s non-blocking counterpart, for a caller that wants to hand back a
        concurrent.futures.Future itself (device/mp_device.py's own *_async() API) rather than
        block the calling thread right away."""
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())

    def schedule_threadsafe(self, fn_or_coro: "Callable[[], None] | Coroutine[Any, Any, Any]") -> None:
        """Fire-and-forget handoff onto this Simulator's engine-room loop, from any thread -
        the mechanism `ExternalDevice` authors (docs/CYW43_WIFI_BACKLOG.md's "Concurrency model")
        use for slow/blocking work (a real `socket.connect()`, handing data to a *different*
        `RP2040`'s engine room) that must never run inline on whichever thread called `attach()`'s
        installed callbacks. A plain callable runs via `call_soon_threadsafe`; a coroutine is
        scheduled via `run_coroutine_threadsafe` - callers not needing the result use this instead
        of `call()`/`submit()` so they aren't forced to block or hold onto a Future they'll never
        look at."""
        loop = self._ensure_loop()
        if asyncio.iscoroutine(fn_or_coro):
            asyncio.run_coroutine_threadsafe(fn_or_coro, loop)
        else:
            loop.call_soon_threadsafe(fn_or_coro)

    def _execute_batch(self) -> None:
        """Runs up to 1,000,000 instructions/idle-jumps, or until stop() is called, or until the
        batch itself has eaten its own real-time budget (_BATCH_YIELD_BUDGET_SECONDS) - whichever
        comes first. Synchronous and self-contained (no `await` anywhere in here) so it can be
        driven directly, e.g. from a test that wants deterministic single-batch behavior without
        depending on asyncio scheduling order - see tests/test_simulator.py."""
        rp2040, clock = self.rp2040, self.clock
        cycle_nanos = 1e9 / 125_000_000  # 125 MHz
        i: float = 0
        batch_start = time.monotonic()
        ticks_since_check = 0
        # RP2040PY_CLOCK_TICK_BATCH (default 1 - see _clock_batch_gate.py's own module docstring
        # for why this stays opt-in): >1 lets the busy branch below accumulate several
        # instructions' worth of simulated nanoseconds into pending_nanos instead of calling the
        # real clock.tick() after every single one, flushing (the real tick(), firing any due
        # alarm exactly as it would have anyway) whenever nanos_budget would go non-positive or
        # tick_batch instructions have accumulated - whichever comes first - so a scheduled alarm
        # never fires later than it does today, only the (typically far more common, per a real
        # measured boot - see the same module docstring) no-alarm-imminent case skips repeated
        # full tick() calls. tick_batch==1 (the default) takes the exact same one-call-per-
        # instruction path as always, just with the redundant branch below.
        tick_batch = clock_tick_batch_size()
        pending_nanos = 0.0
        pending_count = 0
        nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else float("inf")
        while i < 1000000 and not self.stopped:
            # Checked every _TIME_CHECK_INTERVAL iterations regardless of idle/busy - not just
            # during an uninterrupted idle run. A real device idling at the REPL isn't purely
            # WFI'd end to end: a periodic timer interrupt (SysTick, watchdog, ...) briefly flips
            # core.waiting False for a handful of instructions before it goes back to waiting -
            # an earlier version of this check reset its clock whenever that happened, so it
            # never accumulated enough uninterrupted idle time to fire and the batch fell back to
            # running the full 1,000,000-iteration ceiling anyway (confirmed: a batch with a
            # busy interlude every ~50 idle ticks still took ~0.95s wall time with that version).
            # Tracking one clock from the start of the whole batch, unconditionally, doesn't have
            # that gap.
            ticks_since_check += 1
            if ticks_since_check >= _TIME_CHECK_INTERVAL:
                ticks_since_check = 0
                if time.monotonic() - batch_start > _BATCH_YIELD_BUDGET_SECONDS:
                    # Free to keep going indefinitely in simulated time (see below) but not in
                    # real time - stdio_repl.py's add_reader() callback shares this same
                    # engine-room loop and only gets a turn between batches, so a batch that never
                    # ends is indistinguishable from a hung keyboard. Breaking here just ends this
                    # batch early; execute()'s own loop immediately starts the next one after
                    # yielding, so nothing about idle state or instruction execution is lost.
                    break
            if rp2040.core.waiting:
                if pending_nanos:
                    # Flush whatever the busy branch below had accumulated first - the idle jump
                    # right after must be relative to the real, ticked-forward clock, not stale by
                    # however much was still pending.
                    clock.tick(pending_nanos)
                    pending_nanos = 0.0
                    pending_count = 0
                # Jumping straight to the next alarm costs ~nothing in real time no matter how far
                # away it is (no instructions execute), so it must not be weighted by the simulated
                # nanoseconds it covers - that previously counted a single 1ms USB SOF-driven idle
                # tick as ~125,000 "instructions" against the budget above, exhausting a whole batch
                # in ~8 idle ticks and forcing a yield (see execute()'s `await asyncio.sleep(0)`)
                # roughly every 8ms of simulated idle time. A real WFI'd device sits idle almost all
                # the time once booted, so that turned "wait for USB-CDC output" into thousands of
                # avoidable yields - each one exposed to real scheduler jitter - which is what
                # actually produced the wildly variable wall-clock times noted in
                # docs/BACKLOG.md's CDC investigation, not anything USB-specific.
                clock.tick(clock.nanos_to_next_alarm)
                if tick_batch > 1:
                    nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else float("inf")
            else:
                cycles = rp2040.core.execute_instruction()
                delta_nanos = cycles * cycle_nanos
                if tick_batch <= 1:
                    clock.tick(delta_nanos)
                else:
                    pending_nanos += delta_nanos
                    pending_count += 1
                    nanos_budget -= delta_nanos
                    if nanos_budget <= 0 or pending_count >= tick_batch:
                        clock.tick(pending_nanos)
                        pending_nanos = 0.0
                        pending_count = 0
                        nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else float("inf")
            i += 1
        if pending_nanos:
            # End of batch (stopped, iteration ceiling, or the real-time yield budget above) with
            # something still un-flushed - must not leave simulated time silently behind by up to
            # a whole batch's worth of nanoseconds, e.g. for a caller reading clock.nanos right
            # after execute() yields.
            clock.tick(pending_nanos)

    async def execute(self) -> None:
        self.stopped = False
        while not self.stopped:
            self._execute_batch()
            # Upstream rp2040js uses `setTimeout(() => this.execute(), 0)` to yield back to the
            # single-threaded JS event loop every batch so external stop() calls can get in -
            # `await asyncio.sleep(0)` is the direct analogue, on this Simulator's own dedicated
            # engine-room loop (see _ensure_loop()) rather than JS's single process-wide one.
            await asyncio.sleep(0)

    def stop(self) -> None:
        # A plain bool set/read is already atomic in CPython - safe to call from any thread
        # (the engine-room loop's own on_break callback, a GDB connection handler, the CLI's main
        # thread, ...) without further coordination. execute()/RPPIO.run() notice it on their next
        # loop iteration; no task cancellation needed, matching how this always worked (one extra
        # already-scheduled batch may still run - benign staleness, not corruption).
        self.stopped = True

    @property
    def executing(self) -> bool:
        return not self.stopped

    def wait_for_shutdown(self, cleanup: "Callable[[], None] | None" = None) -> None:
        """Blocks the calling thread until this simulator stops running, for one of two reasons:

        - `KeyboardInterrupt` (a real Ctrl+C on the thread calling this - only reachable when
          nothing else has put the terminal in raw mode, since raw mode disables ISIG).
        - `self.shutdown_request` being requested from another thread - a REPL's Ctrl+X, a
          --expect-text match, a SIGTERM handler.

        `execute()` (started via `start_execution()`) runs on this Simulator's own engine-room
        thread, not the caller's - a caller that didn't wait here would return immediately and
        leave the process hanging in interpreter shutdown, joining a GDB server's non-daemon
        accept thread (if any) forever with nothing left to tell it to stop.

        `cleanup`, if given, is this call's one teardown hook, run exactly once before the
        process actually exits, regardless of *why* it's exiting. Callers needing more than one
        thing torn down (a REPL's terminal, a GDB server's listening socket) should compose them -
        e.g. via `contextlib.ExitStack` - into the single callable passed here, rather than this
        method knowing about any of those specifics itself.
        """
        try:
            while self.executing:
                if self.shutdown_request.event.is_set():
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            if cleanup is not None:
                cleanup()
            self.stop()
            sys.exit(130)

        if self.shutdown_request.event.is_set():
            if cleanup is not None:
                cleanup()
            self.stop()
            sys.exit(self.shutdown_request.code)
