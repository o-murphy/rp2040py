"""Public facade: prefers the native Cython port in `rp2040py.native._simulation_clock` (compiled
at build time when Cython and a C compiler are available - see `setup.py`) when it's importable,
falling back to the plain-Python reference implementation in `_simulation_clock.py` otherwise.
Every caller imports `SimulationClock`/`ClockAlarm` from here - never from `_simulation_clock.py`
or `rp2040py.native` directly. Mirrors `cortex_m0_core.py`/`peripherals/state_machine.py`'s own
facade exactly.
"""

from rp2040py._native_gate import raise_import_error_on_native_disabled

try:
    raise_import_error_on_native_disabled()
    from rp2040py.native._simulation_clock import ClockAlarm, SimulationClock
except ImportError:
    from rp2040py.clock._simulation_clock import ClockAlarm, SimulationClock

__all__ = (
    "ClockAlarm",
    "SimulationClock",
)
