import asyncio
import concurrent.futures
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from rp2040py.clock.simulation_clock import SimulationClock
from rp2040py.rp2040 import RP2040
from rp2040py.utils.asyncio_loop_thread import start_loop_thread

__all__ = ("ShutdownRequest", "Simulator")

_T = TypeVar("_T")


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
    def __init__(self, clock: SimulationClock | None = None):
        self.clock = clock if clock is not None else SimulationClock()
        self.rp2040 = RP2040(self.clock)
        self.rp2040.on_break = lambda code: self.stop()
        self.stopped = True
        # Owned here (rather than a separately-constructed, separately-passed-around object) so
        # anyone with a reference to this Simulator can request a shutdown - a REPL, a
        # --expect-text watcher, a SIGTERM handler - without also needing a reference to whatever
        # ShutdownRequest instance some particular caller happened to create.
        self.shutdown_request = ShutdownRequest()
        # The "engine room": this Simulator owns exactly one dedicated background thread hosting
        # one asyncio event loop, for its whole lifetime - execute(), RPPIO.run()
        # (peripherals/pio.py) and everything reachable from execute_instruction()'s bus dispatch
        # (USBCDC, GDB command execution) only ever run here. Lazy, not created here in __init__:
        # plenty of callers construct a Simulator and never run it (tests exercising
        # ShutdownRequest alone, fakes in test_gdb_tcp_server.py, ...) - see _ensure_loop().
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
        the bridge any synchronous, non-engine-room caller (the CLI, a test, a GDB connection
        handler) uses to safely touch execute()/RPPIO/USBCDC state. Mirrors
        device/mp_device.py's _result()/_await() shape, generalized here rather than reinvented
        per caller."""
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return future.result(timeout)

    async def acall(self, coro: "Coroutine[Any, Any, _T]") -> _T:
        """Async equivalent of call(), for a caller that already has its own running loop."""
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()))

    def submit(self, coro: "Coroutine[Any, Any, _T]") -> "concurrent.futures.Future[_T]":
        """Runs `coro` on the engine-room thread, returning immediately with a Future rather than
        blocking - call()'s non-blocking counterpart, for a caller that wants to hand back a
        concurrent.futures.Future itself (device/mp_device.py's own *_async() API) rather than
        block the calling thread right away."""
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())

    def _execute_batch(self) -> None:
        """Runs up to 1,000,000 instructions/idle-jumps, or until stop() is called - whichever
        comes first. Synchronous and self-contained (no `await` anywhere in here) so it can be
        driven directly, e.g. from a test that wants deterministic single-batch behavior without
        depending on asyncio scheduling order - see tests/test_simulator.py."""
        rp2040, clock = self.rp2040, self.clock
        cycle_nanos = 1e9 / 125_000_000  # 125 MHz
        i: float = 0
        while i < 1000000 and not self.stopped:
            if rp2040.core.waiting:
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
            else:
                cycles = rp2040.core.execute_instruction()
                clock.tick(cycles * cycle_nanos)
            i += 1

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
