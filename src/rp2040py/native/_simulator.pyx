"""Native port of Simulator._execute_batch()'s own per-instruction dispatch/idle loop
(simulator.py) - the confirmed single biggest remaining interpretation cost even under an
otherwise-fully-native CPU core/bus (docs/records/0013-cython-core.md's own "not yet tried" #1:
~43-47% of profiled time was still this loop itself, since only execute_instruction() - the
handler it calls - was ever ported, not the while loop driving it).

Only the loop's own control flow moves here. rp2040/core/clock are all already-native (cimported,
typed via their own .pxd - core.execute_instruction()/rp2040.core/clock.tick() field access all
resolve to direct C calls, see native/_simulation_clock.pyx's own module docstring for why clock
was the last piece of this to get ported). RPPIO isn't natively typed (no native/_pio.pyx), so
`pio.step()`/`pio.check_interrupts()` stay the accepted object-typed boundary, same as
StateMachine.rp2040.gpio/.dma staying object-typed in native/_state_machine.pyx (see
docs/records/0031-pio-cython-tick-batching.md's own "not yet tried" #4). simulator itself
(rp2040py.simulator.Simulator) is likewise plain Python - its own .stopped flag needs a fresh read
every iteration (a cross-thread stop() call must be able to interrupt a running batch, per
Simulator.stop()'s own docstring), so it's read via ordinary attribute access every iteration.

Called only when the facade in simulator.py has already confirmed both
rp2040py._native_gate.native_disabled() is False and the caller's own Simulator.rp2040 is actually
the native RP2040 class (not a bare pure-Python one manually constructed while native extensions
happen to be installed) - nothing here re-checks that; the `cdef RP2040 rp2040 = simulator.rp2040`
assignment below would raise a TypeError instead of silently misbehaving if that invariant were
ever violated, but the facade is what's relied on to avoid hitting it. Same accepted risk covers
the new `cdef SimulationClock clock = simulator.clock` assignment: every real construction path in
this codebase (Simulator() defaulting to its own fresh clock, or Simulator(rp2040=...) sourcing
self.clock from rp2040.clock - see simulator.py's own __init__) keeps simulator.clock and
simulator.rp2040.clock as the same SimulationClock-or-subclass instance (MockClock included - see
native/_simulation_clock.pyx's docstring); a caller passing mismatched, non-SimulationClock
clock=/rp2040= arguments by hand would hit the same kind of TypeError as the rp2040 case above,
not a silent correctness bug.
"""

import time

from libc.math cimport INFINITY

from rp2040py.native._cortex_m0_core cimport CortexM0Core
from rp2040py.native._rp2040 cimport RP2040
from rp2040py.native._simulation_clock cimport SimulationClock

cdef double CYCLE_NANOS = 1e9 / 125_000_000  # 125 MHz

# Mirrors simulator.py's own module-level constants exactly - see that file's docstrings for the
# full rationale (why 256/0.005s, why the idle jump must not be weighted by simulated nanoseconds).
cdef double BATCH_YIELD_BUDGET_SECONDS = 0.005
cdef int TIME_CHECK_INTERVAL = 256
cdef long BATCH_INSTRUCTION_CEILING = 1000000


def execute_batch(simulator: object, tick_batch: int) -> None:
    """Same semantics as Simulator._execute_batch() (simulator.py), byte-for-byte translated -
    keep the two in sync by hand if either changes; there's no shared source between them (unlike
    e.g. StateMachine, this loop was never a `_execute_batch.py` reference module the pure-Python
    Simulator imports, since Simulator itself stays a plain Python class either way)."""
    cdef RP2040 rp2040 = simulator.rp2040
    cdef CortexM0Core core = rp2040.core
    cdef SimulationClock clock = simulator.clock

    if rp2040.run_pin_low:
        # RUN held low - the chip is in reset and executes nothing. See _execute_batch.py's own
        # comment here for the full rationale; the two loops must not drift.
        return
    # Advanced once per loop iteration below (both the idle-jump and busy-instruction paths -
    # real PIO keeps running independent of CPU sleep state), by the system clocks that iteration
    # covered rather than by one step regardless: honouring SM_CLKDIV and [delay] is
    # RPPIO.advance()'s own job (docs/records/0063), and its fast path is a single integer
    # compare, which is what let the `_has_runnable_machine()` scan that used to sit here go away
    # entirely. Still the same "driven directly by this loop, not a competing asyncio.Task"
    # pattern clock.tick() itself uses - see _execute_batch.py's own comment on the mirrored line
    # and docs/records/0037-pio-clock-coupled-stepping.md. Called as an ordinary Python method
    # from here (RPPIO is a cdef class in native/_pio.pyx, but this module deliberately doesn't
    # cimport it - see this file's own docstring on the object-typed boundary).
    pios = rp2040.pio

    cdef long i = 0
    cdef int ticks_since_check = 0
    cdef double batch_start = time.monotonic()
    cdef double pending_nanos = 0.0
    cdef int pending_count = 0
    cdef double nanos_budget
    cdef double delta_nanos
    cdef double idle_nanos
    # long long, not int: the idle branch below converts a jump straight to the next alarm into
    # cycles, and that can be far more than a CPU instruction's handful.
    cdef long long cycles

    nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else INFINITY

    while i < BATCH_INSTRUCTION_CEILING and not simulator.stopped:
        ticks_since_check += 1
        if ticks_since_check >= TIME_CHECK_INTERVAL:
            ticks_since_check = 0
            if time.monotonic() - batch_start > BATCH_YIELD_BUDGET_SECONDS:
                break
        if core.waiting:
            if pending_nanos:
                clock.tick(pending_nanos)
                pending_nanos = 0.0
                pending_count = 0
            idle_nanos = clock.nanos_to_next_alarm
            clock.tick(idle_nanos)
            # The system clocks that jump covered, floored at one - see _execute_batch.py's own
            # comment here for why the floor is load-bearing.
            cycles = <long long>(idle_nanos / CYCLE_NANOS) or 1
            if tick_batch > 1:
                nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else INFINITY
        else:
            cycles = core.execute_instruction()
            delta_nanos = cycles * CYCLE_NANOS
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
                    nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else INFINITY
        for pio in pios:
            if not pio.stopped:
                pio.advance(cycles)
        i += 1
    if pending_nanos:
        clock.tick(pending_nanos)
