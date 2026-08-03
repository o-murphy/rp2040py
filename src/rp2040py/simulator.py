import threading

from rp2040py.clock.simulation_clock import SimulationClock
from rp2040py.rp2040 import RP2040

__all__ = ("Simulator",)


class Simulator:
    def __init__(self, clock: SimulationClock | None = None):
        self.clock = clock if clock is not None else SimulationClock()
        self.rp2040 = RP2040(self.clock)
        self.rp2040.on_break = lambda code: self.stop()
        self._execute_timer: threading.Timer | None = None
        self.stopped = True

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
