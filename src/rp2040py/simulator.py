import asyncio
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from rp2040py.clock.simulation_clock import SimulationClock
from rp2040py.rp2040 import RP2040

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
        self._execute_timer: threading.Timer | None = None
        self.stopped = True
        # Owned here (rather than a separately-constructed, separately-passed-around object) so
        # anyone with a reference to this Simulator can request a shutdown - a REPL, a
        # --expect-text watcher, a SIGTERM handler - without also needing a reference to whatever
        # ShutdownRequest instance some particular caller happened to create.
        self.shutdown_request = ShutdownRequest()

    def execute(self) -> None:
        rp2040, clock = self.rp2040, self.clock

        self._execute_timer = None
        self.stopped = False
        cycle_nanos = 1e9 / 125_000_000  # 125 MHz
        i: float = 0
        while i < 1000000 and not self.stopped:
            if rp2040.core.waiting:
                # Jumping straight to the next alarm costs ~nothing in real time no matter how far
                # away it is (no instructions execute), so it must not be weighted by the simulated
                # nanoseconds it covers - that previously counted a single 1ms USB SOF-driven idle
                # tick as ~125,000 "instructions" against the budget below, exhausting a whole batch
                # in ~8 idle ticks and forcing a real OS-thread handoff (see `execute()`'s NOTE)
                # roughly every 8ms of simulated idle time. A real WFI'd device sits idle almost all
                # the time once booted, so that turned "wait for USB-CDC output" into thousands of
                # avoidable thread handoffs - each one exposed to real OS-scheduler jitter - which is
                # what actually produced the wildly variable wall-clock times noted in
                # docs/BACKLOG.md's CDC investigation, not anything USB-specific.
                clock.tick(clock.nanos_to_next_alarm)
            else:
                cycles = rp2040.core.execute_instruction()
                clock.tick(cycles * cycle_nanos)
            i += 1
        if not self.stopped:
            # NOTE: same rationale as RPPIO.run() in peripherals/pio.py - upstream rp2040js uses
            # `setTimeout(() => this.execute(), 0)` to yield back to the single-threaded JS event
            # loop so external stop() calls can get in. threading.Timer is the closest Python
            # analogue, but it introduces real concurrency (self.stopped/self.rp2040 are now
            # touched from a worker thread too) that the JS version never had. Revisit once the
            # overall scheduling model is settled.
            self._execute_timer = threading.Timer(0, self.execute)
            self._execute_timer.start()

    def stop(self) -> None:
        self.stopped = True
        if self._execute_timer is not None:
            self._execute_timer.cancel()
            self._execute_timer = None

    @property
    def executing(self) -> bool:
        return not self.stopped

    def wait_for_shutdown(self, cleanup: "Callable[[], None] | None" = None) -> None:
        """Blocks the calling thread until this simulator stops running, for one of two reasons:

        - `KeyboardInterrupt` (a real Ctrl+C on the thread calling this - only reachable when
          nothing else has put the terminal in raw mode, since raw mode disables ISIG).
        - `self.shutdown_request` being requested from another thread - a REPL's Ctrl+X, a
          --expect-text match, a SIGTERM handler.

        `execute()` only runs the first burst synchronously and then reschedules itself via
        `threading.Timer`, so a caller that didn't wait here would return immediately and leave
        the process hanging in interpreter shutdown, joining that non-daemon timer chain forever.

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
