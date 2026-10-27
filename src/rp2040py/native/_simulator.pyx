# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""Native `Simulator._execute_batch()`: the glue between the Python `Simulator` and the chip's C++ `Mcu::run` (`core/mcu.hpp`, which wraps the batch loop of `core/batch.hpp`;
docs/records/0096-cpp-mcu-core.md, Phase 2 step 4c and step 2a). `_execute_batch.py` stays the pure-Python reference loop; the two are held to
identical behaviour by `tests/test_batch_parity.py`, and `execute_batch.py` is the facade that picks between them.

The whole per-instruction loop - decode, execute, tick the clock (firing due alarms), the idle jump to the next alarm, the stop check and
the real-time budget - runs in C++ against the C++ `Cpu`, `Bus` and `Clock` of the chip, and touches no Python object unless something
it drives does. What it still reaches out to, once per iteration at most:

- the stop byte: the chip's own (`Mcu::request_stop`), which `Simulator.stopped` is a view of, so the loop reads the byte itself and a `stop()` from another
  thread (or from an `on_break` callback in the middle of an instruction) is seen at the next iteration, as before;
- the chip's two PIO blocks, which it steps itself (a block that is running costs a C++ call, nothing else); the pure-Python/`Mock` PIO of a mixed setup is refused up front;
- `time.monotonic()`, once every 256 iterations, for the real-time yield budget (the host's to give).

A Python error raised by any of them, or by a peripheral or alarm callback inside the loop, is parked (`_pending.pyx`) and re-raised here
after the loop returns, with the machine left exactly where the exception would have left it.

The loop ticks `clock._clock` (the C++ `Clock`) directly, so a `SimulationClock` subclass that overrides `tick()` in Python would be
bypassed; that is refused up front too (`MockClock` only adds `advance()`).
"""

import time

from libc.stdint cimport uint8_t

from rp2040py.native._batch cimport BatchParams, BatchResult, kBatchFault
from rp2040py.native._clocks cimport RPClocks
from rp2040py.native._pending cimport park_error, raise_if_pending
from rp2040py.native._pio cimport RPPIO
from rp2040py.native._rp2040 cimport RP2040
from rp2040py.native._simulation_clock cimport SimulationClock

# Looked up through Python attribute access on purpose: `SimulationClock.tick` spelled directly would be the C method.
_BASE_CLOCK_CLASS = SimulationClock

# Mirrors simulator.py's own module-level constants exactly - see that file's docstrings for the full rationale (why 256/0.005s, why the
# idle jump must not be weighted by simulated nanoseconds).
cdef double BATCH_YIELD_BUDGET_SECONDS = 0.005
cdef int TIME_CHECK_INTERVAL = 256
cdef long BATCH_INSTRUCTION_CEILING = 1000000
cdef double PACED_IDLE_NANOS = 1_000_000.0  # mirrors _execute_batch.py's _PACED_IDLE_NANOS


cdef double _monotonic(void* ctx) noexcept:
    try:
        return time.monotonic()
    except BaseException as error:
        park_error(error)
        return 0.0


def execute_batch(simulator: object, tick_batch: int) -> None:
    """Same semantics as Simulator._execute_batch() (simulator.py) and `_execute_batch.py`'s `execute_batch()`: the glue between the Python `Simulator` and the chip's C++ `Mcu::run`.
    The stop byte and the "a device waits on the real world" byte are the chip's own (the Simulator's flags are views of them), and the PIO blocks it steps are the chip's: all that is
    left to do per batch is to check that the components are the native ones, point the chip at the simulator's clock, and give it this batch's limits."""
    cdef RP2040 rp2040 = simulator.rp2040
    cdef SimulationClock clock = simulator.clock
    cdef BatchParams params
    cdef BatchResult result
    cdef RPClocks clocks

    if rp2040.run_pin_low:
        # RUN held low - the chip is in reset and executes nothing. See _execute_batch.py's own comment here for the full rationale; the
        # two loops must not drift.
        return
    if getattr(type(clock), "tick") is not getattr(_BASE_CLOCK_CLASS, "tick"):
        raise TypeError(
            f"{type(clock).__name__} overrides tick(); the native batch loop ticks the C++ clock directly "
            "(use the pure-Python _execute_batch.execute_batch with it)"
        )
    if not isinstance(rp2040.clocks, RPClocks):
        raise TypeError(
            f"the native batch loop follows clk_sys through the native clock tree; {type(rp2040.clocks).__name__} is not one "
            "(use the pure-Python _execute_batch.execute_batch with it)"
        )
    clocks = rp2040.clocks
    pios = list(rp2040.pio)
    if len(pios) != 2:
        raise TypeError(f"the native batch loop steps the chip's two PIO blocks, the chip has {len(pios)}")
    for index, p in enumerate(pios):
        if not isinstance(p, RPPIO) or (<RPPIO> p)._block != rp2040._mcu.pio_block(index):
            raise TypeError(
                f"the native batch loop steps the chip's own PIO blocks; {type(p).__name__} is not PIO{index} of this chip "
                "(use the pure-Python _execute_batch.execute_batch with it)"
            )

    rp2040._mcu.use_clock(&clock._clock)  # a clock handed to the chip is the chip's clock; the simulator's is the one this batch ticks
    rp2040._mcu.begin_batch()  # (the tree raises the chip's own flag when clk_sys changes: the batch ends and the next one starts at the new rate)
    rp2040._mcu.set_monotonic(_monotonic)
    params.tick_batch = tick_batch
    params.cycle_nanos = 1e9 / <double> rp2040.clk_sys  # one system clock: 125 MHz until firmware configures the tree
    params.instruction_ceiling = BATCH_INSTRUCTION_CEILING
    params.yield_budget_seconds = BATCH_YIELD_BUDGET_SECONDS
    params.check_interval = TIME_CHECK_INTERVAL
    params.paced_idle_nanos = PACED_IDLE_NANOS
    params.quantum_cycles = rp2040.quantum_cycles  # two cores only: the cycles a core runs ahead of the other between synchronisation points (1: the exact scheduler)

    if rp2040._mcu.run(params, &result) == kBatchFault:
        raise_if_pending()
        raise RuntimeError("the C++ batch loop faulted without a parked Python error")
