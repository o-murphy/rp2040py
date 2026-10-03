"""Public facade: prefers the native TIMER in `rp2040py.native._timer` (a Cython shell over the C++ block of
`native/core/timer.hpp`, compiled at build time when Cython and a C++ compiler are available - see `setup.py`) when it
is importable, falling back to the plain-Python reference implementation in `_timer.py` otherwise. Every caller imports
`RPTimer` from here - never from `_timer.py` or `rp2040py.native` directly. Mirrors `peripherals/pio.py`.

The two are held to identical behaviour by `tests/test_timer_parity.py` (randomized register sessions) and by
replaying recorded firmware sessions (`tests/utils/mmio_trace.py`); the pure-Python class stays the oracle
(docs/records/0096-cpp-mcu-core.md).
"""

from rp2040py._native_gate import native_disabled

try:
    if native_disabled():
        raise ImportError("RP2040PY_SKIP_CYTHON=1 set, forcing pure-Python fallback")
    from rp2040py.native._timer import RPTimer
except ImportError:
    from rp2040py.peripherals._timer import RPTimer

__all__ = ("RPTimer",)
