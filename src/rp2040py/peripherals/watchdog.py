"""Public facade for WATCHDOG: prefers the native block in `rp2040py.native._watchdog` (a Cython shell over `native/core/watchdog.hpp`) when it is importable, falling back to the plain-Python reference in
`_watchdog.py` otherwise. Every caller imports `RPWatchdog` from here - never from `_watchdog.py` or `rp2040py.native` directly (the pure-Python chip, `_rp2040.py`, is the one deliberate exception).
Mirrors `peripherals/uart.py`; held to identical behaviour by `tests/test_watchdog_diff.py` (docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled
from rp2040py.peripherals._watchdog import (
    COUNT_MASK,
    COUNT_SHIFT,
    CTRL,
    CYCLES_MASK,
    CYCLES_SHIFT,
    ENABLE,
    FORCE,
    LOAD,
    LOAD_MASK,
    LOAD_SHIFT,
    PAUSE_DBG0,
    PAUSE_DBG1,
    PAUSE_JTAG,
    REASON,
    RUNNING,
    SCRATCH0,
    SCRATCH1,
    SCRATCH2,
    SCRATCH3,
    SCRATCH4,
    SCRATCH5,
    SCRATCH6,
    SCRATCH7,
    SCRATCH_REGS,
    TICK,
    TICK_ENABLE,
    TICK_FREQUENCY,
    TIME_MASK,
    TIME_SHIFT,
    TIMER,
    TRIGGER,
)

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._watchdog import RPWatchdog
except ImportError:
    from rp2040py.peripherals._watchdog import RPWatchdog

__all__ = (
    "COUNT_MASK",
    "COUNT_SHIFT",
    "CTRL",
    "CYCLES_MASK",
    "CYCLES_SHIFT",
    "ENABLE",
    "FORCE",
    "LOAD",
    "LOAD_MASK",
    "LOAD_SHIFT",
    "PAUSE_DBG0",
    "PAUSE_DBG1",
    "PAUSE_JTAG",
    "REASON",
    "RUNNING",
    "SCRATCH0",
    "SCRATCH1",
    "SCRATCH2",
    "SCRATCH3",
    "SCRATCH4",
    "SCRATCH5",
    "SCRATCH6",
    "SCRATCH7",
    "SCRATCH_REGS",
    "TICK",
    "TICK_ENABLE",
    "TICK_FREQUENCY",
    "TIMER",
    "TIME_MASK",
    "TIME_SHIFT",
    "TRIGGER",
    "RPWatchdog",
)
