# Type stub for the compiled Cython module: it mirrors the pure-Python reference
# (rp2040py.clock._simulation_clock) exactly, so the types live there (single source of truth) and are re-exported here.
# Lets mypy/IDEs see the native backend's API instead of the ignore_missing_imports fallback.

from rp2040py.clock._simulation_clock import (
    ClockAlarm as ClockAlarm,
)
from rp2040py.clock._simulation_clock import (
    SimulationClock as SimulationClock,
)
