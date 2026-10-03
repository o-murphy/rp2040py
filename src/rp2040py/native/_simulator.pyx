# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""Native `Simulator._execute_batch()`: a Python-facing shell over the C++ batch loop of `core/batch.hpp`
(docs/records/0096-cpp-mcu-core.md, Phase 2 step 4c). `_execute_batch.py` stays the pure-Python reference loop; the two are held to
identical behaviour by `tests/test_batch_parity.py`, and `execute_batch.py` is the facade that picks between them.

The whole per-instruction loop - decode, execute, tick the clock (firing due alarms), the idle jump to the next alarm, the stop check and
the real-time budget - runs in C++ against the C++ `Cpu`, `Bus` and `Clock` of the chip, and touches no Python object unless something
it drives does. What it still reaches out to, once per iteration at most:

- `Simulator.stopped`: a property over a one-byte buffer (`Simulator._stop_flag`), so the loop reads the byte itself and a `stop()`
  from another thread (or from an `on_break` callback in the middle of an instruction) is seen at the next iteration, as before;
- each PIO block's `stopped` flag, read through the address of its own C field; only a PIO that is running costs a call
  (`RPPIO.advance`, a C-level call), the pure-Python/`Mock` PIO of a mixed setup is refused up front;
- `time.monotonic()`, once every 256 iterations, for the real-time yield budget.

A Python error raised by any of them, or by a peripheral or alarm callback inside the loop, is parked (`_pending.pyx`) and re-raised here
after the loop returns, with the machine left exactly where the exception would have left it.

The loop ticks `clock._clock` (the C++ `Clock`) directly, so a `SimulationClock` subclass that overrides `tick()` in Python would be
bypassed; that is refused up front too (`MockClock` only adds `advance()`).
"""

import time

from libc.stdint cimport int64_t, uint8_t
from libcpp cimport bool

from rp2040py.native._batch cimport BatchHost, BatchParams, kBatchFault, run_batch
from rp2040py.native._cortex_m0_core cimport CortexM0Core
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._pio cimport RPPIO
from rp2040py.native._rp2040 cimport RP2040
from rp2040py.native._simulation_clock cimport SimulationClock

# Looked up through Python attribute access on purpose: `SimulationClock.tick` spelled directly would be the C method.
_BASE_CLOCK_CLASS = SimulationClock

cdef double CYCLE_NANOS = 1e9 / 125_000_000  # 125 MHz

# Mirrors simulator.py's own module-level constants exactly - see that file's docstrings for the full rationale (why 256/0.005s, why the
# idle jump must not be weighted by simulated nanoseconds).
cdef double BATCH_YIELD_BUDGET_SECONDS = 0.005
cdef int TIME_CHECK_INTERVAL = 256
cdef long BATCH_INSTRUCTION_CEILING = 1000000


cdef class _BatchContext:
    cdef list pios

    def __cinit__(self, list pios):
        self.pios = pios


cdef bool _pio_advance(void* ctx, int index, int64_t cycles) noexcept:
    cdef _BatchContext context = <_BatchContext> ctx
    cdef RPPIO pio = <RPPIO> context.pios[index]
    try:
        pio.advance(cycles)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef double _monotonic(void* ctx) noexcept:
    try:
        return time.monotonic()
    except BaseException as error:
        park_error(error)
        return 0.0


def execute_batch(simulator: object, tick_batch: int) -> None:
    """Same semantics as Simulator._execute_batch() (simulator.py) and `_execute_batch.py`'s `execute_batch()`."""
    cdef RP2040 rp2040 = simulator.rp2040
    cdef CortexM0Core core = rp2040.core
    cdef SimulationClock clock = simulator.clock
    cdef unsigned char[::1] stop_flag = simulator._stop_flag
    cdef BatchHost host
    cdef BatchParams params
    cdef int result
    cdef int i
    cdef RPPIO pio

    if rp2040.run_pin_low:
        # RUN held low - the chip is in reset and executes nothing. See _execute_batch.py's own comment here for the full rationale; the
        # two loops must not drift.
        return
    if getattr(type(clock), "tick") is not getattr(_BASE_CLOCK_CLASS, "tick"):
        raise TypeError(
            f"{type(clock).__name__} overrides tick(); the native batch loop ticks the C++ clock directly "
            "(use the pure-Python _execute_batch.execute_batch with it)"
        )
    pios = list(rp2040.pio)
    if len(pios) > 4:
        raise TypeError(f"the native batch loop steps at most 4 PIO blocks, the chip has {len(pios)}")
    for p in pios:
        if not isinstance(p, RPPIO):
            raise TypeError(
                f"the native batch loop steps native PIO blocks; {type(p).__name__} is not one "
                "(use the pure-Python _execute_batch.execute_batch with it)"
            )
    context = _BatchContext(pios)  # keeps the blocks alive for the whole batch (their flag addresses are in `host`)

    host.stopped = <const uint8_t*> &stop_flag[0]
    host.pio_count = len(pios)
    for i in range(len(pios)):
        pio = <RPPIO> pios[i]
        host.pio_stopped[i] = <const int*> &pio.stopped
    host.pio_advance = _pio_advance
    host.monotonic = _monotonic
    host.ctx = <void*> context
    params.tick_batch = tick_batch
    params.cycle_nanos = CYCLE_NANOS
    params.instruction_ceiling = BATCH_INSTRUCTION_CEILING
    params.yield_budget_seconds = BATCH_YIELD_BUDGET_SECONDS
    params.check_interval = TIME_CHECK_INTERVAL

    result = run_batch(core._cpu, clock._clock, host, params)
    if result == kBatchFault:
        raise_if_pending()
        raise RuntimeError("the C++ batch loop faulted without a parked Python error")
