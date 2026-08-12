"""Runtime gate for `simulator.py`'s opt-in `clock.tick()` call-volume reduction (simulator
performance side quest, 2026-08-12). `RP2040PY_CLOCK_TICK_BATCH` unset or `"1"` leaves
`Simulator._execute_batch()`'s busy path calling the real `SimulationClock.tick()` once per
instruction, exactly as before; an integer > 1 lets it accumulate that many instructions' worth of
simulated nanoseconds before flushing, refreshed early whenever a scheduled alarm would otherwise
fire late.

Default off deliberately, not just "off until proven safe": alarms (hardware timers,
`peripherals/usb.py`'s SOF/reset/endpoint-completion alarms) fire at an *exact* simulated
nanosecond today, and real firmware can reasonably depend on that precision - batching trades a
small amount of that precision (an alarm can fire up to one batch's worth of nanoseconds late,
though never before its own instant) for fewer `tick()` calls. Confirmed via a real-boot
measurement (not assumed) that alarms are typically tens of thousands of instructions away in
practice (median ~32,000 during a real MicroPython Pico W boot's firmware-download phase), so
batching has real headroom - but it's still a genuine behavior change, opt-in the same way
`RP2040PY_SKIP_CYTHON` is, not a default.
"""

import os


def clock_tick_batch_size() -> int:
    """>=1 - the max number of instructions `Simulator._execute_batch()` may accumulate simulated
    nanoseconds for before calling the real `SimulationClock.tick()` again. `1` (the default,
    whether unset, not an integer, or explicitly `"1"`) means every instruction flushes
    immediately - identical to the pre-2026-08-12 behavior, not just similar."""
    raw = os.environ.get("RP2040PY_CLOCK_TICK_BATCH", "1")
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, value)
